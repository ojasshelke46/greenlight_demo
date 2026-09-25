import logging
import re
import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.log import run_id_var

logger = logging.getLogger("app.request")

_RUN_PATH = re.compile(r"^/runs/([^/]+)")


class RequestContextMiddleware:
    """Tags every log line with the run id, logs one line per request, and turns unhandled errors into JSON 500s.

    Pure ASGI rather than BaseHTTPMiddleware so SSE responses pass straight through without buffering.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        match = _RUN_PATH.match(scope["path"])
        token = run_id_var.set(match.group(1) if match else None)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("Unhandled error")
            if not response_started:
                await JSONResponse({"detail": "Internal server error"}, status_code=500)(scope, receive, send)
        finally:
            logger.log(
                logging.WARNING if status_code >= 500 else logging.INFO,
                "%s %s %s",
                scope["method"],
                scope["path"],
                status_code,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            run_id_var.reset(token)
