"""The ledger's hash chain. Hashes are recomputed here from the spec, not with app.chain, so a bug in the
chain helpers cannot hide itself."""

import asyncio
import hashlib
import hmac
import json
from typing import Any

import aiosqlite
import pytest

from app.ledger import Ledger

RUN_ID = "run_chain"
HMAC_KEY = "test-ledger-key"
GENESIS = "0" * 64


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def recompute(prev_hash: str, run_id: str, idx: int, kind: str, source: dict[str, Any]) -> str:
    entry = {"run_id": run_id, "idx": idx, "kind": kind, "source": source}
    return hashlib.sha256(prev_hash.encode("utf-8") + canonical(entry)).hexdigest()


def as_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


async def source_row(db: aiosqlite.Connection, run_id: str, kind: str, ref_id: str) -> dict[str, Any]:
    if kind == "run":
        run = as_dict(await (await db.execute("SELECT * FROM runs WHERE id = ?", (ref_id,))).fetchone())
        first_turn = await (
            await db.execute("SELECT turn_id FROM turns WHERE run_id = ? ORDER BY created_at, rowid LIMIT 1", (ref_id,))
        ).fetchone()
        # The run entry hashes the row as inserted: status running, no pending action, its first turn.
        return {**run, "status": "running", "pending_action": None, "turn_id": first_turn["turn_id"]}
    if kind == "event":
        query = "SELECT run_id, sequence, type, payload_json, received_at FROM events WHERE run_id = ? AND sequence = ?"
        return as_dict(await (await db.execute(query, (run_id, int(ref_id)))).fetchone())
    if kind == "approval":
        query = "SELECT run_id, tool_name, arguments_json, decision, approver, decided_at, result FROM approvals WHERE id = ?"
        return as_dict(await (await db.execute(query, (int(ref_id),))).fetchone())
    query = "SELECT run_id, kind, payload_json, created_at FROM notes WHERE id = ?"
    return as_dict(await (await db.execute(query, (int(ref_id),))).fetchone())


async def chain_rows(ledger: Ledger, run_id: str = RUN_ID) -> list[dict[str, Any]]:
    cursor = await ledger.db.execute("SELECT * FROM chain WHERE run_id = ? ORDER BY idx", (run_id,))
    return [as_dict(row) for row in await cursor.fetchall()]


async def head(ledger: Ledger, run_id: str = RUN_ID) -> dict[str, Any] | None:
    row = await (await ledger.db.execute("SELECT * FROM chain_heads WHERE run_id = ?", (run_id,))).fetchone()
    return as_dict(row) if row else None


async def assert_chain_verifies(ledger: Ledger, key: str | None = HMAC_KEY, run_id: str = RUN_ID) -> list[dict[str, Any]]:
    rows = await chain_rows(ledger, run_id)
    assert [row["idx"] for row in rows] == list(range(len(rows))), "idx must start at 0 with no gaps"

    prev = GENESIS
    for row in rows:
        assert row["prev_hash"] == prev
        source = await source_row(ledger.db, run_id, row["kind"], row["ref_id"])
        assert row["entry_hash"] == recompute(prev, run_id, row["idx"], row["kind"], source), row
        prev = row["entry_hash"]

    top = await head(ledger, run_id)
    assert top is not None
    assert (top["idx"], top["entry_hash"]) == (rows[-1]["idx"], rows[-1]["entry_hash"])
    if key is None:
        assert top["head_hmac"] is None and top["signed"] == 0
    else:
        message = f"{run_id}:{top['idx']}:{top['entry_hash']}".encode("utf-8")
        assert top["head_hmac"] == hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()
        assert top["signed"] == 1
    return rows


@pytest.fixture
async def ledger(tmp_path):
    ledger = Ledger(str(tmp_path / "chain.db"), hmac_key=HMAC_KEY)
    await ledger.init()
    await ledger.create_run(run_id=RUN_ID, repo="acme/widgets", mode="ship", via_fork=False, session_id="s", turn_id="turn_1")
    yield ledger
    await ledger.close()


def event(sequence: int) -> str:
    # Non ASCII on purpose: canonical JSON keeps it as UTF 8 rather than escaping it.
    return json.dumps({"type": "model.message.delta", "id": f"m{sequence}", "content": f"✔ ₹ step {sequence}"}, ensure_ascii=False)


async def test_run_creation_is_the_genesis_entry(ledger):
    rows = await assert_chain_verifies(ledger)

    assert len(rows) == 1
    assert (rows[0]["kind"], rows[0]["ref_id"], rows[0]["prev_hash"]) == ("run", RUN_ID, GENESIS)


async def test_chain_grows_in_strict_order_under_concurrent_event_and_approval_writes(ledger):
    events, approvals, notes = 300, 12, 5

    async def stream_writer():
        for sequence in range(1, events + 1):
            ledger.append_event(RUN_ID, sequence, "model.message.delta", event(sequence))
            if sequence % 7 == 0:
                await asyncio.sleep(0)

    async def approval_handler():
        for i in range(approvals):
            await ledger.append_approval(
                run_id=RUN_ID, tool_name="merge_pull_request", arguments={"pullNumber": i}, decision="approve",
                approver=f"reviewer {i}", result="accepted" if i == 0 else f"refused: attempt {i}",
            )
            await asyncio.sleep(0)

    async def note_writer():
        for i in range(notes):
            await ledger.append_note(RUN_ID, "receipt", {"attempt": i, "cost": "₹4.20"})
            await asyncio.sleep(0)

    await asyncio.gather(stream_writer(), approval_handler(), note_writer())
    await ledger.flush()

    rows = await assert_chain_verifies(ledger)
    kinds = [row["kind"] for row in rows]
    assert kinds.count("run") == 1 and kinds[0] == "run"
    assert (kinds.count("event"), kinds.count("approval"), kinds.count("note")) == (events, approvals, notes)

    # Each source keeps its own order inside the one chain.
    assert [int(row["ref_id"]) for row in rows if row["kind"] == "event"] == list(range(1, events + 1))
    approval_ids = [int(row["ref_id"]) for row in rows if row["kind"] == "approval"]
    note_ids = [int(row["ref_id"]) for row in rows if row["kind"] == "note"]
    assert approval_ids == sorted(approval_ids) and len(set(approval_ids)) == approvals
    assert note_ids == sorted(note_ids) and len(set(note_ids)) == notes
    # The writers really interleaved rather than running one after another.
    assert kinds.index("approval") < len(kinds) - 1 - kinds[::-1].index("event")


async def test_notes_are_chained(ledger):
    note = await ledger.append_note(RUN_ID, "receipt", {"model_calls": 4, "cost_inr": 4.2})

    rows = await assert_chain_verifies(ledger)

    assert (rows[-1]["kind"], rows[-1]["ref_id"]) == ("note", str(note["id"]))


async def test_replayed_event_is_chained_once(ledger):
    ledger.append_event(RUN_ID, 1, "turn.created", event(1))
    ledger.append_event(RUN_ID, 1, "turn.created", event(1))
    await ledger.flush()

    rows = await assert_chain_verifies(ledger)

    assert [row["kind"] for row in rows] == ["run", "event"]


async def test_unsigned_chain_without_hmac_key(tmp_path):
    ledger = Ledger(str(tmp_path / "unsigned.db"), hmac_key=None)
    await ledger.init()
    try:
        await ledger.create_run(run_id=RUN_ID, repo="acme/widgets", mode="pr_only", via_fork=True, session_id="s", turn_id="t")
        ledger.append_event(RUN_ID, 1, "turn.created", event(1))
        await ledger.flush()

        rows = await assert_chain_verifies(ledger, key=None)
        assert len(rows) == 2
    finally:
        await ledger.close()


async def test_run_created_before_the_chain_is_not_backfilled(ledger):
    # A run row from before this change: no chain entry and no head.
    await ledger.db.execute(
        "INSERT INTO runs (id, repo, mode, via_fork, session_id, turn_id, status, created_at)"
        " VALUES ('old_run', 'acme/old', 'ship', 0, 's', 't', 'running', '2026-01-01T00:00:00+00:00')"
    )
    await ledger.db.commit()

    ledger.append_event("old_run", 1, "turn.created", event(1))
    await ledger.flush()
    await ledger.append_approval(
        run_id="old_run", tool_name="merge_pull_request", arguments={}, decision="reject", approver="a", result="accepted"
    )
    await ledger.append_note("old_run", "receipt", {})

    assert await chain_rows(ledger, "old_run") == []
    assert await head(ledger, "old_run") is None
    assert len((await ledger.audit_trail("old_run"))["events"]) == 1


async def test_chain_is_append_only(ledger):
    with pytest.raises(aiosqlite.IntegrityError, match="append only"):
        await ledger.db.execute("UPDATE chain SET entry_hash = 'x' WHERE run_id = ?", (RUN_ID,))
    with pytest.raises(aiosqlite.IntegrityError, match="append only"):
        await ledger.db.execute("DELETE FROM chain WHERE run_id = ?", (RUN_ID,))


async def test_editing_a_chained_row_breaks_recomputation(ledger):
    ledger.append_event(RUN_ID, 1, "turn.created", event(1))
    await ledger.flush()
    await assert_chain_verifies(ledger)

    # Someone with file access drops the guard and rewrites history.
    await ledger.db.execute("DROP TRIGGER events_no_update")
    await ledger.db.execute("UPDATE events SET payload_json = '{}' WHERE run_id = ? AND sequence = 1", (RUN_ID,))
    await ledger.db.commit()

    with pytest.raises(AssertionError):
        await assert_chain_verifies(ledger)


async def test_failed_write_leaves_no_orphan_chain_entry(ledger):
    with pytest.raises(aiosqlite.IntegrityError):
        await ledger.create_run(run_id=RUN_ID, repo="acme/dupe", mode="ship", via_fork=False, session_id="s", turn_id="t2")

    rows = await assert_chain_verifies(ledger)
    assert len(rows) == 1
