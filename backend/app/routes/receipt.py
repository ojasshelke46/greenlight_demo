from fastapi import APIRouter, Depends

from app.auth import require_api_key

router = APIRouter(tags=["receipt"], dependencies=[Depends(require_api_key)])
