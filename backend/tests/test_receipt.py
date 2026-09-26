import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import app.gateway as gateway_module
import app.routes.receipt as receipt_routes
from app.config import get_settings
from app.event_types import NOTE_RECEIPT
from app.gateway import CallUsage, RunWindow, fetch_run_calls
from app.main import create_app

AUTH = {"Authorization": "Bearer test-key"}
RUN_ID = "run_receipt"
ADVISORY = "GHSA-r683-j2x4-v87g"
GATEWAY_URL = "https://tfy.example.com"


def exec_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "exec", "arguments": json.dumps({"command": command, "intent": "check"})},
        "tool_info": {"type": "truefoundry-system", "name": "exec"},
    }


def exec_response(call_id: str, output: str) -> dict:
    content = json.dumps({"success": True, "response": {"exitCode": 0, "result": output}})
    return {"type": "tool.response", "id": f"resp_{call_id}", "tool_call_id": call_id, "content": content}


def model_message(message_id: str, usage: tuple[int, int], tool_calls: list[dict] | None = None) -> dict:
    event = {
        "type": "model.message",
        "id": message_id,
        "thread_id": "main",
        "created_at": "t",
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "cache_read_tokens": 0, "input_tokens_breakdown": {}},
    }
    if tool_calls:
        event["tool_calls"] = tool_calls
    return event


USAGES = [(1000, 50), (2000, 80), (2500, 60), (3000, 120)]
COSTS = [0.0011, 0.0023, 0.0027, 0.0039]
SCRIPT = [
    {"type": "turn.created", "id": "ev_0", "turn_id": "turn_1"},
    model_message("m1", USAGES[0], [exec_call("c1", "npm audit --json")]),
    exec_response("c1", f"node-fetch  <2.6.7\nSeverity: high\nhttps://github.com/advisories/{ADVISORY}"),
    model_message("m2", USAGES[1], [exec_call("c2", "npm install node-fetch@3")]),
    exec_response("c2", "added 3 packages"),
    model_message("m3", USAGES[2], [exec_call("c3", "npm audit")]),
    exec_response("c3", "found 0 vulnerabilities"),
    model_message("m4", USAGES[3]),
    {"type": "turn.done", "id": "ev_9", "state": {"status": "done"}},
]
DURATION_SECONDS = 42.0


def model_span(usage: tuple[int, int], cost: float | None) -> dict:
    attributes = {
        "tfy.span_type": "Model",
        "tfy.model.name": "greenlight-model",
        "tfy.model.metric.input_tokens": usage[0],
        "tfy.model.metric.output_tokens": usage[1],
    }
    if cost is not None:
        attributes["tfy.model.metric.cost_in_usd"] = cost
    return {"spanName": "Model: greenlight-model", "spanAttributes": attributes}


RUN_SPANS = [model_span(usage, cost) for usage, cost in zip(USAGES, COSTS)]
# Other traffic on the same key inside the window; it matches none of the run's calls.
STRANGER = model_span((777, 7), 0.5)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Gateway:
    """Mock spans query endpoint. Responses are served in turn and the last one repeats."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def spans_page(spans: list[dict], next_token: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"data": spans, "pagination": {"nextPageToken": next_token} if next_token else {}})


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(receipt_routes, "clock", fake)
    return fake


@pytest.fixture
def gateway_env(monkeypatch):
    monkeypatch.setenv("TFY_GATEWAY_BASE_URL", GATEWAY_URL)
    monkeypatch.setenv("TFY_GATEWAY_API_KEY", "tfy-key")
    get_settings.cache_clear()


@pytest.fixture
def no_gateway_env(monkeypatch):
    # Empty values mean "not set", and they win over anything in a local .env file.
    monkeypatch.setenv("TFY_GATEWAY_BASE_URL", "")
    monkeypatch.setenv("TFY_GATEWAY_API_KEY", "")
    get_settings.cache_clear()


@asynccontextmanager
async def api(gateway: Gateway, *, status: str = "done", pr: bool = True):
    app = create_app()
    async with app.router.lifespan_context(app):
        ledger = app.state.ledger
        await ledger.create_run(run_id=RUN_ID, repo="acme/widgets", mode="ship", via_fork=False, session_id="s", turn_id="turn_1")
        created = datetime.fromisoformat((await ledger.get_run(RUN_ID))["created_at"])
        for sequence, event in enumerate(SCRIPT, start=1):
            received = created + timedelta(seconds=DURATION_SECONDS * sequence / len(SCRIPT))
            ledger.append_event(RUN_ID, sequence, event["type"], json.dumps(event), received.isoformat())
        ledger.set_status(RUN_ID, status)
        await ledger.flush()
        if pr:
            await ledger.append_note(RUN_ID, "pr", {"url": "https://github.com/acme/widgets/pull/7", "number": 7, "via_fork": False, "source": "tool_response"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as gateway_http:
            app.state.http_client = gateway_http
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client, app


async def get_receipt(client: AsyncClient) -> httpx.Response:
    return await client.get(f"/runs/{RUN_ID}/receipt")


async def test_full_gateway_data(gateway_env, clock):
    gateway = Gateway(spans_page(RUN_SPANS[:2] + [STRANGER], next_token="page2"), spans_page(RUN_SPANS[2:]))

    async with api(gateway) as (client, app):
        response = await get_receipt(client)
        notes = await app.state.ledger.get_notes(RUN_ID, kind=NOTE_RECEIPT)

    cost = round(sum(COSTS), 6)
    assert response.status_code == 200
    assert response.json() == {
        "advisories_fixed": 1,
        "model_calls": 4,
        "input_tokens": sum(u[0] for u in USAGES),
        "output_tokens": sum(u[1] for u in USAGES),
        "cost_usd": cost,
        "cost_inr": round(cost * 83.0, 2),
        "duration_seconds": DURATION_SECONDS,
        "trace_url": None,
        "source": "gateway",
    }
    assert [note["payload"] for note in notes] == [response.json()]

    first, second = gateway.requests
    assert str(first.url) == f"{GATEWAY_URL}/api/svc/v1/spans/query"
    assert first.headers["Authorization"] == "Bearer tfy-key"
    body = json.loads(first.content)
    assert body["dataRoutingDestination"] == "default"
    assert body["filters"] == [{"spanAttributeKey": "tfy.span_type", "operator": "EQUAL", "value": "Model"}]
    assert "startTime" in body and "endTime" in body and "pageToken" not in body
    assert json.loads(second.content)["pageToken"] == "page2"


async def test_gateway_without_cost_reports_null_cost(gateway_env, clock):
    spans = [model_span(usage, None if i == 0 else cost) for i, (usage, cost) in enumerate(zip(USAGES, COSTS))]

    async with api(Gateway(spans_page(spans))) as (client, _):
        body = (await get_receipt(client)).json()

    assert body["source"] == "gateway"
    assert body["input_tokens"] == sum(u[0] for u in USAGES)
    assert body["cost_usd"] is None and body["cost_inr"] is None


async def test_gateway_down_falls_back_to_events_after_settling(gateway_env, clock):
    gateway = Gateway(httpx.ConnectError("gateway down"))

    async with api(gateway) as (client, app):
        first = await get_receipt(client)
        clock.now += receipt_routes.SETTLE_SECONDS + 1
        settled = await get_receipt(client)
        notes = await app.state.ledger.get_notes(RUN_ID, kind=NOTE_RECEIPT)

    assert first.status_code == 202
    assert first.json() == {"status": "pending"}
    assert settled.status_code == 200
    assert settled.json() == {
        "advisories_fixed": 1,
        "model_calls": 4,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "cost_inr": None,
        "duration_seconds": DURATION_SECONDS,
        "trace_url": None,
        "source": "events",
    }
    assert len(notes) == 1


async def test_gateway_unconfigured_settles_on_events_at_once(no_gateway_env, clock):
    gateway = Gateway(spans_page(RUN_SPANS))

    async with api(gateway) as (client, _):
        response = await get_receipt(client)

    assert response.status_code == 200
    assert response.json()["source"] == "events"
    assert response.json()["model_calls"] == 4
    assert response.json()["cost_usd"] is None
    assert gateway.requests == []


async def test_gateway_lagging_then_succeeding(gateway_env, clock):
    # First the spans are not logged yet, then only some are, then all of them are.
    gateway = Gateway(spans_page([]), spans_page(RUN_SPANS[:2]), spans_page(RUN_SPANS))

    async with api(gateway) as (client, app):
        first = await get_receipt(client)
        cached = await get_receipt(client)
        clock.now += receipt_routes.CACHE_SECONDS + 1
        partial = await get_receipt(client)
        clock.now += receipt_routes.CACHE_SECONDS + 1
        final = await get_receipt(client)
        notes = await app.state.ledger.get_notes(RUN_ID, kind=NOTE_RECEIPT)

    assert first.status_code == cached.status_code == partial.status_code == 202
    assert len(gateway.requests) == 3  # the cached response did not hit the gateway
    assert final.status_code == 200
    assert final.json()["source"] == "gateway"
    assert final.json()["model_calls"] == 4
    assert len(notes) == 1


async def test_second_request_reads_the_stored_receipt(gateway_env, clock):
    gateway = Gateway(spans_page(RUN_SPANS))

    async with api(gateway) as (client, app):
        first = await get_receipt(client)
        clock.now += receipt_routes.CACHE_SECONDS + 1
        second = await get_receipt(client)
        notes = await app.state.ledger.get_notes(RUN_ID, kind=NOTE_RECEIPT)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(notes) == 1
    assert len(gateway.requests) == 1


async def test_unfinished_run_returns_409(gateway_env, clock):
    gateway = Gateway(spans_page(RUN_SPANS))

    async with api(gateway, status="awaiting_approval") as (client, app):
        response = await get_receipt(client)
        notes = await app.state.ledger.get_notes(RUN_ID, kind=NOTE_RECEIPT)

    assert response.status_code == 409
    assert response.json() == {"reason": "run not finished"}
    assert notes == []
    assert gateway.requests == []


async def test_fetch_run_calls_gives_up_on_a_hanging_gateway(gateway_env, monkeypatch):
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return spans_page(RUN_SPANS)

    monkeypatch.setattr(gateway_module, "TIMEOUT_SECONDS", 0.05)
    window = RunWindow(start=datetime.now(timezone.utc), end=datetime.now(timezone.utc), calls=[CallUsage(*USAGES[0])])
    async with httpx.AsyncClient(transport=httpx.MockTransport(hang)) as http:
        started = asyncio.get_running_loop().time()
        result = await fetch_run_calls(window, http, get_settings())

    assert result is None
    assert asyncio.get_running_loop().time() - started < 1


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, text="not json"), httpx.Response(200, json={"unexpected": []})],
)
async def test_fetch_run_calls_never_raises_on_bad_responses(gateway_env, response):
    window = RunWindow(start=datetime.now(timezone.utc), end=datetime.now(timezone.utc), calls=[CallUsage(*USAGES[0])])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        assert await fetch_run_calls(window, http, get_settings()) is None


async def test_unknown_run_is_404(gateway_env, clock):
    async with api(Gateway(spans_page([]))) as (client, _):
        response = await client.get("/runs/nope/receipt")

    assert response.status_code == 404


async def test_receipt_requires_api_key(gateway_env, clock):
    async with api(Gateway(spans_page([]))) as (client, _):
        response = await client.get(f"/runs/{RUN_ID}/receipt", headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


async def test_a_run_without_a_pr_fixed_nothing(gateway_env, clock):
    gateway = Gateway(httpx.ConnectError("gateway down"))

    async with api(gateway, pr=False) as (client, _):
        await get_receipt(client)
        clock.now += receipt_routes.SETTLE_SECONDS + 1
        body = (await get_receipt(client)).json()

    assert body["advisories_fixed"] == 0
    assert body["model_calls"] == 4
