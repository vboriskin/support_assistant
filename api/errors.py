"""Глобальные exception-handler'ы FastAPI.

Все error-ответы — единый JSON-формат
``{"error": "<code>", "message": "<human>", "details": {...}}``,
описанный в ``docs/13-API.md`` §"Контракт ошибок".
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from adapters.llm.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from config.logging import get_logger
from core.redact import redact_secrets

logger = get_logger("api.errors")


def _body(error: str, message: str, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {"error": error, "message": message}
    if extra:
        body["details"] = extra
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LLMRateLimitError)
    async def _llm_rate(_: Request, exc: LLMRateLimitError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse(
            status_code=429,
            content=_body("llm_rate_limit", "LLM rate limit", retry_after=exc.retry_after),
            headers=headers,
        )

    @app.exception_handler(LLMAuthError)
    async def _llm_auth(_: Request, exc: LLMAuthError) -> JSONResponse:
        logger.warning("llm.auth_failed", error=redact_secrets(str(exc)))
        return JSONResponse(
            status_code=503,
            content=_body(
                "llm_auth_failed",
                "LLM authentication failed, contact administrator",
            ),
        )

    @app.exception_handler(LLMTimeoutError)
    async def _llm_timeout(_: Request, exc: LLMTimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=504,
            content=_body("llm_timeout", "LLM request timed out"),
        )

    @app.exception_handler(LLMError)
    async def _llm_generic(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=_body("llm_error", redact_secrets(str(exc))[:300]),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_body("validation", "Request validation failed", errors=exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _fallback(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("api.unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_body("internal_error", "Internal server error"),
        )
