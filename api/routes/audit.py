"""Audit-log: GET /api/audit для UI «кто что делал».

Записи пишутся middleware'ом `DBAuditMiddleware` для unsafe-методов на
чувствительных путях. Этот роут только отдаёт их с фильтрами.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from api.dependencies import SessionDep
from db.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


def _to_dict(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "action": a.action,
        "target_type": a.target_type,
        "target_id": a.target_id,
        "method": a.method,
        "path": a.path,
        "status": a.status,
        "details": a.details_json,
        "created_at": a.created_at.isoformat(),
    }


@router.get("")
async def list_audit(
    session: SessionDep,
    user_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    items = (await session.execute(stmt)).scalars().all()
    return [_to_dict(a) for a in items]
