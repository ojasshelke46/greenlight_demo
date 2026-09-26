import json
from datetime import datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response

from app.auth import require_api_key
from app.chain import PRE_FLIGHT_RECORDER, export_document, load_run
from app.config import get_settings
from app.event_types import NOTE_RECEIPT
from app.tools import merge_tool_call_deltas, resolve_tool_call
from app.trueforge import TrueForgeError

router = APIRouter(tags=["audit"], dependencies=[Depends(require_api_key)])

SUMMARY_LENGTH = 80


async def _snapshot(run_id: str, request: Request) -> dict[str, Any]:
    await request.app.state.ledger.flush()  # queued stream writes land before the snapshot is taken
    snapshot = await load_run(run_id, db_path=get_settings().ledger_path)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return snapshot


@router.get("/runs/{run_id}/audit/verify")
async def verify(run_id: str, request: Request) -> dict[str, Any]:
    return (await _snapshot(run_id, request))["verify"]


@router.get("/runs/{run_id}/audit/export")
async def export(run_id: str, request: Request, format: Literal["md", "json"] = Query("json")) -> Response:
    snapshot = await _snapshot(run_id, request)
    headers = {"Content-Disposition": f'attachment; filename="greenlight_{run_id}.{format}"'}
    if format == "json":
        return JSONResponse(export_document(snapshot), headers=headers)
    return Response(render_markdown(snapshot), media_type="text/markdown; charset=utf-8", headers=headers)


def auditor_input(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The export plus the server's verify result, for the auditor agent.

    Chain entries are listed without their source rows, and events are folded into the tool call timeline:
    a run streams thousands of model.message.delta events, far more than a model should read. The hashes and
    chain_verified are the server's; the auditor never checks or changes anything itself.
    """
    document = export_document(snapshot)
    document["entries"] = [
        {key: entry[key] for key in ("idx", "kind", "ref_id", "prev_hash", "entry_hash", "created_at")}
        for entry in snapshot["entries"]
    ]
    verify = snapshot["verify"]
    return {
        **document,
        "chain_verified": verify["ok"],
        "verify": verify,
        "tool_calls": _tool_calls(snapshot["events"]),
        "approvals": [
            {
                "tool_name": approval["tool_name"],
                "arguments": json.loads(approval["arguments_json"]) if approval["arguments_json"] else None,
                "decision": approval["decision"],
                "approver": approval["approver"],
                "decided_at": approval["decided_at"],
                "result": approval["result"],
            }
            for approval in snapshot["approvals"]
        ],
        "notes": [
            {"kind": note["kind"], "payload": json.loads(note["payload_json"]), "created_at": note["created_at"]}
            for note in snapshot["notes"]
        ],
    }


@router.post("/runs/{run_id}/audit/report", status_code=status.HTTP_201_CREATED)
async def audit_report(run_id: str, request: Request) -> dict[str, str]:
    """Start the auditor agent on this run's export and verify result. Returns the auditor's child run id."""
    report = auditor_input(await _snapshot(run_id, request))
    try:
        child_run_id = await request.app.state.orchestrator.start_auditor(run_id, report)
    except (TrueForgeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not start the auditor: {exc}") from exc
    return {"run_id": child_run_id}


# Markdown report


def _time(iso: str | None, with_date: bool = False) -> str:
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return moment.strftime("%Y %b %d, %H:%M:%S UTC" if with_date else "%H:%M:%S")


def _cell(text: Any) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_LENGTH else text[: SUMMARY_LENGTH - 1] + "…"


def _summarise_arguments(name: str | None, arguments: Any) -> str:
    if isinstance(arguments, dict):
        if name == "exec" and isinstance(arguments.get("command"), str):
            return _shorten(arguments["command"])
        scalars = [f"{key}={value}" for key, value in arguments.items() if isinstance(value, (str, int, float, bool))]
        return _shorten(", ".join(scalars)) if scalars else _shorten(json.dumps(arguments, ensure_ascii=False))
    if arguments in (None, ""):
        return ""
    return _shorten(arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False))


def _response_status(content: Any) -> str:
    try:
        body = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError:
        body = None
    if isinstance(body, dict):
        if isinstance(body.get("error"), list) or body.get("success") is False:
            return "failed"
        response = body.get("response")
        if isinstance(response, dict) and isinstance(response.get("exitCode"), int) and response["exitCode"] != 0:
            return f"exit {response['exitCode']}"
    return "ok"


def _tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool calls in the order the agent made them, each with the status of its response."""
    payloads = [(event, json.loads(event["payload_json"])) for event in events]
    calls: dict[str, list[dict[str, Any]]] = {}
    message_time: dict[str, str] = {}
    responses: dict[str, str] = {}
    awaiting_approval: set[str] = set()

    for event, payload in payloads:
        kind = event["type"]
        if kind == "model.message":
            message_time[payload["id"]] = event["received_at"]
            if payload.get("tool_calls"):
                calls[payload["id"]] = [json.loads(json.dumps(call)) for call in payload["tool_calls"]]
        elif kind == "model.message.delta" and payload.get("tool_calls"):
            merge_tool_call_deltas(calls.setdefault(payload["id"], []), payload["tool_calls"])
        elif kind == "tool.response" and payload.get("tool_call_id"):
            responses[payload["tool_call_id"]] = _response_status(payload.get("content"))
        elif kind == "tool.approval_required":
            awaiting_approval.update(ref.get("id") for ref in payload.get("tool_calls") or [])

    rows = []
    for message_id, message_calls in calls.items():
        for call in message_calls:
            resolved = resolve_tool_call(call)
            tool = f"{resolved.server}.{resolved.name}" if resolved.server else (resolved.name or "tool")
            if resolved.tool_call_id in responses:
                result = responses[resolved.tool_call_id]
            elif resolved.tool_call_id in awaiting_approval:
                result = "waiting for approval"
            else:
                result = "no response"
            rows.append(
                {
                    "time": message_time.get(message_id),
                    "tool": tool,
                    "arguments": _summarise_arguments(resolved.name, resolved.arguments),
                    "result": result,
                }
            )
    return rows


def _verification_line(result: dict[str, Any]) -> str:
    if result["ok"]:
        return "Verified" + (f" ({result['reason']})" if result["reason"] else "")
    if result["reason"] == PRE_FLIGHT_RECORDER:
        return "Not verifiable: created before flight recorder"
    where = f" at entry {result['first_broken_idx']}" if result["first_broken_idx"] is not None else ""
    return f"Broken{where}: {result['reason']}"


RECEIPT_LABELS = {
    "cost_usd": "Cost (USD)",
    "cost_inr": "Cost (INR)",
    "duration_seconds": "Duration (seconds)",
    "trace_url": "Trace",
}


def _label(key: str) -> str:
    return RECEIPT_LABELS.get(key, key.replace("_", " ").capitalize())


def render_markdown(snapshot: dict[str, Any]) -> str:
    run, result, head = snapshot["run"], snapshot["verify"], snapshot["head"]
    repo = run["repo"]
    lines = [
        f"# Greenlight flight record: {repo}",
        "",
        f"- Repo: [{repo}](https://github.com/{repo})",
        f"- Mode: {'Ship it' if run['mode'] == 'ship' else 'PR only'}",
        f"- Access: {'fork' if run['via_fork'] else 'direct'}",
        f"- Created: {_time(run['created_at'], with_date=True)}",
        f"- Verification: {_verification_line(result)}",
        f"- Entries: {result['entries']}",
        f"- Signature: {'signed' if head and head['signed'] else 'unsigned'}",
        "",
        "## Timeline",
        "",
    ]

    calls = _tool_calls(snapshot["events"])
    if calls:
        lines += ["| Time | Tool | Arguments | Result |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {_time(call['time'])} | {_cell(call['tool'])} | {_cell(call['arguments'])} | {_cell(call['result'])} |"
            for call in calls
        ]
    else:
        lines.append("No tool calls were recorded.")

    lines += ["", "## Approvals", ""]
    if snapshot["approvals"]:
        lines += ["| Tool | PR | Decision | Approver | Time | Validation |", "| --- | --- | --- | --- | --- | --- |"]
        for approval in snapshot["approvals"]:
            arguments = json.loads(approval["arguments_json"]) if approval["arguments_json"] else None
            number = arguments.get("pullNumber") if isinstance(arguments, dict) else None
            pr = f"[#{number}](https://github.com/{repo}/pull/{number})" if number is not None else "none"
            lines.append(
                f"| {_cell(approval['tool_name'])} | {pr} | {_cell(approval['decision'])} | {_cell(approval['approver'])}"
                f" | {_time(approval['decided_at'], with_date=True)} | {_cell(approval['result'])} |"
            )
    else:
        lines.append("No approvals were requested.")

    receipts = [note for note in snapshot["notes"] if note["kind"] == NOTE_RECEIPT]
    if receipts:
        receipt = json.loads(receipts[-1]["payload_json"])
        lines += ["", "## Receipt", ""]
        if isinstance(receipt, dict):
            lines += [f"- {_label(key)}: {'unavailable' if value is None else value}" for key, value in receipt.items()]
        else:
            lines.append(f"- {receipt}")

    last_hash = snapshot["entries"][-1]["entry_hash"] if snapshot["entries"] else None
    head_mac = head["head_hmac"] if head else None
    lines += [
        "",
        "---",
        "",
        f"Final entry hash: `{last_hash}`" if last_hash else "Final entry hash: none",
        "",
        f"Head HMAC: `{head_mac}`" if head_mac else "Head HMAC: none (unsigned)",
        "",
    ]
    return "\n".join(lines)
