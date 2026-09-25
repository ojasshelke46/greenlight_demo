import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.log import run_id_var

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    mode TEXT NOT NULL,
    via_fork INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    status TEXT NOT NULL,
    pending_action TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL REFERENCES runs(id),
    sequence INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    decision TEXT,
    approver TEXT,
    decided_at TEXT,
    result TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);

-- Every TrueForge turn a run has streamed. A resumed turn numbers its events from 1 again,
-- so its events are stored at base_sequence + turn sequence.
CREATE TABLE IF NOT EXISTS turns (
    run_id TEXT NOT NULL REFERENCES runs(id),
    turn_id TEXT NOT NULL,
    base_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, turn_id)
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append only'); END;
"""

_Write = Callable[[aiosqlite.Connection], Awaitable[Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_row(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "repo": row["repo"],
        "mode": row["mode"],
        "via_fork": bool(row["via_fork"]),
        "session_id": row["session_id"],
        "turn_id": row["turn_id"],
        "status": row["status"],
        "pending_action": json.loads(row["pending_action"]) if row["pending_action"] else None,
        "created_at": row["created_at"],
    }


class Ledger:
    """Single SQLite connection. Stream side writes are queued so disk latency never stalls the SSE fan out."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        # (run_id, write) pairs; the run id tags the log line if the write fails.
        self._queue: asyncio.Queue[tuple[str, _Write] | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Ledger.init() was not called"
        return self._db

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        self._writer = asyncio.create_task(self._write_loop())

    async def close(self) -> None:
        if self._writer is not None:
            self._queue.put_nowait(None)
            await self._writer
        if self._db is not None:
            await self._db.close()

    async def flush(self) -> None:
        await self._queue.join()

    async def _write_loop(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while not self._queue.empty():
                batch.append(self._queue.get_nowait())

            run_id: str | None = None
            try:
                for item in batch:
                    if item is not None:
                        run_id, write = item
                        await write(self.db)
                await self.db.commit()
            except Exception:
                token = run_id_var.set(run_id)
                logger.exception("Ledger write failed; batch rolled back")
                run_id_var.reset(token)
                await self.db.rollback()
            finally:
                for _ in batch:
                    self._queue.task_done()

            if None in batch:
                return

    async def create_run(
        self, *, run_id: str, repo: str, mode: str, via_fork: bool, session_id: str, turn_id: str
    ) -> None:
        await self.db.execute(
            "INSERT INTO runs (id, repo, mode, via_fork, session_id, turn_id, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
            (run_id, repo, mode, int(via_fork), session_id, turn_id, utc_now()),
        )
        await self.db.execute(
            "INSERT INTO turns (run_id, turn_id, base_sequence, created_at) VALUES (?, ?, 0, ?)",
            (run_id, turn_id, utc_now()),
        )
        await self.db.commit()

    async def begin_turn(self, run_id: str, turn_id: str, base_sequence: int) -> None:
        """Make turn_id the run's current turn, e.g. the turn TrueForge created on resume."""
        await self.db.execute(
            "INSERT INTO turns (run_id, turn_id, base_sequence, created_at) VALUES (?, ?, ?, ?)",
            (run_id, turn_id, base_sequence, utc_now()),
        )
        await self.db.execute(
            "UPDATE runs SET turn_id = ?, status = 'running', pending_action = NULL WHERE id = ?",
            (turn_id, run_id),
        )
        await self.db.commit()

    async def turn_base(self, run_id: str, turn_id: str) -> int:
        cursor = await self.db.execute(
            "SELECT base_sequence FROM turns WHERE run_id = ? AND turn_id = ?", (run_id, turn_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def record_approval(
        self,
        *,
        run_id: str,
        tool_name: str,
        arguments: Any,
        decision: str,
        approver: str,
        result: str,
    ) -> dict[str, Any]:
        """Durably record one approval attempt. result is "accepted" or "refused: <reason>"."""
        row = {
            "tool_name": tool_name,
            "arguments": arguments,
            "decision": decision,
            "approver": approver,
            "decided_at": utc_now(),
            "result": result,
        }
        await self.db.execute(
            "INSERT INTO approvals (run_id, tool_name, arguments_json, decision, approver, decided_at, result)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, tool_name, json.dumps(arguments), decision, approver, row["decided_at"], result),
        )
        await self.db.commit()
        return row

    async def accepted_approval(self, run_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT tool_name, arguments_json, decision, approver, decided_at, result FROM approvals"
            " WHERE run_id = ? AND result = 'accepted' ORDER BY id LIMIT 1",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "tool_name": row["tool_name"],
            "arguments": json.loads(row["arguments_json"]),
            "decision": row["decision"],
            "approver": row["approver"],
            "decided_at": row["decided_at"],
            "result": row["result"],
        }

    def append_event(self, run_id: str, sequence: int, event_type: str, payload_json: str, received_at: str) -> None:
        async def write(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT OR IGNORE INTO events (run_id, sequence, type, payload_json, received_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, sequence, event_type, payload_json, received_at),
            )

        self._queue.put_nowait((run_id, write))

    def set_pending_action(self, run_id: str, pending_action: dict[str, Any]) -> None:
        async def write(db: aiosqlite.Connection) -> None:
            await db.execute(
                "UPDATE runs SET pending_action = ?, status = 'awaiting_approval' WHERE id = ?",
                (json.dumps(pending_action), run_id),
            )

        self._queue.put_nowait((run_id, write))

    def set_status(self, run_id: str, status: str) -> None:
        async def write(db: aiosqlite.Connection) -> None:
            await db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))

        self._queue.put_nowait((run_id, write))

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        return _run_row(row) if row else None

    async def events_after(self, run_id: str, after: int) -> list[tuple[int, str]]:
        cursor = await self.db.execute(
            "SELECT sequence, payload_json FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence",
            (run_id, after),
        )
        return [(row["sequence"], row["payload_json"]) for row in await cursor.fetchall()]

    async def last_sequence(self, run_id: str) -> int:
        cursor = await self.db.execute("SELECT MAX(sequence) FROM events WHERE run_id = ?", (run_id,))
        row = await cursor.fetchone()
        return row[0] or 0

    async def has_event(self, run_id: str, event_type: str, after: int = 0) -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM events WHERE run_id = ? AND type = ? AND sequence > ? LIMIT 1",
            (run_id, event_type, after),
        )
        return await cursor.fetchone() is not None

    async def audit_trail(self, run_id: str) -> dict[str, Any] | None:
        run = await self.get_run(run_id)
        if run is None:
            return None

        cursor = await self.db.execute(
            "SELECT sequence, type, payload_json, received_at FROM events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        events = [
            {
                "sequence": row["sequence"],
                "type": row["type"],
                "payload": json.loads(row["payload_json"]),
                "received_at": row["received_at"],
            }
            for row in await cursor.fetchall()
        ]

        cursor = await self.db.execute(
            "SELECT tool_name, arguments_json, decision, approver, decided_at, result FROM approvals"
            " WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        approvals = [
            {
                "tool_name": row["tool_name"],
                "arguments": json.loads(row["arguments_json"]),
                "decision": row["decision"],
                "approver": row["approver"],
                "decided_at": row["decided_at"],
                "result": row["result"],
            }
            for row in await cursor.fetchall()
        ]

        return {"run": run, "events": events, "approvals": approvals}
