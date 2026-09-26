"""The audit routes, the JSON and Markdown exports, and the two scripts run as real processes."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.chain import verify_run
from app.config import get_settings
from app.main import create_app
from tests.test_chain_verify import APPROVAL_IDX, ENTRIES, RUN_ID, build_run

AUTH = {"Authorization": "Bearer test-key"}
KEY = "audit-test-key"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setenv("LEDGER_HMAC_KEY", KEY)
    get_settings.cache_clear()


@asynccontextmanager
async def api(with_receipt: bool = True):
    app = create_app()
    async with app.router.lifespan_context(app):
        await build_run(app.state.ledger, with_receipt=with_receipt)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
            yield client, app


def run_script(*args: str, key: str | None) -> subprocess.CompletedProcess:
    env = {**os.environ, "LEDGER_HMAC_KEY": key or ""}
    return subprocess.run([sys.executable, *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)


async def test_verify_route_reports_a_clean_signed_run():
    async with api() as (client, _):
        response = await client.get(f"/runs/{RUN_ID}/audit/verify")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "entries": ENTRIES, "signed": True, "first_broken_idx": None, "reason": None}


async def test_json_export_is_an_attachment_that_verifies_offline(tmp_path):
    async with api() as (client, _):
        response = await client.get(f"/runs/{RUN_ID}/audit/export", params={"format": "json"})

    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="greenlight_{RUN_ID}.json"'
    document = response.json()
    assert document["run"]["id"] == RUN_ID
    assert len(document["entries"]) == ENTRIES
    assert all(entry["source"] is not None for entry in document["entries"])
    assert document["verify"]["ok"] is True
    assert document["head_hmac"] == document["head"]["head_hmac"]

    export = tmp_path / "export.json"
    export.write_text(json.dumps(document))

    checked = run_script("scripts/verify_export.py", str(export), key=KEY)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert f"ok: run {RUN_ID}, {ENTRIES} entries verified" in checked.stdout
    assert "HMAC: checked against LEDGER_HMAC_KEY" in checked.stdout

    unkeyed = run_script("scripts/verify_export.py", str(export), key=None)
    assert unkeyed.returncode == 0
    assert "HMAC: not checked, LEDGER_HMAC_KEY is not set" in unkeyed.stdout


async def test_offline_verify_catches_an_edited_export(tmp_path):
    async with api() as (client, _):
        document = (await client.get(f"/runs/{RUN_ID}/audit/export", params={"format": "json"})).json()

    document["entries"][APPROVAL_IDX]["source"]["approver"] = "Mallory"
    export = tmp_path / "edited.json"
    export.write_text(json.dumps(document))

    checked = run_script("scripts/verify_export.py", str(export), key=KEY)
    assert checked.returncode == 1
    assert f"broken at entry {APPROVAL_IDX}: approval at entry {APPROVAL_IDX} was edited" in checked.stdout


async def test_markdown_export_is_a_readable_report():
    async with api() as (client, _):
        response = await client.get(f"/runs/{RUN_ID}/audit/export", params={"format": "md"})
        head = (await client.get(f"/runs/{RUN_ID}/audit/export", params={"format": "json"})).json()["head"]

    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="greenlight_{RUN_ID}.md"'
    assert response.headers["content-type"].startswith("text/markdown")
    report = response.text

    for line in ["- Mode: Ship it", "- Access: direct", "- Verification: Verified", f"- Entries: {ENTRIES}", "- Signature: signed"]:
        assert line in report
    assert "## Timeline" in report
    assert "| exec | npm audit --json | exit 1 |" in report
    assert "| github.create_pull_request | owner=acme, repo=widgets, title=Upgrade node-fetch | ok |" in report
    assert "| github.merge_pull_request | owner=acme, repo=widgets, pullNumber=7 | waiting for approval |" in report
    assert "| merge_pull_request | [#7](https://github.com/acme/widgets/pull/7) | approve | Shreyash |" in report
    assert "| accepted |" in report
    assert "## Receipt" in report and "- Model calls: 3" in report and "- Cost (INR): unavailable" in report
    assert f"Final entry hash: `{head['entry_hash']}`" in report
    assert f"Head HMAC: `{head['head_hmac']}`" in report
    # Summaries only: the 5000 character audit output is never dumped into the report.
    assert "x" * 200 not in report


async def test_markdown_omits_the_receipt_section_without_a_receipt():
    async with api(with_receipt=False) as (client, _):
        report = (await client.get(f"/runs/{RUN_ID}/audit/export", params={"format": "md"})).text

    assert "## Receipt" not in report
    assert "## Approvals" in report


async def test_legacy_run_reports_the_pre_flight_recorder_reason():
    async with api() as (client, app):
        await app.state.ledger.db.execute(
            "INSERT INTO runs (id, repo, mode, via_fork, session_id, turn_id, status, created_at)"
            " VALUES ('legacy', 'acme/old', 'pr_only', 1, 's', 't', 'done', '2026-01-01T00:00:00+00:00')"
        )
        await app.state.ledger.db.commit()
        verified = await client.get("/runs/legacy/audit/verify")
        report = (await client.get("/runs/legacy/audit/export", params={"format": "md"})).text

    assert verified.json()["reason"] == "created before flight recorder"
    assert verified.json()["ok"] is False
    assert "- Verification: Not verifiable: created before flight recorder" in report
    assert "- Access: fork" in report


async def test_unknown_run_and_bad_format():
    async with api() as (client, _):
        assert (await client.get("/runs/nope/audit/verify")).status_code == 404
        assert (await client.get("/runs/nope/audit/export")).status_code == 404
        assert (await client.get(f"/runs/{RUN_ID}/audit/export", params={"format": "pdf"})).status_code == 422
        assert (await client.get(f"/runs/{RUN_ID}/audit/verify", headers={"Authorization": "Bearer wrong"})).status_code == 401


async def test_tamper_demo_detects_the_forged_approver_and_leaves_the_real_ledger_alone():
    async with api():
        pass  # the run is built and the ledger closed cleanly
    db_path = get_settings().ledger_path
    before = hashlib.sha256(Path(db_path).read_bytes()).hexdigest()

    demo = run_script("scripts/tamper_demo.py", RUN_ID, "--db", db_path, key=KEY)

    assert demo.returncode == 0, demo.stdout + demo.stderr
    assert f"before tampering: ok ({ENTRIES} entries)" in demo.stdout
    assert f"detected: broken at entry {APPROVAL_IDX}: approval at entry {APPROVAL_IDX} was edited" in demo.stdout
    assert hashlib.sha256(Path(db_path).read_bytes()).hexdigest() == before
    real = sqlite3.connect(db_path)
    assert real.execute("SELECT approver FROM approvals WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == "Shreyash"
    real.close()
    assert (await verify_run(RUN_ID, db_path=db_path, hmac_key=KEY))["ok"] is True
