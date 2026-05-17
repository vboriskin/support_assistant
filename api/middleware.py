"""Middleware-слои API.

- :class:`RateLimitMiddleware` — in-memory скользящее окно per ``X-User-Id+IP``.
- :class:`AuditLogMiddleware` — структурный лог запроса (без тел — PII).
- :class:`CSRFMiddleware` — обязательный ``X-CSRF-Token`` на ``POST/PUT/DELETE``
  для ``/api/*``. Токен — ``GET /api/csrf``. Управляется ``SECURITY_CSRF_ENABLED``.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.logging import get_logger
from config.settings import Settings
from core.security import verify_csrf_token

logger = get_logger("api.middleware")

DispatchT = Callable[[Request], Awaitable[Response]]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory rate-limit. Применяется только к `/api/*`."""

    def __init__(self, app, *, limit: int, window_sec: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_sec
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: DispatchT) -> Response:
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        user = request.headers.get("X-User-Id", "anonymous")
        ip = request.client.host if request.client else "unknown"
        key = f"{user}:{ip}"
        now = time()
        q = self._hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limit", "message": "Too many requests"},
                headers={"Retry-After": str(self.window)},
            )
        q.append(now)
        return await call_next(request)


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: DispatchT) -> Response:
        t0 = time()
        response = await call_next(request)
        latency_ms = int((time() - t0) * 1000)
        logger.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=latency_ms,
            user=request.headers.get("X-User-Id", "anonymous"),
        )
        return response


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# CSRF не требуется для эндпоинта выдачи токена и health/ready.
_CSRF_EXEMPT_PATHS = frozenset({"/api/csrf"})


class CSRFMiddleware(BaseHTTPMiddleware):
    """Проверяет ``X-CSRF-Token`` для unsafe-методов на ``/api/*``-путях."""

    async def dispatch(self, request: Request, call_next: DispatchT) -> Response:
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or path in _CSRF_EXEMPT_PATHS:
            return await call_next(request)
        user = request.headers.get("X-User-Id", "anonymous")
        token = request.headers.get("X-CSRF-Token", "")
        if not verify_csrf_token(user, token):
            return JSONResponse(
                status_code=403,
                content={"error": "csrf_invalid", "message": "CSRF token missing or invalid"},
            )
        return await call_next(request)


_AUDITED_UNSAFE = re.compile(
    r"^/api/(kb|ingest|evals|tickets/[^/]+/reindex|conversations(/[^/]+/feedback)?|"
    r"prompts|fewshot|alerts|pii)"
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _audit_action(method: str, path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1:
        return f"{method} {path}".strip()
    head = parts[1] if parts[0] == "api" else parts[0]
    rest = "/".join(parts[2:]) if parts[0] == "api" else "/".join(parts[1:])
    return f"{method.lower()}:{head}{':' + rest if rest else ''}"


def _audit_target(path: str) -> tuple[str | None, str | None]:
    """Грубо вытащить (target_type, target_id) из вида /api/<type>/<id>/..."""
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "api":
        return None, None
    target_type = parts[1]
    target_id = parts[2] if len(parts) > 2 and parts[2] not in {"jobs", "runs", "cases", "bulk", "csv"} else None
    return target_type, target_id


class DBAuditMiddleware(BaseHTTPMiddleware):
    """Пишет в таблицу ``audit_log`` записи об unsafe-вызовах в чувствительных
    путях. Аккуратно: если БД недоступна — просто игнорируем (audit не должен
    валить запрос)."""

    async def dispatch(self, request: Request, call_next: DispatchT) -> Response:
        method = request.method
        path = request.url.path
        response = await call_next(request)
        if method in _SAFE_METHODS or not _AUDITED_UNSAFE.match(path):
            return response
        try:
            from db.engine import get_session_factory
            from db.models import AuditLog

            target_type, target_id = _audit_target(path)
            factory = get_session_factory()
            async with factory() as session:
                session.add(
                    AuditLog(
                        id=str(uuid.uuid4()),
                        user_id=request.headers.get("X-User-Id"),
                        action=_audit_action(method, path),
                        target_type=target_type,
                        target_id=target_id,
                        method=method,
                        path=path,
                        status=response.status_code,
                        created_at=_now(),
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning("audit.persist_failed", error=str(e), path=path)
        return response


def configure_middleware(app, settings: Settings) -> None:
    """Регистрирует middleware в нужном порядке.

    Audit идёт *снаружи* rate-limit и CSRF, чтобы фиксировать и 429/403-ответы.
    """
    if settings.security.csrf_enabled:
        app.add_middleware(CSRFMiddleware)
    app.add_middleware(RateLimitMiddleware, limit=settings.security.rate_limit_per_minute)
    app.add_middleware(AuditLogMiddleware)
    if getattr(settings.security, "db_audit_enabled", True):
        app.add_middleware(DBAuditMiddleware)
