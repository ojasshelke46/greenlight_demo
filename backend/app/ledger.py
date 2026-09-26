import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.chain import GENESIS_HASH, ChainKind, entry_hash, head_hmac, make_entry
from app.config import get_settings
from app.log import run_id_var

logger = logging.getLogger(__name__)

_KEY_FROM_SETTINGS = object()


def _settings_hmac_key() -> str | None:
    try:
        return get_settings().ledger_hmac_key
    except Exception:  # a Ledger outside the app (scripts, some tests) may have no full settings
        return None

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

-- Things derived about a run after the fact, e.g. its cost receipt. kind names the note type.
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_run_id ON notes(run_id);

-- Tamper evidence: one hash chained entry per run, event, approval and note row, written in the same
-- transaction as that row. ref_id is the row's id in its own table (the run id, the event sequence, or
-- the approvals / notes autoincrement id). Runs created before the chain existed have no entries.
CREATE TABLE IF NOT EXISTS chain (
    run_id TEXT NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('run', 'event', 'approval', 'note')),
    ref_id TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, idx)
);

-- Latest entry per run, signed with LEDGER_HMAC_KEY when one is configured (signed = 0 otherwise).
CREATE TABLE IF NOT EXISTS chain_heads (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    idx INTEGER NOT NULL,
    entry_hash TEXT NOT NULL,
    head_hmac TEXT,
    signed INTEGER NOT NULL
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append only'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_update BEFORE UPDATE ON notes
BEGIN SELECT RAISE(ABORT, 'notes are append only'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_delete BEFORE DELETE ON notes
BEGIN SELECT RAISE(ABORT, 'notes are append only'); END;
CREATE TRIGGER IF NOT EXISTS chain_no_update BEFORE UPDATE ON chain
BEGIN SELECT RAISE(ABORT, 'chain is append only'); END;
CREATE TRIGGER IF NOT EXISTS chain_no_delete BEFORE DELETE ON chain
BEGIN SELECT RAISE(ABORT, 'chain is append only'); END;
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

    def __init__(self, db_path: str, hmac_key: Any = _KEY_FROM_SETTINGS) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        # (run_id, write) pairs; the run id tags the log line if the write fails.
        self._queue: asyncio.Queue[tuple[str, _Write] | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._hmac_key: str | None = _settings_hmac_key() if hmac_key is _KEY_FROM_SETTINGS else hmac_key
        # One write transaction at a time on the shared connection, so a source row and its chain entry
        # always commit together. Lock order is always transaction first, then the run's chain lock.
        self._tx_lock = asyncio.Lock()
        # Serialises chain appends per run: the stream writer and the approval handler never compute
        # the same idx or prev_hash.
        self._chain_locks: dict[str, asyncio.Lock] = {}

    def _chain_lock(self, run_id: str) -> asyncio.Lock:
        return self._chain_locks.setdefault(run_id, asyncio.Lock())

    async def _chain(
        self, db: aiosqlite.Connection, run_id: str, kind: ChainKind, ref_id: str | int, source: dict[str, Any]
    ) -> None:
        """Append one chain entry. Caller holds the transaction and the run's chain lock."""
        cursor = await db.execute("SELECT idx, entry_hash FROM chain_heads WHERE run_id = ?", (run_id,))
        head = await cursor.fetchone()
        if head is None:
            if kind != "run":
                return  # the run predates the chain; it is never backfilled
            idx, prev_hash = 0, GENESIS_HASH
        else:
            idx, prev_hash = head["idx"] + 1, head["entry_hash"]

        digest = entry_hash(prev_hash, make_entry(run_id, idx, kind, source))
        await db.execute(
            "INSERT INTO chain (run_id, idx, kind, ref_id, prev_hash, entry_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, idx, kind, str(ref_id), prev_hash, digest, utc_now()),
        )
        signature = head_hmac(self._hmac_key, run_id, idx, digest)
        await db.execute(
            "INSERT INTO chain_heads (run_id, idx, entry_hash, head_hmac, signed) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET idx = excluded.idx, entry_hash = excluded.entry_hash,"
            " head_hmac = excluded.head_hmac, signed = excluded.signed",
            (run_id, idx, digest, signature, int(signature is not None)),
        )

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
            async with self._tx_lock:
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

    async def _transaction(self, run_id: str, work: Callable[[aiosqlite.Connection], Awaitable[Any]]) -> Any:
        """Run work and commit it as one transaction, holding the run's chain lock throughout."""
        async with self._tx_lock:
            try:
                async with self._chain_lock(run_id):
                    result = await work(self.db)
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise
        return result

    async def create_run(
        self, *, run_id: str, repo: str, mode: str, via_fork: bool, session_id: str, turn_id: str
    ) -> None:
        row = {
            "id": run_id,
            "repo": repo,
            "mode": mode,
            "via_fork": int(via_fork),
            "session_id": session_id,
            "turn_id": turn_id,
            "status": "running",
            "pending_action": None,
            "created_at": utc_now(),
        }

        async def work(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT INTO runs (id, repo, mode, via_fork, session_id, turn_id, status, pending_action, created_at)"
                " VALUES (:id, :repo, :mode, :via_fork, :session_id, :turn_id, :status, :pending_action, :created_at)",
                row,
            )
            await db.execute(
                "INSERT INTO turns (run_id, turn_id, base_sequence, created_at) VALUES (?, ?, 0, ?)",
                (run_id, turn_id, utc_now()),
            )
            await self._chain(db, run_id, "run", run_id, row)

        await self._transaction(run_id, work)

    async def begin_turn(self, run_id: str, turn_id: str, base_sequence: int) -> None:
        """Make turn_id the run's current turn, e.g. the turn TrueForge created on resume."""

        async def work(db: aiosqlite.Connection) -> None:
            await db.execute(
                "INSERT INTO turns (run_id, turn_id, base_sequence, created_at) VALUES (?, ?, ?, ?)",
                (run_id, turn_id, base_sequence, utc_now()),
            )
            await db.execute(
                "UPDATE runs SET turn_id = ?, status = 'running', pending_action = NULL WHERE id = ?",
                (turn_id, run_id),
            )

        await self._transaction(run_id, work)

    async def turn_base(self, run_id: str, turn_id: str) -> int:
        cursor = await self.db.execute(
            "SELECT base_sequence FROM turns WHERE run_id = ? AND turn_id = ?", (run_id, turn_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def append_approval(
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
        source = {
            "run_id": run_id,
            "tool_name": tool_name,
            "arguments_json": json.dumps(arguments),
            "decision": decision,
            "approver": approver,
            "decided_at": row["decided_at"],
            "result": result,
        }

        async def work(db: aiosqlite.Connection) -> None:
            cursor = await db.execute(
                "INSERT INTO approvals (run_id, tool_name, arguments_json, decision, approver, decided_at, result)"
                " VALUES (:run_id, :tool_name, :arguments_json, :decision, :approver, :decided_at, :result)",
                source,
            )
            await self._chain(db, run_id, "approval", cursor.lastrowid, source)

        await self._transaction(run_id, work)
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

    def append_event(
        self, run_id: str, sequence: int, type: str, payload: str, received_at: str | None = None
    ) -> None:
        """Queue one event. payload is the event JSON exactly as streamed; received_at defaults to now."""
        source = {
            "run_id": run_id,
            "sequence": sequence,
            "type": type,
            "payload_json": payload,
            "received_at": received_at or utc_now(),
        }

        # Runs in the queued writer, never on the SSE path: hashing costs the stream nothing.
        async def write(db: aiosqlite.Connection) -> None:
            async with self._chain_lock(run_id):
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO events (run_id, sequence, type, payload_json, received_at)"
                    " VALUES (:run_id, :sequence, :type, :payload_json, :received_at)",
                    source,
                )
                if cursor.rowcount == 1:  # a replayed sequence is ignored, so it is not chained twice
                    await self._chain(db, run_id, "event", sequence, source)

        self._queue.put_nowait((run_id, write))

    async def append_note(self, run_id: str, kind: str, payload: Any) -> dict[str, Any]:
        """Durably append one note about a run. payload must be JSON serialisable."""
        row = {"kind": kind, "payload": payload, "created_at": utc_now()}
        source = {"run_id": run_id, "kind": kind, "payload_json": json.dumps(payload), "created_at": row["created_at"]}

        async def work(db: aiosqlite.Connection) -> int:
            cursor = await db.execute(
                "INSERT INTO notes (run_id, kind, payload_json, created_at) VALUES (:run_id, :kind, :payload_json, :created_at)",
                source,
            )
            await self._chain(db, run_id, "note", cursor.lastrowid, source)
            return cursor.lastrowid

        note_id = await self._transaction(run_id, work)
        return {"id": note_id, **row}

    async def get_notes(self, run_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, kind, payload_json, created_at FROM notes WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if kind is not None:
            query += " AND kind = ?"
            params += (kind,)
        cursor = await self.db.execute(query + " ORDER BY id", params)
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in await cursor.fetchall()
        ]

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

    async def list_runs(self, limit: int) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT id, repo, mode, via_fork, status, created_at FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [
            {
                "id": row["id"],
                "repo": row["repo"],
                "mode": row["mode"],
                "via_fork": bool(row["via_fork"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in await cursor.fetchall()
        ]

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
