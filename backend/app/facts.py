"""Facts the agent reported during a run, in their own run_facts table in the ledger database.

Module level so features can call add_fact and get_facts directly. init() and close() are called
from the app lifespan.

Writes go through the ledger's own connection, never a second one: the ledger interleaves reads and
queued writes on one connection, and any other writer to the same file makes those writes fail with
SQLITE_BUSY, silently dropping audit events. Features must not open their own connection either.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.log import run_id_var

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_facts_run_kind ON run_facts(run_id, kind);
"""

_db: aiosqlite.Connection | None = None
_queue: asyncio.Queue[tuple[str, list[dict[str, Any]]] | None] | None = None
_writer: asyncio.Task[None] | None = None
_write_lock: asyncio.Lock = asyncio.Lock()


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("app.facts.init() was not called")
    return _db


async def init(ledger_db: aiosqlite.Connection, write_lock: asyncio.Lock | None = None) -> None:
    """ledger_db is Ledger.db and write_lock is Ledger.write_lock. The ledger owns and closes the connection;
    close() here only stops the writer. Holding the ledger's write lock around each commit keeps it from
    committing half of a ledger transaction (a row without its chain entry)."""
    global _db, _queue, _writer, _write_lock
    _db = ledger_db
    _write_lock = write_lock or asyncio.Lock()
    await _db.executescript(_SCHEMA)
    await _db.commit()
    _queue = asyncio.Queue()
    _writer = asyncio.create_task(_write_loop(_queue))


async def close() -> None:
    global _db, _queue, _writer
    if _queue is not None and _writer is not None:
        _queue.put_nowait(None)
        await _writer
    _db, _queue, _writer = None, None, None


async def add_fact(run_id: str, fact: dict[str, Any]) -> None:
    db = _conn()
    async with _write_lock:
        await _insert(db, run_id, fact)
        await db.commit()


async def get_facts(run_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    """Facts for a run in the order they were reported, each as the agent sent it."""
    query = "SELECT payload_json FROM run_facts WHERE run_id = ?"
    params: tuple[str, ...] = (run_id,)
    if kind is not None:
        query += " AND kind = ?"
        params += (kind,)
    cursor = await _conn().execute(query + " ORDER BY id", params)
    return [json.loads(row["payload_json"]) for row in await cursor.fetchall()]


def record(run_id: str, facts: list[dict[str, Any]]) -> None:
    """Queue facts for storage. Never blocks or raises, so the event stream is not slowed."""
    if not facts:
        return
    if _queue is None:
        logger.warning("Dropped %d facts: app.facts is not initialised", len(facts))
        return
    _queue.put_nowait((run_id, facts))


async def flush() -> None:
    if _queue is not None:
        await _queue.join()


async def _insert(db: aiosqlite.Connection, run_id: str, fact: dict[str, Any]) -> None:
    await db.execute(
        "INSERT INTO run_facts (run_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (run_id, fact["kind"], json.dumps(fact), datetime.now(timezone.utc).isoformat()),
    )


async def _write_loop(queue: asyncio.Queue[tuple[str, list[dict[str, Any]]] | None]) -> None:
    while True:
        item = await queue.get()
        try:
            if item is None:
                return
            run_id, facts = item
            try:
                async with _write_lock:
                    for fact in facts:
                        await _insert(_conn(), run_id, fact)
                    await _conn().commit()
            except Exception:
                # No rollback: on the shared connection it would also discard the ledger's pending writes.
                token = run_id_var.set(run_id)
                logger.exception("Could not store %d facts", len(facts))
                run_id_var.reset(token)
        finally:
            queue.task_done()
