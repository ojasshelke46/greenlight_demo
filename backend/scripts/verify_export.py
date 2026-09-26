"""Recheck a Greenlight flight record export offline, with no database.

Recomputes every entry hash from its source row, checks every prev_hash link and the chain head, and
checks the head HMAC when LEDGER_HMAC_KEY is available (environment first, then backend/.env).

Usage (from backend/):
    uv run python scripts/verify_export.py greenlight_<run_id>.json
"""

import argparse
import json
import os
import sys

from app.chain import EXPORT_FORMAT, verify_export


def hmac_key() -> str | None:
    if os.environ.get("LEDGER_HMAC_KEY"):
        return os.environ["LEDGER_HMAC_KEY"]
    try:
        from app.config import get_settings

        return get_settings().ledger_hmac_key
    except Exception:  # no .env or incomplete settings: verify the hashes without the signature
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="JSON export from GET /runs/{id}/audit/export?format=json")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("format") != EXPORT_FORMAT:
        print(f"not a Greenlight flight record export (format {document.get('format')!r})")
        return 2

    key = hmac_key()
    result = verify_export(document, key)

    if result["ok"]:
        print(f"ok: run {document['run_id']}, {result['entries']} entries verified")
    elif result["first_broken_idx"] is not None:
        print(f"broken at entry {result['first_broken_idx']}: {result['reason']}")
    else:
        print(f"not verified: {result['reason']}")

    if result["hmac_checked"]:
        print("HMAC: checked against LEDGER_HMAC_KEY")
    elif not result["signed"]:
        print("HMAC: not checked, the run is unsigned")
    elif key is None:
        print("HMAC: not checked, LEDGER_HMAC_KEY is not set")
    else:
        print("HMAC: not checked, the chain failed before the head")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
