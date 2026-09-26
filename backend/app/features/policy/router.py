from fastapi import APIRouter, Depends

from app.auth import require_api_key

router = APIRouter(prefix="/features/policy", tags=["policy"], dependencies=[Depends(require_api_key)])
