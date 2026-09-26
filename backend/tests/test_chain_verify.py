"""verify_run against a real ledger file, clean and tampered with by someone who has file access."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.chain import GENESIS_HASH, PRE_FLIGHT_RECORDER, SIGNATURE_NOT_CHECKED, entry_hash, head_hmac, make_entry, verify_run
from app.event_types import NOTE_RECEIPT
from app.ledger import Ledger

RUN_ID = "run_verify"
KEY = "verify-test-key"

# Chain layout of the run built below: idx 0 run, 1..8 events (sequence == idx), 9 approval, 10 note.
EVENT_IDX = {sequence: sequence for sequence in range(1, 9)}
APPROVAL_IDX, NOTE_IDX, ENTRIES = 9, 10, 11


def call_tool(call_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "call_tool", "arguments": json.dumps({"mcp_server": "github", "tool_name": tool, "input": arguments})},
        "tool_info": {"type": "truefoundry-system", "name": "call_tool"},
    }


def exec_call(call_id: str, command: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "exec", "arguments": json.dumps({"command": command})},
        "tool_info": {"type": "truefoundry-system", "name": "exec"},
    }


def response(call_id: str, exit_code: int, result: str) -> dict[str, Any]:
    content = json.dumps({"success": True, "response": {"exitCode": exit_code, "result": result}})
    return {"type": "tool.response", "id": f"r_{call_id}", "tool_call_id": call_id, "content": content}


SCRIPT: list[dict[str, Any]] = [
    {"type": "turn.created", "id": "e1", "turn_id": "turn_1"},
    {"type": "model.message", "id": "m1", "tool_calls": [exec_call("c1", "npm audit --json")]},
    response("c1", 1, "GHSA-r683-j2x4-v87g " + "x" * 5000),
    {"type": "model.message", "id": "m2", "tool_calls": [call_tool("c2", "create_pull_request", {"owner": "acme", "repo": "widgets", "title": "Upgrade node-fetch"})]},
    response("c2", 0, '{"number": 7}'),
    {"type": "model.message", "id": "m3", "tool_calls": [call_tool("c3", "merge_pull_request", {"owner": "acme", "repo": "widgets", "pullNumber": 7})]},
    {"type": "tool.approval_required", "id": "e7", "tool_calls": [{"id": "c3", "source_event_id": "m3"}]},
    {"type": "turn.done", "id": "e8", "state": {"status": "done"}},
]


async def build_run(ledger: Ledger, run_id: str = RUN_ID, with_receipt: bool = True) -> None:
    await ledger.create_run(run_id=run_id, repo="acme/widgets", mode="ship", via_fork=False, session_id="s", turn_id="turn_1")
    for sequence, event in enumerate(SCRIPT, start=1):
        ledger.append_event(run_id, sequence, event["type"], json.dumps(event))
    await ledger.flush()
    await ledger.append_approval(
        run_id=run_id, tool_name="merge_pull_request", arguments={"owner": "acme", "repo": "widgets", "pullNumber": 7},
        decision="approve", approver="Shreyash", result="accepted",
    )
    if with_receipt:
        await ledger.append_note(run_id, NOTE_RECEIPT, {"advisories_fixed": 1, "model_calls": 3, "cost_inr": None, "source": "events"})


@pytest.fixture
async def ledger_file(tmp_path) -> Path:
    path = tmp_path / "ledger.db"
    ledger = Ledger(str(path), hmac_key=KEY)
    await ledger.init()
    await build_run(ledger)
    await ledger.close()
    return path


def tamper(path: Path, *statements: tuple[str, tuple]) -> None:
    """What an attacker with file access does: drop the append only guards and rewrite rows."""
    db = sqlite3.connect(path)
    for trigger in ("events_no_update", "events_no_delete", "approvals_no_update", "chain_no_update", "chain_no_delete"):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for sql, params in statements:
        db.execute(sql, params)
    db.commit()
    db.close()


async def verify(path: Path, key: str | None = KEY) -> dict[str, Any]:
    return await verify_run(RUN_ID, db_path=str(path), hmac_key=key)


async def test_clean_run_verifies(ledger_file):
    assert await verify(ledger_file) == {"ok": True, "entries": ENTRIES, "signed": True, "first_broken_idx": None, "reason": None}


async def test_edited_event_payload_fails_at_its_entry(ledger_file):
    tamper(ledger_file, ("UPDATE events SET payload_json = ? WHERE run_id = ? AND sequence = 3", ('{"type":"tool.response"}', RUN_ID)))

    result = await verify(ledger_file)

    assert result["ok"] is False
    assert result["first_broken_idx"] == EVENT_IDX[3]
    assert result["reason"] == "event at entry 3 was edited"


async def test_edited_approval_fails(ledger_file):
    tamper(ledger_file, ("UPDATE approvals SET approver = 'Mallory' WHERE run_id = ?", (RUN_ID,)))

    result = await verify(ledger_file)

    assert (result["ok"], result["first_broken_idx"], result["reason"]) == (False, APPROVAL_IDX, "approval at entry 9 was edited")


async def test_deleted_event_row_fails(ledger_file):
    tamper(ledger_file, ("DELETE FROM events WHERE run_id = ? AND sequence = 5", (RUN_ID,)))

    result = await verify(ledger_file)

    assert (result["ok"], result["first_broken_idx"], result["reason"]) == (False, 5, "event at entry 5 was deleted")


async def test_inserted_unchained_row_fails(ledger_file):
    tamper(
        ledger_file,
        (
            "INSERT INTO events (run_id, sequence, type, payload_json, received_at) VALUES (?, 99, 'tool.response', '{}', '2026-01-01T00:00:00+00:00')",
            (RUN_ID,),
        ),
    )

    result = await verify(ledger_file)

    assert (result["ok"], result["first_broken_idx"], result["reason"]) == (False, None, "event 99 was inserted without a chain entry")


async def test_rewritten_chain_with_valid_hashes_fails_on_the_head_hmac(ledger_file):
    # The attacker edits the approval, then recomputes every entry hash and the head. Without the key
    # the head_hmac they write is wrong, and that is the only thing left to catch it.
    tamper(ledger_file, ("UPDATE approvals SET approver = 'Mallory' WHERE run_id = ?", (RUN_ID,)))
    db = sqlite3.connect(ledger_file)
    db.row_factory = sqlite3.Row
    prev = GENESIS_HASH
    rows = db.execute("SELECT idx, kind, ref_id FROM chain WHERE run_id = ? ORDER BY idx", (RUN_ID,)).fetchall()
    for row in rows:
        source = _source(db, row["kind"], row["ref_id"])
        digest = entry_hash(prev, make_entry(RUN_ID, row["idx"], row["kind"], source))
        db.execute("UPDATE chain SET prev_hash = ?, entry_hash = ? WHERE run_id = ? AND idx = ?", (prev, digest, RUN_ID, row["idx"]))
        prev = digest
    forged = head_hmac("attacker-guess", RUN_ID, rows[-1]["idx"], prev)
    db.execute("UPDATE chain_heads SET entry_hash = ?, head_hmac = ? WHERE run_id = ?", (prev, forged, RUN_ID))
    db.commit()
    db.close()

    result = await verify(ledger_file)
    assert (result["ok"], result["first_broken_idx"], result["reason"]) == (
        False,
        ENTRIES - 1,
        "the head signature does not match LEDGER_HMAC_KEY",
    )

    # Without the key every hash and link checks out, which is exactly why the head is signed.
    unkeyed = await verify(ledger_file, key=None)
    assert (unkeyed["ok"], unkeyed["reason"]) == (True, SIGNATURE_NOT_CHECKED)


async def test_run_from_before_the_flight_recorder(ledger_file):
    db = sqlite3.connect(ledger_file)
    db.execute(
        "INSERT INTO runs (id, repo, mode, via_fork, session_id, turn_id, status, created_at)"
        " VALUES ('legacy', 'acme/old', 'ship', 0, 's', 't', 'done', '2026-01-01T00:00:00+00:00')"
    )
    db.commit()
    db.close()

    result = await verify_run("legacy", db_path=str(ledger_file), hmac_key=KEY)

    assert result == {"ok": False, "entries": 0, "signed": False, "first_broken_idx": None, "reason": PRE_FLIGHT_RECORDER}


async def test_unsigned_run_verifies_as_unsigned(tmp_path):
    ledger = Ledger(str(tmp_path / "unsigned.db"), hmac_key=None)
    await ledger.init()
    await build_run(ledger)
    await ledger.close()

    result = await verify_run(RUN_ID, db_path=str(tmp_path / "unsigned.db"), hmac_key=KEY)

    assert (result["ok"], result["signed"], result["entries"]) == (True, False, ENTRIES)


async def test_status_changes_after_creation_do_not_break_the_run_entry(ledger_file):
    # runs.status, turn_id and pending_action legitimately change; the run entry hashed the row as inserted.
    db = sqlite3.connect(ledger_file)
    db.execute("UPDATE runs SET status = 'done', pending_action = '{}', turn_id = 'turn_2' WHERE id = ?", (RUN_ID,))
    db.execute("INSERT INTO turns (run_id, turn_id, base_sequence, created_at) VALUES (?, 'turn_2', 8, '2999-01-01T00:00:00+00:00')", (RUN_ID,))
    db.commit()
    db.close()

    assert (await verify(ledger_file))["ok"] is True


def _source(db: sqlite3.Connection, kind: str, ref_id: str) -> dict[str, Any]:
    if kind == "run":
        run = dict(db.execute("SELECT * FROM runs WHERE id = ?", (ref_id,)).fetchone())
        return {**run, "status": "running", "pending_action": None, "turn_id": "turn_1"}
    if kind == "event":
        row = db.execute(
            "SELECT run_id, sequence, type, payload_json, received_at FROM events WHERE run_id = ? AND sequence = ?", (RUN_ID, int(ref_id))
        ).fetchone()
    elif kind == "approval":
        row = db.execute(
            "SELECT run_id, tool_name, arguments_json, decision, approver, decided_at, result FROM approvals WHERE id = ?", (int(ref_id),)
        ).fetchone()
    else:
        row = db.execute("SELECT run_id, kind, payload_json, created_at FROM notes WHERE id = ?", (int(ref_id),)).fetchone()
    return dict(row)
