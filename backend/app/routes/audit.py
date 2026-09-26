from fastapi import APIRouter, Depends

from app.auth import require_api_key

router = APIRouter(tags=["audit"], dependencies=[Depends(require_api_key)])
