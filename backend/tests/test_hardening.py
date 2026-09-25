import asyncio
import io
import json
import logging
from contextlib import contextmanager

from httpx import ASGITransport, AsyncClient

from app.cli import SERVER_OPTIONS
from app.log import JsonFormatter, configure_logging, run_id_var
from app.main import create_app
from app.trueforge import TurnStreamGone
from tests.test_runs import AUTH, FakeTrueForge, api, start_run

BOT_TOKEN = "ghp_" + "a1B2c3D4" * 5


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict] = []
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@contextmanager
def captured():
    """Attach after create_app/configure_logging, since dictConfig drops existing root handlers."""
    capture = Capture()
    logging.getLogger().addHandler(capture)
    try:
        yield capture.lines
    finally:
        logging.getLogger().removeHandler(capture)


def test_log_lines_are_json_with_run_id():
    configure_logging()
    with captured() as logs:
        token = run_id_var.set("run_123")
        logging.getLogger("app.test").info("hello %s", "world", extra={"step": 2})
        run_id_var.reset(token)
        logging.getLogger("app.test").info("outside a run")

    assert logs[0]["run_id"] == "run_123"
    assert logs[0]["message"] == "hello world"
    assert logs[0]["level"] == "info" and logs[0]["logger"] == "app.test" and logs[0]["step"] == 2
    assert "ts" in logs[0]
    assert logs[1]["run_id"] is None


def test_logs_never_contain_secrets():
    configure_logging(secrets=("super-secret-api-key", BOT_TOKEN))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.secrets")
    logger.addHandler(handler)
    try:
        logger.warning("key super-secret-api-key and %s", BOT_TOKEN, extra={"auth": "Bearer abc.def-123"})
        try:
            raise RuntimeError(f"GitHub rejected {BOT_TOKEN}")
        except RuntimeError:
            logger.exception("call failed with github_pat_" + "Z" * 30)
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    for leaked in ("super-secret-api-key", BOT_TOKEN, "abc.def-123", "github_pat_ZZZ"):
        assert leaked not in output
    assert output.count("[REDACTED]") >= 5
    assert all(json.loads(line) for line in output.splitlines())


async def test_request_logs_carry_run_id_from_path_and_new_runs():
    async with api(FakeTrueForge()) as (client, _):
        with captured() as logs:
            await client.get("/runs/abc-123")
            run_id = await start_run(client)
            await client.get(f"/runs/{run_id}/events")

    requests = [line for line in logs if line["logger"] == "app.request"]
    assert {"path": "/runs/abc-123", "status": 404, "run_id": "abc-123"}.items() <= requests[0].items()
    assert requests[1]["path"] == "/runs" and requests[1]["status"] == 201 and requests[1]["run_id"] == run_id
    assert requests[2]["run_id"] == run_id and requests[2]["duration_ms"] >= 0
    assert all(line["run_id"] for line in requests)


async def test_pump_logs_carry_run_id():
    class GoneTrueForge(FakeTrueForge):
        async def stream_turn(self, session_id, turn_id, after_sequence=None):
            raise TurnStreamGone("gone")
            yield

    async with api(GoneTrueForge()) as (client, _):
        with captured() as logs:
            run_id = await start_run(client)
            for _ in range(50):
                if any(line["logger"] == "app.runs" for line in logs):
                    break
                await asyncio.sleep(0.01)

    pump_lines = [line for line in logs if line["logger"] == "app.runs"]
    assert pump_lines and all(line["run_id"] == run_id for line in pump_lines)


async def test_unhandled_error_is_clean_json_500_with_cors():
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("internal detail that must not leak")

    async with app.router.lifespan_context(app):
        with captured() as logs:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/boom", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "internal detail" not in response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    errors = [line for line in logs if line["message"] == "Unhandled error"]
    assert errors and "internal detail that must not leak" in errors[0]["exc"]


async def test_validation_errors_are_clean_and_do_not_echo_input():
    async with api(FakeTrueForge()) as (client, _):
        response = await client.post("/runs", json={"repo": "https://github.com/acme/widgets", "mode": "yolo"})

    assert response.status_code == 422
    body = response.json()
    assert body["detail"].startswith("Invalid request: body.mode:")
    assert body["errors"][0]["loc"] == "body.mode"
    assert "yolo" not in response.text


async def test_http_errors_keep_detail_shape():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/nope")).json() == {"detail": "Not Found"}
            unauthorized = await client.get("/runs/x")
            authorized = await client.get("/runs/x", headers=AUTH)

    assert unauthorized.status_code == 401 and unauthorized.json() == {"detail": "Invalid or missing API key"}
    assert authorized.status_code == 404 and authorized.json() == {"detail": "Run x not found"}


def test_server_options():
    assert SERVER_OPTIONS["loop"] == "uvloop"
    assert SERVER_OPTIONS["http"] == "httptools"
    assert SERVER_OPTIONS["workers"] == 1
    assert SERVER_OPTIONS["proxy_headers"] is True
