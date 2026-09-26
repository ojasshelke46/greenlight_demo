import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app import orchestrator
from app.auth import require_api_key
from app.config import get_settings
from app.event_types import NOTE_RECEIPT
from app.gateway import gateway_configured
from app.receipt import build_receipt

router = APIRouter(tags=["receipt"], dependencies=[Depends(require_api_key)])

FINAL_STATUSES = {"done"}
# Gateway logs can lag the run; keep asking the gateway for this long before settling on ledger events.
SETTLE_SECONDS = 20.0
CACHE_SECONDS = 5.0

clock = time.monotonic


@dataclass
class _ReceiptState:
    first_attempt: dict[str, float] = field(default_factory=dict)
    cache: dict[str, tuple[float, int, dict[str, Any]]] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)


def _state(app: FastAPI) -> _ReceiptState:
    state = getattr(app.state, "receipt_state", None)
    if state is None:
        state = app.state.receipt_state = _ReceiptState()
    return state


def forget_receipt(app: FastAPI, run_id: str) -> None:
    """Drop the cached answer when the run takes another turn, so its next receipt covers that turn too."""
    state = _state(app)
    state.cache.pop(run_id, None)
    state.first_attempt.pop(run_id, None)


@router.get("/runs/{run_id}/receipt")
async def get_receipt(run_id: str, request: Request) -> JSONResponse:
    status_code, body = await compute_receipt(request.app, run_id)
    return JSONResponse(body, status_code=status_code)


async def compute_receipt(app: FastAPI, run_id: str) -> tuple[int, dict[str, Any]]:
    """The receipt as (status code, body). Shared by the route and the backend's own settle loop."""
    state = _state(app)
    cached = state.cache.get(run_id)
    if cached is not None and cached[0] > clock():
        return cached[1], cached[2]

    # One computation per run at a time, so a settled receipt is appended exactly once.
    async with state.locks.setdefault(run_id, asyncio.Lock()):
        status_code, body = await _resolve(run_id, app, state)
    state.cache[run_id] = (clock() + CACHE_SECONDS, status_code, body)
    return status_code, body


async def _resolve(run_id: str, app: FastAPI, state: _ReceiptState) -> tuple[int, dict[str, Any]]:
    app_state = app.state
    ledger = app_state.ledger
    settings = get_settings()

    await ledger.flush()
    run = await ledger.get_run(run_id)
    if run is None:
        return 404, {"detail": f"Run {run_id} not found"}
    if run["status"] not in FINAL_STATUSES:
        return 409, {"reason": "run not finished"}

    # A receipt covers the run up to when it was settled; a turn taken after it (the run was continued) needs a new one.
    stored = await ledger.get_notes(run_id, kind=NOTE_RECEIPT)
    cursor = await ledger.db.execute("SELECT MAX(created_at) FROM turns WHERE run_id = ?", (run_id,))
    last_turn = (await cursor.fetchone())[0]
    if stored and (last_turn is None or stored[-1]["created_at"] >= last_turn):
        return 200, stored[-1]["payload"]

    receipt = await build_receipt(run_id, ledger=ledger, http=app_state.http_client, settings=settings)
    if receipt is None:
        return 404, {"detail": f"Run {run_id} not found"}

    if receipt.source == "events" and gateway_configured(settings):
        first = state.first_attempt.setdefault(run_id, clock())
        if clock() - first < SETTLE_SECONDS:
            return 202, {"status": "pending"}

    body = receipt.model_dump(mode="json")
    note = await ledger.append_note(run_id, NOTE_RECEIPT, body)
    state.first_attempt.pop(run_id, None)
    if (running := orchestrator.current()) is not None:
        running.receipt_stored(run_id, body, note["id"])
    return 200, body
