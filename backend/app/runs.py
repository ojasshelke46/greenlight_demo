import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.ledger import Ledger, utc_now
from app.trueforge import TurnEvent, TurnHandle, TurnStreamGone

logger = logging.getLogger(__name__)

PUMP_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
ACTIVE_STATUSES = {"running", "awaiting_approval"}


class TrueForge(Protocol):
    async def get_agent_id(self, name: str | None = None) -> str: ...
    async def create_session(self, agent_id: str) -> str: ...
    async def start_turn(self, session_id: str, message: str) -> TurnHandle: ...
    def stream_turn(
        self, session_id: str, turn_id: str, after_sequence: int | None = None
    ) -> AsyncIterator[TurnEvent]: ...


class LiveRun:
    """Events seen by the pump for one run, plus the SSE clients waiting on new ones."""

    def __init__(self) -> None:
        self.events: list[tuple[int, str]] = []
        self.last_sequence = 0
        self.finished = False
        self.closed = False
        self._subscribers: set[asyncio.Queue[tuple[int, str] | None]] = set()

    def publish(self, sequence: int, data: str) -> None:
        self.events.append((sequence, data))
        self.last_sequence = sequence
        for queue in self._subscribers:
            queue.put_nowait((sequence, data))

    def close(self) -> None:
        self.closed = True
        for queue in self._subscribers:
            queue.put_nowait(None)

    def subscribe(self) -> asyncio.Queue[tuple[int, str] | None]:
        queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        if self.closed:
            queue.put_nowait(None)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[int, str] | None]) -> None:
        self._subscribers.discard(queue)


def _pending_action(turn_id: str, event: dict[str, Any], tool_calls: dict[str, dict[str, Any]]) -> dict[str, Any]:
    calls = []
    for ref in event.get("tool_calls", []):
        call = tool_calls.get(ref["id"], {})
        function = call.get("function", {})
        raw_arguments = function.get("arguments")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else None
        except json.JSONDecodeError:
            arguments = raw_arguments
        calls.append({"tool_call_id": ref["id"], "tool_name": function.get("name"), "arguments": arguments})

    # turn_id + thread_id + tool_call ids are what TrueForgeClient.resume_with_approval needs.
    return {
        "turn_id": turn_id,
        "event_id": event.get("id"),
        "thread_id": event.get("thread_id"),
        "created_at": event.get("created_at"),
        "tool_calls": calls,
    }


class RunManager:
    def __init__(self, ledger: Ledger, trueforge: TrueForge) -> None:
        self._ledger = ledger
        self._trueforge = trueforge
        self._live: dict[str, LiveRun] = {}
        self._pumps: dict[str, asyncio.Task[None]] = {}

    async def close(self) -> None:
        for task in self._pumps.values():
            task.cancel()
        await asyncio.gather(*self._pumps.values(), return_exceptions=True)

    def start(self, run_id: str, session_id: str, turn_id: str, after_sequence: int = 0) -> None:
        live = LiveRun()
        live.last_sequence = after_sequence
        self._live[run_id] = live
        task = asyncio.create_task(self._pump(run_id, session_id, turn_id, live))
        self._pumps[run_id] = task
        task.add_done_callback(lambda _: self._pumps.pop(run_id, None))

    async def ensure_pump(self, run: dict[str, Any]) -> None:
        """Resume streaming from TrueForge for an active run with no pump, e.g. after a backend restart."""
        run_id = run["id"]
        if run["status"] not in ACTIVE_STATUSES or run_id in self._pumps:
            return
        live = self._live.get(run_id)
        if (live is not None and live.finished) or await self._ledger.has_event(run_id, "turn.done"):
            return
        self.start(run_id, run["session_id"], run["turn_id"], await self._ledger.last_sequence(run_id))

    async def _pump(self, run_id: str, session_id: str, turn_id: str, live: LiveRun) -> None:
        tool_calls: dict[str, dict[str, Any]] = {}
        pending = False
        attempt = 0
        try:
            while True:
                try:
                    stream = self._trueforge.stream_turn(session_id, turn_id, live.last_sequence or None)
                    async for turn_event in stream:
                        sequence = turn_event.sequence
                        if sequence is None or sequence <= live.last_sequence:
                            continue
                        attempt = 0
                        received_at = utc_now()
                        event, event_type = turn_event.data, turn_event.type
                        data = json.dumps(event)
                        live.publish(sequence, data)
                        self._ledger.append_event(run_id, sequence, event_type, data, received_at)

                        if event_type == "model.message":
                            for call in event.get("tool_calls") or []:
                                tool_calls[call["id"]] = call
                        elif event_type == "tool.approval_required":
                            pending = True
                            self._ledger.set_pending_action(run_id, _pending_action(turn_id, event, tool_calls))
                        elif event_type == "turn.done":
                            live.finished = True
                            state = (event.get("state") or {}).get("status", "done")
                            if not (pending and state == "done"):
                                self._ledger.set_status(run_id, state)
                            return
                    return
                except asyncio.CancelledError:
                    raise
                except TurnStreamGone:
                    logger.warning("TrueForge stream for run %s is gone; serving the ledger only", run_id)
                    return
                except Exception:
                    if attempt >= len(PUMP_RETRY_DELAYS):
                        logger.exception("Giving up on TrueForge stream for run %s", run_id)
                        return
                    logger.warning("TrueForge stream for run %s dropped, retrying", run_id, exc_info=True)
                    await asyncio.sleep(PUMP_RETRY_DELAYS[attempt])
                    attempt += 1
        finally:
            live.close()

    async def stream(self, run_id: str, after: int) -> AsyncIterator[tuple[int, str]]:
        """Ledger replay, then the in memory buffer, then live events; deduplicated by sequence."""
        live = self._live.get(run_id)
        queue = live.subscribe() if live is not None else None
        last = after
        try:
            for sequence, data in await self._ledger.events_after(run_id, after):
                yield sequence, data
                last = sequence

            if live is None or queue is None:
                return

            for sequence, data in list(live.events):
                if sequence > last:
                    yield sequence, data
                    last = sequence

            while (item := await queue.get()) is not None:
                sequence, data = item
                if sequence > last:
                    yield sequence, data
                    last = sequence
        finally:
            if live is not None and queue is not None:
                live.unsubscribe(queue)
