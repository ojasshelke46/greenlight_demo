from fastapi import APIRouter

from app.features.fleet.router import router as fleet_router
from app.features.policy.router import router as policy_router
from app.features.proof.router import router as proof_router
from app.routes.access import router as access_router
from app.routes.agents import router as agents_router
from app.routes.health import router as health_router
from app.routes.runs import router as runs_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(access_router)
api_router.include_router(agents_router)
api_router.include_router(runs_router)
api_router.include_router(proof_router)
api_router.include_router(policy_router)
api_router.include_router(fleet_router)
