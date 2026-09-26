import subprocess
import sys
from typing import Any

import uvicorn

from app.log import LOGGING_CONFIG

HOST = "0.0.0.0"
PORT = 8000

SERVER_OPTIONS: dict[str, Any] = {
    "loop": "uvloop",
    "http": "httptools",
    # Must stay 1: live runs, SSE subscribers and approval locks live in this process's memory.
    "workers": 1,
    # Trusts X-Forwarded-* only from FORWARDED_ALLOW_IPS (uvicorn default 127.0.0.1).
    "proxy_headers": True,
    "log_config": LOGGING_CONFIG,
}


def dev() -> None:
    # Without this, the ledger writing to its default ./*.db path inside backend/ triggers
    # its own reload, which restarts the server (and drops its in-memory runs) mid-write.
    uvicorn.run(
        "app.main:app", host=HOST, port=PORT, reload=True, reload_excludes=["*.db", "*.db-*"], **SERVER_OPTIONS
    )


def serve() -> None:
    uvicorn.run("app.main:app", host=HOST, port=PORT, **SERVER_OPTIONS)


def test() -> None:
    result = subprocess.run(["pytest", *sys.argv[1:]], check=False)
    sys.exit(result.returncode)
