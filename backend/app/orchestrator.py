"""Runs across Greenlight's agents: top level runs, the child runs of helper agents, and what starts them.

Links between runs are chained notes, never extra columns: a top level run gets a "run_meta" note with its
role; a child gets "run_meta" {role, parent_run_id, purpose} and its parent gets "child_run"
{child_run_id, role, purpose}. A run with no run_meta note predates this and is a top level fixer run.

The orchestrator observes every run's pump (app.runs.RunObserver). Its methods only schedule work, so the
event stream is never slowed; triggers are idempotent against the parent's child_run notes.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Coroutine
from typing import Any

from fastapi import FastAPI

from app import facts
from app.agents import AgentRegistry
from app.ledger import Ledger
from app.markers import MARKER
from app.tools import ResolvedToolCall

logger = logging.getLogger(__name__)

NOTE_RUN_META = "run_meta"
NOTE_CHILD_RUN = "child_run"
NOTE_GUARD = "guard"

PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
FENCED = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)

# Child runs never merge: check_merge only lets ship runs merge.
CHILD_MODE = "pr_only"
# A prover that never finishes must not hold up the next one forever.
CHILD_WAIT_SECONDS = 45 * 60
RECEIPT_POLL_SECONDS = 5.0
RECEIPT_ATTEMPTS = 24
ERROR_STATUSES = {"error", "cancelled", "resume_failed"}
WAITING_STATUSES = {"running", "awaiting_approval", "awaiting_input"}

# Appended to every fixer task so the vulnerabilities it finds reach Greenlight as facts.
FACT_INSTRUCTION = (
    "As soon as you know the vulnerable packages (from npm audit or OSV), and before upgrading anything, print "
    "this line in your reply, on its own line, once per advisory, with the advisory id (e.g. its GHSA id):\n"
    f'{MARKER}{{"kind": "vulnerability", "advisory": "<advisory id>", "package": "<package>", '
    '"current": "<current version>", "fixed": "<first fixed version>", "severity": "<severity>"}'
)
# The Daytona sandbox image ships Python only, with no xz for .tar.xz archives and no git credentials.
SANDBOX_NOTE = (
    "The sandbox may not have Node.js. If node or npm is missing, install Node.js 22 yourself from the "
    "linux x64 .tar.gz archive on https://nodejs.org/dist/latest-v22.x/ (xz is not available), symlink its "
    "node, npm and npx into /usr/local/bin, and continue without asking. The sandbox has no GitHub "
    "credentials: create the branch, push files and open the PR with the GitHub tools, never git push."
)
FIXER_INSTRUCTIONS = f"{SANDBOX_NOTE}\n\n{FACT_INSTRUCTION}"

_current: "Orchestrator | None" = None


def bind(orchestrator: "Orchestrator | None") -> None:
    global _current
    _current = orchestrator


def current() -> "Orchestrator | None":
    return _current


class ResultParseError(ValueError):
    pass


def parse_result(text: str) -> Any:
    """The last fenced json block (labelled json, or unlabelled) in text."""
    blocks = [body for label, body in FENCED.findall(text or "") if label.lower() in ("json", "")]
    if not blocks:
        raise ResultParseError("no fenced json block")
    try:
        return json.loads(blocks[-1].strip())
    except json.JSONDecodeError as exc:
        raise ResultParseError(str(exc)) from exc


def result_fact(role: str, text: str) -> dict[str, Any]:
    kind = f"{role}_result"
    try:
        parsed = parse_result(text)
    except ResultParseError:
        return {"kind": kind, "parse_error": True, "raw": text}
    if isinstance(parsed, dict):
        return {**parsed, "kind": kind}
    return {"kind": kind, "value": parsed}


def advisory_ids(vulnerabilities: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for fact in vulnerabilities:
        advisory = fact.get("advisory") or fact.get("advisory_id") or fact.get("id")
        if isinstance(advisory, str) and advisory.strip() and advisory.strip() not in ids:
            ids.append(advisory.strip())
    return ids


def _content_text(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content)


class Orchestrator:
    def __init__(self, app: FastAPI, registry: AgentRegistry) -> None:
        self._app = app
        self.registry = registry
        # run id -> (role, parent run id)
        self._meta: dict[str, tuple[str, str | None]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._pr_urls: dict[str, str] = {}
        self._guarded: set[str] = set()

    # Plumbing

    @property
    def _ledger(self) -> Ledger:
        return self._app.state.ledger

    def _lock(self, run_id: str, name: str) -> asyncio.Lock:
        return self._locks.setdefault((run_id, name), asyncio.Lock())

    def _spawn(self, coro: Coroutine[Any, Any, Any], what: str) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(finished: asyncio.Task[Any]) -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.error("%s failed", what, exc_info=finished.exception())

        task.add_done_callback(done)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def meta(self, run_id: str) -> tuple[str, str | None]:
        """(role, parent run id) from the run's run_meta note; a run without one is a top level fixer run."""
        if run_id not in self._meta:
            notes = await self._ledger.get_notes(run_id, kind=NOTE_RUN_META)
            payload = notes[0]["payload"] if notes else {}
            self._meta[run_id] = (payload.get("role") or "fixer", payload.get("parent_run_id"))
        return self._meta[run_id]

    # Starting runs

    async def start_run(
        self,
        role: str,
        repo: str,
        mode: str,
        message: str,
        *,
        via_fork: bool = False,
        parent_run_id: str | None = None,
        purpose: str | None = None,
    ) -> str:
        """Start a TrueForge session and turn with the role's agent, record the run and its links, and pump it."""
        state = self._app.state
        trueforge = state.trueforge_client
        session_id = await trueforge.create_session(await self.registry.agent_id(role, trueforge))
        turn = await trueforge.start_turn(session_id, message)

        run_id = str(uuid.uuid4())
        await self._ledger.create_run(
            run_id=run_id, repo=repo, mode=mode, via_fork=via_fork, session_id=session_id, turn_id=turn.turn_id
        )
        self._meta[run_id] = (role, parent_run_id)
        meta: dict[str, Any] = {"role": role}
        if parent_run_id is not None:
            meta.update(parent_run_id=parent_run_id, purpose=purpose)
        await self._ledger.append_note(run_id, NOTE_RUN_META, meta)
        if parent_run_id is not None:
            await self._ledger.append_note(
                parent_run_id, NOTE_CHILD_RUN, {"child_run_id": run_id, "role": role, "purpose": purpose}
            )
        state.run_manager.start(run_id, session_id, turn.turn_id)
        logger.info("Started %s run %s%s", role, run_id, f" for {parent_run_id}" if parent_run_id else "")
        return run_id

    async def start_child_run(self, parent_run_id: str, role: str, message: str, purpose: str) -> str:
        parent = await self._ledger.get_run(parent_run_id)
        if parent is None:
            raise LookupError(f"Run {parent_run_id} not found")
        return await self.start_run(
            role, parent["repo"], CHILD_MODE, message, parent_run_id=parent_run_id, purpose=purpose
        )

    # Reading runs

    async def list_top_level(self, limit: int) -> list[dict[str, Any]]:
        cursor = await self._ledger.db.execute(
            "SELECT r.id, r.repo, r.mode, r.via_fork, r.status, r.created_at,"
            " (SELECT json_extract(n.payload_json, '$.role') FROM notes n"
            "   WHERE n.run_id = r.id AND n.kind = ? ORDER BY n.id LIMIT 1) AS role"
            " FROM runs r WHERE r.id NOT IN (SELECT run_id FROM notes WHERE kind = ?"
            "   AND json_extract(payload_json, '$.parent_run_id') IS NOT NULL)"
            " ORDER BY r.created_at DESC LIMIT ?",
            (NOTE_RUN_META, NOTE_RUN_META, limit),
        )
        return [
            {
                "id": row["id"],
                "repo": row["repo"],
                "mode": row["mode"],
                "via_fork": bool(row["via_fork"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "role": row["role"] or "fixer",
            }
            for row in await cursor.fetchall()
        ]

    async def result(self, run_id: str, role: str) -> dict[str, Any] | None:
        """The run's latest parsed result, without its fact kind."""
        await facts.flush()
        results = await facts.get_facts(run_id, kind=f"{role}_result")
        if not results:
            return None
        return {key: value for key, value in results[-1].items() if key != "kind"}

    async def children(self, parent_run_id: str) -> list[dict[str, Any]]:
        children = []
        for note in await self._ledger.get_notes(parent_run_id, kind=NOTE_CHILD_RUN):
            link = note["payload"]
            run = await self._ledger.get_run(link["child_run_id"])
            children.append(
                {
                    "run_id": link["child_run_id"],
                    "role": link["role"],
                    "purpose": link.get("purpose"),
                    "status": run["status"] if run else "missing",
                    "created_at": run["created_at"] if run else note["created_at"],
                    "result": await self.result(link["child_run_id"], link["role"]),
                }
            )
        return children

    async def _has_child(self, parent_run_id: str, role: str, purpose: str) -> bool:
        notes = await self._ledger.get_notes(parent_run_id, kind=NOTE_CHILD_RUN)
        return any(n["payload"].get("role") == role and n["payload"].get("purpose") == purpose for n in notes)

    async def _wait_for(self, run_id: str) -> None:
        task = self._app.state.run_manager.pump_task(run_id)
        if task is not None:
            await asyncio.wait({task}, timeout=CHILD_WAIT_SECONDS)

    # RunObserver: called from the pump, so each only schedules work.

    def tool_calls(self, run_id: str, session_id: str) -> None:
        known = self._meta.get(run_id)
        if run_id in self._guarded or (known is not None and known[0] != "auditor"):
            return
        self._guarded.add(run_id)
        self._spawn(self._guard_auditor(run_id, session_id), f"Auditor guard for {run_id}")

    def tool_response(self, run_id: str, call: ResolvedToolCall | None, event: dict[str, Any]) -> None:
        if call is None or call.name != "create_pull_request":
            return
        match = PR_URL.search(_content_text(event.get("content")))
        if match is None:
            return
        self._pr_urls[run_id] = match.group(0)
        self._spawn(self._start_provers(run_id, match.group(0)), f"Prover trigger for {run_id}")

    def turn_done(self, run_id: str, status: str, final_text: str) -> None:
        self._spawn(self._after_turn(run_id, status, final_text), f"Turn end for {run_id}")

    # Triggers

    async def _guard_auditor(self, run_id: str, session_id: str) -> None:
        role, _ = await self.meta(run_id)
        if role != "auditor":
            self._guarded.discard(run_id)
            return
        # The auditor only reads what it is given; any tool call could write, so stop the session.
        logger.warning("Auditor run %s called a tool; cancelling its session", run_id)
        try:
            await self._app.state.trueforge_client.cancel(session_id)
        finally:
            await self._ledger.append_note(
                run_id, NOTE_GUARD, {"reason": "The auditor called a tool, so Greenlight stopped it: the auditor never writes"}
            )

    async def _after_turn(self, run_id: str, status: str, final_text: str) -> None:
        role, parent = await self.meta(run_id)
        if role != "fixer":
            await facts.add_fact(run_id, result_fact(role, final_text))
            return
        if parent is not None:
            return
        if run_id in self._pr_urls:
            # Vulnerability facts may have arrived after the PR; the trigger skips provers already started.
            self._spawn(self._start_provers(run_id, self._pr_urls[run_id]), f"Prover trigger for {run_id}")
        if status == "done":
            self._spawn(self._auto_receipt(run_id), f"Receipt for {run_id}")

    async def _start_provers(self, run_id: str, pr_url: str) -> None:
        role, parent = await self.meta(run_id)
        if role != "fixer" or parent is not None:
            return
        async with self._lock(run_id, "prover"):
            await facts.flush()
            advisories = advisory_ids(await facts.get_facts(run_id, kind="vulnerability"))
            if not advisories:
                logger.info("PR %s opened by run %s, but no vulnerability facts yet; no prover started", pr_url, run_id)
                return
            # One at a time: each prover gets its own sandbox, and they finish in a predictable order.
            for advisory in advisories:
                purpose = f"verify {pr_url} {advisory}"
                if await self._has_child(run_id, "prover", purpose):
                    continue
                child = await self.start_child_run(run_id, "prover", f"Verify {pr_url} for advisory {advisory}", purpose)
                await self._wait_for(child)

    async def _auto_receipt(self, run_id: str) -> None:
        """Settle the receipt without waiting for a browser to ask for it."""
        from app.routes.receipt import compute_receipt

        for _ in range(RECEIPT_ATTEMPTS):
            status_code, _ = await compute_receipt(self._app, run_id)
            if status_code != 202:
                return
            await asyncio.sleep(RECEIPT_POLL_SECONDS)

    def receipt_stored(self, run_id: str, receipt: dict[str, Any]) -> None:
        self._spawn(self._start_receipt_child(run_id, receipt), f"Receipt agent for {run_id}")

    async def _start_receipt_child(self, run_id: str, receipt: dict[str, Any]) -> None:
        _, parent = await self.meta(run_id)
        if parent is not None:
            return
        async with self._lock(run_id, "receipt"):
            if await self._has_child(run_id, "receipt", "receipt"):
                return
            await self.start_child_run(run_id, "receipt", f"Receipt data:\n{json.dumps(receipt, indent=2)}", "receipt")

    async def start_auditor(self, run_id: str, report: dict[str, Any]) -> str:
        return await self.start_child_run(
            run_id, "auditor", f"Audit ledger export:\n{json.dumps(report, indent=2)}", "audit report"
        )

    # Independent verification

    async def verification(self, run_id: str) -> dict[str, Any]:
        """Where the prover children of a run stand. Only a proof that still fails after the fix blocks a merge."""
        provers = []
        for child in await self.children(run_id):
            if child["role"] != "prover":
                continue
            result = child["result"]
            if isinstance(result, dict) and result.get("after") == "fail":
                state = "still_exploitable"
            elif result is None and child["status"] in WAITING_STATUSES:
                state = "running"
            elif result is None or result.get("parse_error") or child["status"] in ERROR_STATUSES or "error" in (
                result.get("before"),
                result.get("after"),
            ):
                state = "error"
            elif result.get("before") == "fail" and result.get("after") == "pass":
                state = "proven"
            else:
                state = "inconclusive"
            advisory = (result or {}).get("advisory") or (child["purpose"] or "").rsplit(" ", 1)[-1] or None
            provers.append(
                {"run_id": child["run_id"], "advisory": advisory, "run_status": child["status"], "state": state, "result": result}
            )

        states = {p["state"] for p in provers}
        overall = next(
            (s for s in ("still_exploitable", "running", "error", "inconclusive", "proven") if s in states), "none"
        )
        return {"status": overall, "provers": provers}
