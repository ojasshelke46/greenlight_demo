"""TrueFoundry AI Gateway request logs for one Greenlight run.

API shapes come only from docs/gateway_notes.md. Correlation is strategy (c) from those notes: a time
window around the run, Model spans only, then each span is matched one for one to the run's own
TrueForge model.message usage (same input and output token counts). Spans that do not match one of the
run's calls, e.g. other traffic on the same key in the window, are never counted.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

SPANS_QUERY_PATH = "/api/svc/v1/spans/query"
TIMEOUT_SECONDS = 5.0
# Absorbs clock skew between this host and the gateway; token matching keeps the wider window safe.
WINDOW_SLACK = timedelta(seconds=60)
PAGE_LIMIT = 200
MAX_PAGES = 20


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RunWindow:
    start: datetime
    end: datetime
    # One entry per TrueForge model.message that reported usage, in stream order.
    calls: list[CallUsage]


@dataclass(frozen=True)
class GatewayCall:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    model: str | None


def gateway_configured(settings: Settings) -> bool:
    return bool(settings.tfy_gateway_base_url and settings.tfy_gateway_api_key)


async def fetch_run_calls(run: RunWindow, http: httpx.AsyncClient, settings: Settings) -> list[GatewayCall] | None:
    """The run's gateway calls, [] when they are not (all) logged yet, None when the gateway is unconfigured
    or unreachable. Never raises."""
    if not gateway_configured(settings):
        return None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            spans = await _query_model_spans(run, http, settings)
    except Exception as exc:  # the receipt degrades to ledger events rather than failing
        logger.warning("Gateway logs unavailable: %s", type(exc).__name__)
        return None
    return _match(run.calls, spans)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


async def _query_model_spans(run: RunWindow, http: httpx.AsyncClient, settings: Settings) -> list[GatewayCall]:
    url = f"{settings.tfy_gateway_base_url.rstrip('/')}{SPANS_QUERY_PATH}"
    headers = {"Authorization": f"Bearer {settings.tfy_gateway_api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "dataRoutingDestination": "default",
        "startTime": _iso(run.start - WINDOW_SLACK),
        "endTime": _iso(run.end + WINDOW_SLACK),
        "filters": [{"spanAttributeKey": "tfy.span_type", "operator": "EQUAL", "value": "Model"}],
        "limit": PAGE_LIMIT,
        "sortDirection": "asc",
    }

    calls: list[GatewayCall] = []
    for _ in range(MAX_PAGES):
        response = await http.post(url, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        page = response.json()
        for span in page["data"]:
            call = _to_call(span.get("spanAttributes") or {})
            if call is not None:
                calls.append(call)
        next_token = (page.get("pagination") or {}).get("nextPageToken")
        if not next_token:
            break
        body["pageToken"] = next_token
    return calls


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_call(attributes: dict[str, Any]) -> GatewayCall | None:
    input_tokens = _number(attributes.get("tfy.model.metric.input_tokens"))
    output_tokens = _number(attributes.get("tfy.model.metric.output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    model = attributes.get("tfy.model.name")
    return GatewayCall(
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=_number(attributes.get("tfy.model.metric.cost_in_usd")),
        model=model if isinstance(model, str) else None,
    )


def _match(wanted: list[CallUsage], logged: list[GatewayCall]) -> list[GatewayCall]:
    """Pair every run call with one logged span of identical token counts; [] unless all of them pair."""
    if not wanted:
        return []
    remaining = list(logged)
    matched: list[GatewayCall] = []
    for usage in wanted:
        index = next(
            (
                i
                for i, call in enumerate(remaining)
                if call.input_tokens == usage.input_tokens and call.output_tokens == usage.output_tokens
            ),
            None,
        )
        if index is None:
            return []
        matched.append(remaining.pop(index))
    return matched
