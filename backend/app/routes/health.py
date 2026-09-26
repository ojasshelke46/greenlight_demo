from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    trueforge_client = request.app.state.trueforge_client
    trueforge_reachable = await trueforge_client.health()
    agents = {info.role: {"name": info.name, "found": info.found} for info in request.app.state.agents.all()}
    return {"ok": True, "trueforge_reachable": trueforge_reachable, "agents": agents}
