import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app import facts
from app.ledger import Ledger, utc_now
from app.log import run_id_var
from app.markers import FactStream, message_text
from app.tools import ResolvedToolCall, merge_tool_call_deltas, resolve_tool_call
from app.trueforge import TurnEvent, TurnHandle, TurnStreamGone

logger = logging.getLogger(__name__)

PUMP_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
ACTIVE_STATUSES = {"running", "awaiting_approval"}


class TrueForge(Protocol):
    async def get_agent_id(self, name: str | None = None) -> str: ...
    async def create_session(self, agent_id: str) -> str: ...
    async def start_turn(self, session_id: str, message: str) -> TurnHandle: ...
    async def cancel(self, session_id: str) -> None: ...
    def stream_turn(
        self, session_id: str, turn_id: str, after_sequence: int | None = None
    ) -> AsyncIterator[TurnEvent]: ...


class RunObserver(Protocol):
    """Told about a run's stream as the pump sees it. Each method must return at once and never raise."""

    def tool_calls(self, run_id: str, session_id: str) -> None: ...
    def tool_response(self, run_id: str, call: ResolvedToolCall | None, event: dict[str, Any]) -> None: ...
    def turn_done(self, run_id: str, status: str, final_text: str) -> None: ...


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


def _find_call(messages: dict[str, list[dict[str, Any]]], tool_call_id: Any) -> ResolvedToolCall | None:
    for calls in messages.values():
        for call in calls:
            if call.get("id") == tool_call_id:
                return resolve_tool_call(call)
    return None


def _final_text(event: dict[str, Any], streamed: str) -> str:
    """The agent's final message: turn.done's output when it carries one, else the last streamed message."""
    output = (event.get("state") or {}).get("output")
    if isinstance(output, dict):
        output = output.get("content")
    return message_text(output) or streamed


def _pending_action(turn_id: str, event: dict[str, Any], messages: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    calls = []
    for ref in event.get("tool_calls", []):
        source = messages.get(ref.get("source_event_id"), [])
        call = next((c for c in source if c.get("id") == ref["id"]), {"id": ref["id"]})
        resolved = resolve_tool_call(call)
        calls.append(
            {
                "tool_call_id": ref["id"],
                "source_event_id": ref.get("source_event_id"),
                "server": resolved.server,
                "tool_name": resolved.name,
                "arguments": resolved.arguments,
            }
        )

    # turn_id + thread_id + tool_call ids are what TrueForgeClient.resume_with_approval needs.
    return {
        "turn_id": turn_id,
        "event_id": event.get("id"),
        "thread_id": event.get("thread_id"),
        "created_at": event.get("created_at"),
        "tool_calls": calls,
    }


class RunManager:
    def __init__(self, ledger: Ledger, trueforge: TrueForge, observer: RunObserver | None = None) -> None:
        self._ledger = ledger
        self._trueforge = trueforge
        self.observer = observer
        self._live: dict[str, LiveRun] = {}
        self._pumps: dict[str, asyncio.Task[None]] = {}
        self._approval_locks: dict[str, asyncio.Lock] = {}
        # run_id -> (expires at, monotonic clock; GET /runs/{id}/release body)
        self.release_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def approval_lock(self, run_id: str) -> asyncio.Lock:
        """Serializes approval calls per run so two requests can never both resume."""
        return self._approval_locks.setdefault(run_id, asyncio.Lock())

    async def close(self) -> None:
        for task in self._pumps.values():
            task.cancel()
        await asyncio.gather(*self._pumps.values(), return_exceptions=True)

    def pump_task(self, run_id: str) -> asyncio.Task[None] | None:
        """The run's pump while it is consuming TrueForge events; None once it has stopped."""
        return self._pumps.get(run_id)

    async def resume_active(self) -> None:
        """Restart the pump of every active run, e.g. at startup, so no run waits for a browser to connect."""
        cursor = await self._ledger.db.execute(
            f"SELECT id FROM runs WHERE status IN ({', '.join('?' * len(ACTIVE_STATUSES))})", tuple(ACTIVE_STATUSES)
        )
        for (run_id,) in await cursor.fetchall():
            run = await self._ledger.get_run(run_id)
            if run is not None:
                await self.ensure_pump(run)

    def _notify(self, method: str, *args: Any) -> None:
        if self.observer is None:
            return
        try:
            getattr(self.observer, method)(*args)
        except Exception:
            logger.exception("Run observer %s failed", method)

    def start(self, run_id: str, session_id: str, turn_id: str, after_sequence: int = 0, base: int = 0) -> None:
        """Pump a turn into the run. Its events are numbered base + TrueForge sequence in the run."""
        live = LiveRun()
        live.last_sequence = max(after_sequence, base)
        self._live[run_id] = live
        task = asyncio.create_task(self._pump(run_id, session_id, turn_id, live, base))
        self._pumps[run_id] = task
        task.add_done_callback(lambda _: self._pumps.pop(run_id, None))

    async def ensure_pump(self, run: dict[str, Any]) -> None:
        """Resume streaming from TrueForge for an active run with no pump, e.g. after a backend restart."""
        run_id = run["id"]
        if run["status"] not in ACTIVE_STATUSES or run_id in self._pumps:
            return
        live = self._live.get(run_id)
        base = await self._ledger.turn_base(run_id, run["turn_id"])
        if (live is not None and live.finished) or await self._ledger.has_event(run_id, "turn.done", after=base):
            return
        self.start(run_id, run["session_id"], run["turn_id"], await self._ledger.last_sequence(run_id), base)

    async def _pump(self, run_id: str, session_id: str, turn_id: str, live: LiveRun, base: int) -> None:
        run_id_var.set(run_id)
        fact_stream = FactStream()
        # Tool calls per model.message id; the live stream sends them as delta fragments.
        messages: dict[str, list[dict[str, Any]]] = {}
        # Text of each model.message by id, and the id of the latest one with text: the final message.
        texts: dict[str, str] = {}
        last_text_id: str | None = None
        pending = False
        asked = False
        attempt = 0
        try:
            while True:
                try:
                    stream = self._trueforge.stream_turn(session_id, turn_id, (live.last_sequence - base) or None)
                    async for turn_event in stream:
                        if turn_event.sequence is not None:
                            sequence = base + turn_event.sequence
                        elif turn_event.type == "turn.done":
                            # Read back from TrueForge storage after the live buffer expired; it is the
                            # turn's last event, so it goes after everything seen.
                            sequence = live.last_sequence + 1
                        else:
                            continue
                        if sequence <= live.last_sequence:
                            continue
                        attempt = 0
                        received_at = utc_now()
                        event, event_type = turn_event.data, turn_event.type
                        data = json.dumps(event)
                        live.publish(sequence, data)
                        self._ledger.append_event(run_id, sequence, event_type, data, received_at)

                        # After publish, and only queued, so facts never delay the stream.
                        facts.record(run_id, fact_stream.feed(event))

                        if event_type == "model.message":
                            calls = messages.setdefault(event["id"], [])
                            if event.get("tool_calls"):
                                calls[:] = [json.loads(json.dumps(call)) for call in event["tool_calls"]]
                                self._notify("tool_calls", run_id, session_id)
                            if text := message_text(event.get("content")):
                                texts[event["id"]], last_text_id = text, event["id"]
                        elif event_type == "model.message.delta":
                            if event.get("tool_calls"):
                                merge_tool_call_deltas(messages.setdefault(event["id"], []), event["tool_calls"])
                                self._notify("tool_calls", run_id, session_id)
                            if isinstance(event.get("content"), str) and event["content"]:
                                texts[event["id"]] = texts.get(event["id"], "") + event["content"]
                                last_text_id = event["id"]
                        elif event_type == "tool.response":
                            self._notify("tool_response", run_id, _find_call(messages, event.get("tool_call_id")), event)
                        elif event_type == "tool.response_required":
                            asked = True
                        elif event_type == "tool.approval_required":
                            pending = True
                            self._ledger.set_pending_action(run_id, _pending_action(turn_id, event, messages))
                        elif event_type == "turn.done":
                            live.finished = True
                            state = (event.get("state") or {}).get("status", "done")
                            if asked and state == "done":
                                final = "awaiting_input"
                            elif pending and state == "done":
                                final = "awaiting_approval"
                            else:
                                final = state
                            if final != "awaiting_approval":
                                self._ledger.set_status(run_id, final)
                            self._notify("turn_done", run_id, final, _final_text(event, texts.get(last_text_id or "", "")))
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
