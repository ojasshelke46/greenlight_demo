"""Hash chain primitives and verification for the tamper evident ledger.

Every chained row gets an entry {run_id, idx, kind, source}; its hash commits to the previous entry's
hash, so changing, removing or reordering any chained row breaks every hash after it. The run's latest
hash is signed with LEDGER_HMAC_KEY when one is configured.

Verification reads the ledger file through its own read only connection inside one read transaction, so
it sees a consistent, committed snapshot and never a row whose chain entry is still being written.
"""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from app.config import get_settings

GENESIS_HASH = "0" * 64
EXPORT_FORMAT = "greenlight.flight_recorder.v1"
PRE_FLIGHT_RECORDER = "created before flight recorder"
SIGNATURE_NOT_CHECKED = "signature not checked: LEDGER_HMAC_KEY is not set"

ChainKind = Literal["run", "event", "approval", "note"]

_FROM_SETTINGS: Any = object()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def make_entry(run_id: str, idx: int, kind: ChainKind, source: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": run_id, "idx": idx, "kind": kind, "source": source}


def entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode("utf-8") + canonical_json(entry)).hexdigest()


def head_hmac(key: str | None, run_id: str, idx: int, entry_hash_hex: str) -> str | None:
    if not key:
        return None
    message = f"{run_id}:{idx}:{entry_hash_hex}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


# Pure checks, shared by the database verifier and the offline export verifier.


def _result(
    ok: bool, entries: int, signed: bool, first_broken_idx: int | None = None, reason: str | None = None
) -> dict[str, Any]:
    return {"ok": ok, "entries": entries, "signed": signed, "first_broken_idx": first_broken_idx, "reason": reason}


def _walk(run_id: str, entries: list[dict[str, Any]]) -> tuple[tuple[int, str] | None, dict[tuple[str, str], int]]:
    """Check every entry in idx order. entries carry their reloaded "source" (None if the row is gone).

    Returns the first (idx, reason) failure, if any, and the (kind, ref_id) -> idx map of what is chained.
    """
    prev = GENESIS_HASH
    seen: dict[tuple[str, str], int] = {}
    for position, entry in enumerate(entries):
        idx, kind, ref_id = entry["idx"], entry["kind"], str(entry["ref_id"])
        if idx != position:
            return (position, f"entry {position} is missing"), seen
        if position == 0 and kind != "run":
            return (0, "entry 0 is not the run"), seen
        if entry["prev_hash"] != prev:
            return (idx, f"entry {idx} does not link to entry {idx - 1}" if idx else "entry 0 does not start the chain"), seen
        if (kind, ref_id) in seen:
            return (idx, f"{kind} {ref_id} is chained twice, at entries {seen[kind, ref_id]} and {idx}"), seen
        seen[kind, ref_id] = idx
        if entry["source"] is None:
            return (idx, f"{kind} at entry {idx} was deleted"), seen
        if entry_hash(prev, make_entry(run_id, idx, kind, entry["source"])) != entry["entry_hash"]:
            return (idx, f"{kind} at entry {idx} was edited"), seen
        prev = entry["entry_hash"]
    return None, seen


def _check_head(
    run_id: str, head: dict[str, Any] | None, last: dict[str, Any], key: str | None
) -> tuple[tuple[int, str] | None, bool, bool]:
    """Returns (failure, signed, signature_checked)."""
    if head is None:
        return (last["idx"], "the chain head is missing"), False, False
    if (head["idx"], head["entry_hash"]) != (last["idx"], last["entry_hash"]):
        return (last["idx"], f"the chain head points at entry {head['idx']}, but the chain ends at entry {last['idx']}"), False, False
    signed = bool(head["signed"])
    if not signed:
        return None, False, False
    if not key:
        return None, True, False
    expected = head_hmac(key, run_id, head["idx"], head["entry_hash"]) or ""
    if not hmac.compare_digest(expected, head["head_hmac"] or ""):
        return (last["idx"], "the head signature does not match LEDGER_HMAC_KEY"), True, True
    return None, True, True


def verify_export(document: dict[str, Any], hmac_key: str | None) -> dict[str, Any]:
    """Recheck an exported run offline: every hash, every link, the head, and the HMAC when a key is given.

    Also returns "hmac_checked". Rows deleted from or inserted into the database after export cannot be
    seen offline; verify_run covers those against the live ledger.
    """
    run_id, entries = document["run_id"], document["entries"]
    if not entries:
        return {**_result(False, 0, False, None, PRE_FLIGHT_RECORDER), "hmac_checked": False}
    failure, _ = _walk(run_id, entries)
    if failure is None:
        failure, signed, checked = _check_head(run_id, document.get("head"), entries[-1], hmac_key)
    else:
        signed, checked = bool((document.get("head") or {}).get("signed")), False
    if failure is not None:
        return {**_result(False, len(entries), signed, *failure), "hmac_checked": checked}
    return {**_result(True, len(entries), signed), "hmac_checked": checked}


# Reading the ledger.


def _settings_key() -> str | None:
    try:
        return get_settings().ledger_hmac_key
    except Exception:
        return None


def _as_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return {key: row[key] for key in row.keys()} if row is not None else None


@asynccontextmanager
async def open_readonly(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """A read only connection holding one read transaction, i.e. one consistent snapshot."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    db = await aiosqlite.connect(uri, uri=True)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("BEGIN")
        yield db
    finally:
        await db.close()


async def source_row(db: aiosqlite.Connection, run_id: str, kind: str, ref_id: str) -> dict[str, Any] | None:
    """The source row exactly as it was hashed, or None if it is gone."""
    if kind == "run":
        run = _as_dict(await (await db.execute("SELECT * FROM runs WHERE id = ?", (ref_id,))).fetchone())
        if run is None:
            return None
        first_turn = await (
            await db.execute("SELECT turn_id FROM turns WHERE run_id = ? ORDER BY created_at, rowid LIMIT 1", (ref_id,))
        ).fetchone()
        # status, turn_id and pending_action change after creation; the entry hashed the row as inserted.
        return {**run, "status": "running", "pending_action": None, "turn_id": first_turn["turn_id"] if first_turn else None}
    try:
        numeric_ref = int(ref_id)
    except ValueError:
        return None
    if kind == "event":
        query = "SELECT run_id, sequence, type, payload_json, received_at FROM events WHERE run_id = ? AND sequence = ?"
        return _as_dict(await (await db.execute(query, (run_id, numeric_ref))).fetchone())
    if kind == "approval":
        query = (
            "SELECT run_id, tool_name, arguments_json, decision, approver, decided_at, result FROM approvals"
            " WHERE id = ? AND run_id = ?"
        )
        return _as_dict(await (await db.execute(query, (numeric_ref, run_id))).fetchone())
    if kind == "note":
        query = "SELECT run_id, kind, payload_json, created_at FROM notes WHERE id = ? AND run_id = ?"
        return _as_dict(await (await db.execute(query, (numeric_ref, run_id))).fetchone())
    return None


async def _entries_with_sources(db: aiosqlite.Connection, run_id: str) -> list[dict[str, Any]]:
    cursor = await db.execute(
        "SELECT idx, kind, ref_id, prev_hash, entry_hash, created_at FROM chain WHERE run_id = ? ORDER BY idx", (run_id,)
    )
    entries = [dict(_as_dict(row) or {}) for row in await cursor.fetchall()]
    for entry in entries:
        entry["source"] = await source_row(db, run_id, entry["kind"], entry["ref_id"])
    return entries


async def _head(db: aiosqlite.Connection, run_id: str) -> dict[str, Any] | None:
    cursor = await db.execute("SELECT idx, entry_hash, head_hmac, signed FROM chain_heads WHERE run_id = ?", (run_id,))
    head = _as_dict(await cursor.fetchone())
    if head is not None:
        head["signed"] = bool(head["signed"])
    return head


async def _verify(db: aiosqlite.Connection, run_id: str, key: str | None, entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return _result(False, 0, False, None, PRE_FLIGHT_RECORDER)
    head = await _head(db, run_id)
    claimed_signed = bool(head and head["signed"])

    failure, seen = _walk(run_id, entries)
    if failure is not None:
        return _result(False, len(entries), claimed_signed, *failure)

    # Every row of the run must be chained exactly once; this is what catches inserted rows.
    for kind, query in (
        ("event", "SELECT sequence FROM events WHERE run_id = ? ORDER BY sequence"),
        ("approval", "SELECT id FROM approvals WHERE run_id = ? ORDER BY id"),
        ("note", "SELECT id FROM notes WHERE run_id = ? ORDER BY id"),
    ):
        for (ref,) in await (await db.execute(query, (run_id,))).fetchall():
            if (kind, str(ref)) not in seen:
                return _result(False, len(entries), claimed_signed, None, f"{kind} {ref} was inserted without a chain entry")

    failure, signed, checked = _check_head(run_id, head, entries[-1], key)
    if failure is not None:
        return _result(False, len(entries), signed, *failure)
    return _result(True, len(entries), signed, None, None if (checked or not signed) else SIGNATURE_NOT_CHECKED)


async def verify_run(run_id: str, db_path: str | None = None, hmac_key: Any = _FROM_SETTINGS) -> dict[str, Any]:
    """{ok, entries, signed, first_broken_idx, reason} for one run, read from the ledger file."""
    key = _settings_key() if hmac_key is _FROM_SETTINGS else hmac_key
    async with open_readonly(db_path or get_settings().ledger_path) as db:
        return await _verify(db, run_id, key, await _entries_with_sources(db, run_id))


async def load_run(run_id: str, db_path: str | None = None, hmac_key: Any = _FROM_SETTINGS) -> dict[str, Any] | None:
    """Everything an export needs, from one consistent snapshot. None if the run does not exist."""
    key = _settings_key() if hmac_key is _FROM_SETTINGS else hmac_key
    async with open_readonly(db_path or get_settings().ledger_path) as db:
        run = _as_dict(await (await db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))).fetchone())
        if run is None:
            return None
        entries = await _entries_with_sources(db, run_id)
        events = [
            _as_dict(row)
            for row in await (
                await db.execute(
                    "SELECT sequence, type, payload_json, received_at FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
                )
            ).fetchall()
        ]
        approvals = [
            _as_dict(row)
            for row in await (
                await db.execute(
                    "SELECT id, tool_name, arguments_json, decision, approver, decided_at, result FROM approvals"
                    " WHERE run_id = ? ORDER BY id",
                    (run_id,),
                )
            ).fetchall()
        ]
        notes = [
            _as_dict(row)
            for row in await (
                await db.execute("SELECT id, kind, payload_json, created_at FROM notes WHERE run_id = ? ORDER BY id", (run_id,))
            ).fetchall()
        ]
        return {
            "run": run,
            "entries": entries,
            "head": await _head(db, run_id),
            "verify": await _verify(db, run_id, key, entries),
            "events": events,
            "approvals": approvals,
            "notes": notes,
        }


def export_document(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The self contained JSON export: everything verify_export needs, with no database."""
    run = snapshot["run"]
    return {
        "format": EXPORT_FORMAT,
        "run_id": run["id"],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run": run,
        "entries": snapshot["entries"],
        "head": snapshot["head"],
        "head_hmac": (snapshot["head"] or {}).get("head_hmac"),
        "verify": snapshot["verify"],
    }
