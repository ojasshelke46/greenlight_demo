from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.github import GitHubClient
from app.ledger import Ledger
from app.log import configure_logging
from app.middleware import RequestContextMiddleware
from app.routes import api_router
from app.runs import RunManager
from app.trueforge import TrueForgeClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as http_client:
        app.state.http_client = http_client
        app.state.trueforge_client = TrueForgeClient(
            http_client, settings.trueforge_base_url, settings.trueforge_agent_name
        )
        app.state.github_client = GitHubClient(http_client, settings)

        ledger = Ledger(settings.ledger_path)
        await ledger.init()
        app.state.ledger = ledger
        app.state.run_manager = RunManager(ledger, app.state.trueforge_client)

        try:
            yield
        finally:
            await app.state.run_manager.close()
            await ledger.close()


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default echoes the rejected input back; keep only where and why.
    errors = [{"loc": ".".join(str(part) for part in error["loc"]), "msg": error["msg"]} for error in exc.errors()]
    detail = "; ".join(f"{error['loc']}: {error['msg']}" for error in errors)
    return JSONResponse({"detail": f"Invalid request: {detail}", "errors": errors}, status_code=422)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(secrets=(settings.greenlight_api_key, settings.github_bot_token))

    app = FastAPI(title="Greenlight", lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    # Added before CORS so CORS stays outermost and error responses still carry CORS headers.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    return app


app = create_app()
