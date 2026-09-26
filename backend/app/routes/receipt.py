import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

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


@router.get("/runs/{run_id}/receipt")
async def get_receipt(run_id: str, request: Request) -> JSONResponse:
    state = _state(request.app)
    cached = state.cache.get(run_id)
    if cached is not None and cached[0] > clock():
        return JSONResponse(cached[2], status_code=cached[1])

    # One computation per run at a time, so a settled receipt is appended exactly once.
    async with state.locks.setdefault(run_id, asyncio.Lock()):
        status_code, body = await _resolve(run_id, request, state)
    state.cache[run_id] = (clock() + CACHE_SECONDS, status_code, body)
    return JSONResponse(body, status_code=status_code)


async def _resolve(run_id: str, request: Request, state: _ReceiptState) -> tuple[int, dict[str, Any]]:
    app_state = request.app.state
    ledger = app_state.ledger
    settings = get_settings()

    await ledger.flush()
    run = await ledger.get_run(run_id)
    if run is None:
        return 404, {"detail": f"Run {run_id} not found"}
    if run["status"] not in FINAL_STATUSES:
        return 409, {"reason": "run not finished"}

    stored = await ledger.get_notes(run_id, kind=NOTE_RECEIPT)
    if stored:
        return 200, stored[0]["payload"]

    receipt = await build_receipt(run_id, ledger=ledger, http=app_state.http_client, settings=settings)
    if receipt is None:
        return 404, {"detail": f"Run {run_id} not found"}

    if receipt.source == "events" and gateway_configured(settings):
        first = state.first_attempt.setdefault(run_id, clock())
        if clock() - first < SETTLE_SECONDS:
            return 202, {"status": "pending"}

    body = receipt.model_dump(mode="json")
    await ledger.append_note(run_id, NOTE_RECEIPT, body)
    state.first_attempt.pop(run_id, None)
    return 200, body
