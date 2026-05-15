"""FastAPI-приложение.

Точка входа: ``uvicorn api.main:app --reload``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.dependencies import (
    embeddings_client,
    llm_client,
    text_search_client,
    vector_store_client,
)
from api.errors import register_error_handlers
from api.middleware import configure_middleware
from api.routes import (
    alerts,
    assistant,
    audit,
    categorize,
    conversations,
    csrf,
    diag,
    evals,
    fewshot,
    health,
    ingest,
    kb,
    pii,
    prompts,
    settings as settings_route,
    stale,
    stats,
    tickets,
    weak,
)
from config.logging import configure_logging, get_logger
from config.settings import Settings, get_settings
from db.engine import dispose_engine

UI_ROOT = Path(__file__).resolve().parent.parent / "ui"

logger = get_logger("api.main")


def _ensure_dirs(settings: Settings) -> None:
    if settings.db.backend == "sqlite":
        settings.db.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    settings.embeddings.cache_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    _ensure_dirs(settings)

    # Прогрев схем индексов — иначе первый запрос словит ленивый CREATE.
    try:
        await vector_store_client().count()
        await text_search_client().count()
    except Exception as e:  # noqa: BLE001
        logger.warning("lifespan.warmup_failed", error=str(e))

    # Фоновый watcher алёртов (только если включён в settings)
    import asyncio as _asyncio

    alerts_task: _asyncio.Task | None = None
    if settings.alerts.enabled and settings.alerts.webhook_url:
        async def _alerts_loop() -> None:
            from db.engine import get_session_factory
            import httpx as _httpx
            from api.routes.alerts import compute_signals, _violations

            factory = get_session_factory()
            interval = max(60, settings.alerts.check_interval_sec)
            last_sent_violations: tuple[str, ...] = ()
            while True:
                try:
                    async with factory() as session:
                        sig = await compute_signals(session, window_minutes=60)
                        violations = _violations(sig, settings)
                    cur = tuple(violations)
                    if cur and cur != last_sent_violations:
                        async with _httpx.AsyncClient(timeout=5.0) as client:
                            payload = {
                                "text": "Support Assistant alerts:\n• " + "\n• ".join(violations),
                                "signals": sig,
                            }
                            await client.post(settings.alerts.webhook_url, json=payload)
                        last_sent_violations = cur
                    elif not cur:
                        last_sent_violations = ()
                except Exception as e:  # noqa: BLE001
                    logger.warning("alerts.loop_failed", error=str(e))
                await _asyncio.sleep(interval)

        alerts_task = _asyncio.create_task(_alerts_loop())

    logger.info(
        "lifespan.started",
        env=settings.app_env,
        db_backend=settings.db.backend,
        llm_provider=settings.llm.provider,
        alerts_enabled=settings.alerts.enabled,
    )
    try:
        yield
    finally:
        if alerts_task is not None:
            alerts_task.cancel()
            try:
                await alerts_task
            except Exception:  # noqa: BLE001
                pass
        try:
            await llm_client().aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning("lifespan.llm_close_failed", error=str(e))
        try:
            await embeddings_client().aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning("lifespan.embeddings_close_failed", error=str(e))
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Support Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env != "prod" else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    configure_middleware(app, settings)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(csrf.router, prefix="/api")
    app.include_router(assistant.router, prefix="/api")
    app.include_router(categorize.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(tickets.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(kb.router, prefix="/api")
    app.include_router(evals.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")
    app.include_router(weak.router, prefix="/api")
    app.include_router(stale.router, prefix="/api")
    app.include_router(pii.router, prefix="/api")
    app.include_router(prompts.router, prefix="/api")
    app.include_router(fewshot.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    app.include_router(settings_route.router, prefix="/api")
    app.include_router(diag.router, prefix="/api")

    # Статика UI. Для dev-стенда отдаём с Cache-Control: no-cache, чтобы
    # после правок кнопка «Обновить приложение» гарантированно подтянула
    # свежие JS/CSS. В prod при необходимости можно поменять на immutable+hash.
    class _NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path, scope):  # type: ignore[override]
            resp = await super().get_response(path, scope)
            try:
                resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
            except Exception:  # noqa: BLE001
                pass
            return resp

    if (UI_ROOT / "index.html").exists():
        app.mount("/ui/static", _NoCacheStaticFiles(directory=str(UI_ROOT)), name="ui_static")

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/{path:path}", include_in_schema=False)
        async def serve_ui(path: str = "") -> FileResponse:
            return FileResponse(str(UI_ROOT / "index.html"))

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"app": "support-assistant", "ui": "/ui", "docs": "/api/docs"}

    return app


app = create_app()
