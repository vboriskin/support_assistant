"""POST /api/categorize."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.dependencies import categorizer_service, get_user_id
from api.schemas import CategorizeBody
from services.categorizer import CategorizationResult, CategorizeRequest, CategorizerService

router = APIRouter(tags=["categorize"])


@router.post("/categorize", response_model=CategorizationResult)
async def categorize(
    body: CategorizeBody,
    _user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[CategorizerService, Depends(categorizer_service)],
) -> CategorizationResult:
    return await service.categorize(
        CategorizeRequest(
            subject=body.subject,
            description=body.description,
            channel=body.channel,
            author_role=body.author_role,
            attachments=body.attachments,
        )
    )
