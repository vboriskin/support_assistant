"""Health / readiness."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from adapters.vector_store.base import VectorStore
from api.dependencies import vector_store_client

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    vs: Annotated[VectorStore, Depends(vector_store_client)],
) -> dict[str, str]:
    return {
        "status": "ok",
        "vector_store": "ok" if await vs.health() else "fail",
    }
