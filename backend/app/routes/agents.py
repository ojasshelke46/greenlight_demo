from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.agents import AgentRegistry
from app.auth import require_api_key

router = APIRouter(tags=["agents"], dependencies=[Depends(require_api_key)])


class AgentResponse(BaseModel):
    role: str
    name: str
    model: str | None
    found: bool
    # Whether a user can start this agent from the chat; the others are started by Greenlight.
    chat: bool


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(request: Request) -> list[AgentResponse]:
    registry: AgentRegistry = request.app.state.agents
    # Cached after the first success; only agents not found yet are looked up again.
    await registry.resolve(request.app.state.trueforge_client)
    return [AgentResponse(**info.as_dict()) for info in registry.all()]
