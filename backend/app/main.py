from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.github import GitHubClient
from app.ledger import Ledger
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


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Greenlight", lifespan=lifespan)

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
