"""Show the flight recorder catching a tampered approval, without touching the real ledger.

Copies the ledger database to a temporary file (the real one is opened read only), changes the approver
of the run's first approval in the copy, and runs verify_run against the copy.

Usage (from backend/):
    uv run python scripts/tamper_demo.py <run_id> [--db greenlight.db]
"""

import argparse
import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

from app.chain import verify_run


def copy_ledger(source: str, target: Path) -> None:
    # sqlite's backup API copies a consistent snapshot, WAL contents included; mode=ro forbids any write.
    real = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            real.backup(copy)
        finally:
            copy.close()
    finally:
        real.close()


def tamper(copy_path: Path, run_id: str) -> str | None:
    """Rewrite the approver of the run's first approval in the copy; returns the old approver."""
    db = sqlite3.connect(copy_path)
    try:
        row = db.execute("SELECT id, approver FROM approvals WHERE run_id = ? ORDER BY id LIMIT 1", (run_id,)).fetchone()
        if row is None:
            return None
        db.execute("DROP TRIGGER IF EXISTS approvals_no_update")
        db.execute("UPDATE approvals SET approver = ? WHERE id = ?", (f"{row[1]} (forged)", row[0]))
        db.commit()
        return row[1]
    finally:
        db.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id")
    parser.add_argument("--db", help="ledger database (default: LEDGER_PATH from settings)")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        from app.config import get_settings

        db_path = get_settings().ledger_path

    with tempfile.TemporaryDirectory(prefix="greenlight_tamper_") as workdir:
        copy_path = Path(workdir) / "ledger_copy.db"
        copy_ledger(db_path, copy_path)

        before = await verify_run(args.run_id, db_path=str(copy_path))
        print(f"before tampering: {'ok' if before['ok'] else before['reason']} ({before['entries']} entries)")
        if not before["ok"]:
            return 1

        approver = tamper(copy_path, args.run_id)
        if approver is None:
            print(f"run {args.run_id} has no approvals to tamper with")
            return 1
        print(f"tampered copy: approver {approver!r} rewritten")

        after = await verify_run(args.run_id, db_path=str(copy_path))
        if after["ok"]:
            print("NOT DETECTED: verification still passes")
            return 1
        print(f"detected: broken at entry {after['first_broken_idx']}: {after['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
