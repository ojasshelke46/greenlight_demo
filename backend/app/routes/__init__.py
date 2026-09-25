from fastapi import APIRouter

from app.routes.access import router as access_router
from app.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(access_router)
