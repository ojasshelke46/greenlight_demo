"""The cost receipt for a finished run.

Model calls, tokens and cost come from the gateway only. When the gateway has nothing for the run, the
receipt counts model.message events in the ledger instead and leaves tokens and cost null: a cost is
never estimated.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.gateway import CallUsage, RunWindow, fetch_run_calls
from app.ledger import Ledger
from app.tools import merge_tool_call_deltas, resolve_tool_call

AUDIT_COMMAND = re.compile(r"\bnpm audit\b(?! fix)|osv\.dev")
ADVISORY_ID = re.compile(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", re.IGNORECASE)


class Receipt(BaseModel):
    advisories_fixed: int
    model_calls: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    cost_inr: float | None
    duration_seconds: float | None
    # gateway_notes.md documents no URL format for a trace or a filtered view, so there is none to build.
    trace_url: str | None
    source: Literal["gateway", "events"]


@dataclass
class _LedgerFacts:
    window: RunWindow
    model_call_events: int
    advisories_fixed: int
    duration_seconds: float | None


def _parse_json(text: Any) -> Any:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _exec_output(content: Any) -> str | None:
    """stdout of a sandbox exec tool response; None when the command did not run."""
    body = _parse_json(content)
    if isinstance(body, dict):
        if isinstance(body.get("error"), list):
            return None
        response = body.get("response")
        if isinstance(response, dict):
            result = response.get("result")
            return result if isinstance(result, str) else ""
    return content if isinstance(content, str) else None


def _advisories_fixed(events: list[dict[str, Any]]) -> int:
    """Advisories in the first npm audit that a later npm audit in the same run no longer reports."""
    calls: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        payload = event["payload"]
        if event["type"] == "model.message" and payload.get("tool_calls"):
            calls[payload["id"]] = [json.loads(json.dumps(call)) for call in payload["tool_calls"]]
        elif event["type"] == "model.message.delta" and payload.get("tool_calls"):
            merge_tool_call_deltas(calls.setdefault(payload["id"], []), payload["tool_calls"])

    audit_call_ids: set[str] = set()
    for message_calls in calls.values():
        for call in message_calls:
            resolved = resolve_tool_call(call)
            command = resolved.arguments.get("command") if isinstance(resolved.arguments, dict) else None
            if resolved.server is None and resolved.name == "exec" and isinstance(command, str) and AUDIT_COMMAND.search(command):
                audit_call_ids.add(resolved.tool_call_id)

    audits: list[set[str]] = []
    for event in events:
        payload = event["payload"]
        if event["type"] == "tool.response" and payload.get("tool_call_id") in audit_call_ids:
            output = _exec_output(payload.get("content"))
            if output is not None:
                audits.append({advisory.upper() for advisory in ADVISORY_ID.findall(output)})

    if len(audits) < 2:
        return 0
    return len(audits[0] - audits[-1])


def _usage(payload: dict[str, Any]) -> CallUsage | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    return CallUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _active_seconds(events: list[dict[str, Any]], turns: list[tuple[str, int]]) -> float | None:
    """Time the agent was working: each turn from its start to its last event, summed. A run continued later
    is not charged for the time it sat finished between turns."""
    if not events:
        return None
    total = 0.0
    for index, (started_at, base) in enumerate(turns):
        end = turns[index + 1][1] if index + 1 < len(turns) else None
        mine = [e for e in events if e["sequence"] > base and (end is None or e["sequence"] <= end)]
        if mine:
            span = datetime.fromisoformat(mine[-1]["received_at"]) - datetime.fromisoformat(started_at)
            total += max(0.0, span.total_seconds())
    return round(total, 1)


def _ledger_facts(trail: dict[str, Any], turns: list[tuple[str, int]] | None = None) -> _LedgerFacts:
    events = trail["events"]
    created = datetime.fromisoformat(trail["run"]["created_at"])
    last = datetime.fromisoformat(events[-1]["received_at"]) if events else None

    model_messages = [event["payload"] for event in events if event["type"] == "model.message"]
    usages = [usage for usage in map(_usage, model_messages) if usage is not None]
    # A call without reported usage cannot be matched, so the window then holds no provable set of calls.
    calls = usages if len(usages) == len(model_messages) else []

    return _LedgerFacts(
        window=RunWindow(start=created, end=last or created, calls=calls),
        model_call_events=len(model_messages),
        advisories_fixed=_advisories_fixed(events),
        duration_seconds=_active_seconds(events, turns) if turns else (round((last - created).total_seconds(), 1) if last else None),
    )


async def build_receipt(run_id: str, *, ledger: Ledger, http: httpx.AsyncClient, settings: Settings) -> Receipt | None:
    """None when the run does not exist."""
    trail = await ledger.audit_trail(run_id)
    if trail is None:
        return None
    cursor = await ledger.db.execute(
        "SELECT created_at, base_sequence FROM turns WHERE run_id = ? ORDER BY base_sequence, created_at", (run_id,)
    )
    turns = [(row["created_at"], row["base_sequence"]) for row in await cursor.fetchall()]
    facts = _ledger_facts(trail, turns)
    gateway_calls = await fetch_run_calls(facts.window, http, settings)

    common = {
        "advisories_fixed": facts.advisories_fixed,
        "duration_seconds": facts.duration_seconds,
        "trace_url": None,
    }
    if not gateway_calls:
        return Receipt(
            **common,
            model_calls=facts.model_call_events,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            cost_inr=None,
            source="events",
        )

    costs = [call.cost_usd for call in gateway_calls]
    cost_usd = round(sum(costs), 6) if all(cost is not None for cost in costs) else None
    return Receipt(
        **common,
        model_calls=len(gateway_calls),
        input_tokens=sum(call.input_tokens for call in gateway_calls),
        output_tokens=sum(call.output_tokens for call in gateway_calls),
        cost_usd=cost_usd,
        cost_inr=round(cost_usd * settings.inr_per_usd, 2) if cost_usd is not None else None,
        source="gateway",
    )
