from fastapi import APIRouter, Depends

from app.auth import require_api_key

router = APIRouter(prefix="/features/fleet", tags=["fleet"], dependencies=[Depends(require_api_key)])
