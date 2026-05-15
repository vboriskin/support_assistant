"""GET /api/csrf — выдаёт CSRF-токен для текущего ``X-User-Id``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.dependencies import get_user_id
from core.security import generate_csrf_token

router = APIRouter(tags=["csrf"])


@router.get("/csrf")
async def get_csrf(user_id: Annotated[str, Depends(get_user_id)]) -> dict[str, str]:
    return {"token": generate_csrf_token(user_id)}
