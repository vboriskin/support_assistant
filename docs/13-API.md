# 13. API

FastAPI-приложение. Корень — `api/main.py`. Все роуты — в `api/routes/`. Префикс — `/api`.

## Структура приложения

`api/main.py`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config.settings import get_settings
from config.logging import setup_logging
from api.routes import (
    health, assistant, categorize, ingest,
    tickets, kb, conversations, evals, stats,
)
from api.middleware import RateLimitMiddleware, AuditLogMiddleware
from api.errors import register_error_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    # Создание директорий, прогрев модели эмбеддингов (опционально)
    yield
    # Cleanup: закрытие LLM/HTTP клиентов
    from api.dependencies import llm_client, embeddings_client
    await llm_client().aclose()
    await embeddings_client().aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Support Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env != "prod" else None,
        redoc_url=None,
    )

    # Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins.split(","),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuditLogMiddleware)

    # Routes
    app.include_router(health.router, tags=["health"])
    app.include_router(assistant.router, prefix="/api", tags=["assistant"])
    app.include_router(categorize.router, prefix="/api", tags=["categorize"])
    app.include_router(ingest.router, prefix="/api", tags=["ingest"])
    app.include_router(tickets.router, prefix="/api", tags=["tickets"])
    app.include_router(kb.router, prefix="/api", tags=["kb"])
    app.include_router(conversations.router, prefix="/api", tags=["conversations"])
    app.include_router(evals.router, prefix="/api", tags=["evals"])
    app.include_router(stats.router, prefix="/api", tags=["stats"])

    # Error handlers
    register_error_handlers(app)

    # Статика — отдача UI
    app.mount("/ui/static", StaticFiles(directory="ui"), name="ui_static")

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/{path:path}", include_in_schema=False)
    async def serve_ui(path: str = ""):
        # SPA-роутинг — все пути отдают index.html
        return FileResponse("ui/index.html")

    @app.get("/", include_in_schema=False)
    async def root():
        return {"app": "support-assistant", "ui": "/ui", "docs": "/api/docs"}

    return app


app = create_app()
```

## Аутентификация (MVP)

На старте — простая: заголовок `X-User-Id` для аудита. Без проверки. Это будет добавлено позже (SSO/OAuth).

```python
# api/dependencies.py
from fastapi import Header, Depends

def get_user_id(x_user_id: str | None = Header(default=None)) -> str:
    return x_user_id or "anonymous"
```

## Endpoints

### Health

`GET /health`, `GET /ready`:

```python
# api/routes/health.py
@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/ready")
async def ready(
    llm: Annotated[LLMClient, Depends(llm_client)],
    vs: Annotated[VectorStore, Depends(vector_store_client)],
):
    return {
        "status": "ok",
        "llm": "ok",                    # не дёргаем реально, чтобы не тратить токены
        "vector_store": "ok" if await vs.health() else "fail",
    }
```

### Assistant

`POST /api/assistant/chat` — одноразовый ответ.

```python
# api/schemas.py
class AssistantChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
    ticket_context: TicketContext | None = None
    filters: dict | None = None
```

```python
# api/routes/assistant.py
@router.post("/assistant/chat")
async def chat(
    req: AssistantChatRequest,
    user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[AssistantService, Depends(get_assistant_service)],
) -> Answer:
    return await service.answer(AssistantRequest(
        query=req.query,
        conversation_id=req.conversation_id,
        ticket_context=req.ticket_context,
        filters=req.filters,
    ))
```

`POST /api/assistant/chat/stream` — streaming SSE.

```python
from fastapi.responses import StreamingResponse
import json

@router.post("/assistant/chat/stream")
async def chat_stream(
    req: AssistantChatRequest,
    user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[AssistantService, Depends(get_assistant_service)],
):
    async def event_generator():
        try:
            async for chunk in service.answer_stream(AssistantRequest(
                query=req.query,
                conversation_id=req.conversation_id,
                ticket_context=req.ticket_context,
                filters=req.filters,
                stream=True,
            )):
                data = chunk.model_dump_json(exclude_none=True)
                yield f"data: {data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            err = json.dumps({"type": "error", "error": str(e)})
            yield f"data: {err}\n\n"
            yield "data: [DONE]\n\n"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",        # отключаем буферизацию nginx
        },
    )
```

`POST /api/assistant/analyze` — анализ тикета (даёт сразу полный пакет: категория + рекомендации + draft ответа).

```python
class AnalyzeTicketRequest(BaseModel):
    ticket_id: str | None = None
    subject: str | None = None
    description: str | None = None
    module: str | None = None

class AnalyzeTicketResponse(BaseModel):
    categorization: Categorization
    answer: Answer
    suggested_response_to_user: str       # драфт письма клиенту
    similar_tickets: list[dict]


@router.post("/assistant/analyze")
async def analyze(
    req: AnalyzeTicketRequest,
    user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[AssistantService, Depends(get_assistant_service)],
    cat_service: Annotated[CategorizerService, Depends(get_categorizer_service)],
) -> AnalyzeTicketResponse:
    # ... композиция категоризации + RAG + draft
```

### Categorize

`POST /api/categorize`:

```python
@router.post("/categorize")
async def categorize(
    req: CategorizeRequest,
    user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[CategorizerService, Depends(get_categorizer_service)],
) -> CategorizationResult:
    return await service.categorize(req)
```

### Ingest

`POST /api/ingest/csv` — старт ингеста CSV.

```python
from fastapi import UploadFile, File, BackgroundTasks

@router.post("/ingest/csv")
async def ingest_csv(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: Annotated[str, Depends(get_user_id)] = "anonymous",
    pipeline: Annotated[TicketIngestionPipeline, Depends(get_ingest_pipeline)] = None,
    repo: Annotated[IngestJobsRepository, Depends(get_ingest_jobs_repo)] = None,
):
    # Сохраняем загруженный файл во временную директорию
    import uuid
    job_id = str(uuid.uuid4())
    temp_path = Path("data/uploads") / f"{job_id}.csv"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    temp_path.write_bytes(content)

    # Создаём job
    await repo.create(job_id=job_id, job_type="tickets_csv", user_id=user_id)

    # Запускаем в фоне
    background.add_task(
        _run_ingest_csv_job,
        pipeline=pipeline,
        repo=repo,
        job_id=job_id,
        path=str(temp_path),
    )
    return {"job_id": job_id, "status": "started"}


async def _run_ingest_csv_job(pipeline, repo, job_id, path):
    try:
        await repo.update(job_id, status="running", started_at=datetime.utcnow())
        async def progress(stats):
            await repo.update_progress(job_id, stats)
        stats = await pipeline.run(path, job_id=job_id, progress_callback=progress)
        await repo.update(
            job_id, status="succeeded",
            finished_at=datetime.utcnow(),
            metadata=stats,
        )
    except Exception as e:
        await repo.update(
            job_id, status="failed",
            finished_at=datetime.utcnow(),
            error_message=str(e),
        )
```

`GET /api/ingest/jobs` — список задач:

```python
@router.get("/ingest/jobs")
async def list_ingest_jobs(
    limit: int = 50,
    repo: Annotated[IngestJobsRepository, Depends(get_ingest_jobs_repo)] = None,
):
    return await repo.list(limit=limit)
```

`GET /api/ingest/jobs/{job_id}` — прогресс конкретной задачи:

```python
@router.get("/ingest/jobs/{job_id}")
async def get_ingest_job(
    job_id: str,
    repo: Annotated[IngestJobsRepository, Depends(get_ingest_jobs_repo)] = None,
):
    job = await repo.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job
```

`POST /api/ingest/kb` — индексация статей KB (аналогично CSV).

### Tickets

`GET /api/tickets` — список с фильтрами.

```python
@router.get("/tickets")
async def list_tickets(
    q: str | None = None,                       # поиск
    module: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
    service: Annotated[TicketSearchService, Depends(get_ticket_search_service)] = None,
):
    return await service.list(q=q, module=module, status=status,
                              page=page, page_size=page_size)
```

`GET /api/tickets/{ticket_id}` — детали тикета.

```python
@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    service: Annotated[TicketSearchService, Depends(get_ticket_search_service)] = None,
) -> TicketDetail:
    ticket = await service.get_with_summary(ticket_id)
    if not ticket:
        raise HTTPException(404)
    return ticket
```

`POST /api/tickets/{ticket_id}/reindex` — переиндексация.

### KB

`GET /api/kb` — список статей.
`GET /api/kb/{article_id}` — статья.
`POST /api/kb` — создание (опционально для MVP — может загружаться только через ingest).
`PUT /api/kb/{article_id}` — обновление.
`DELETE /api/kb/{article_id}` — удаление (с инвалидацией индекса).

### Conversations

`GET /api/conversations` — список диалогов пользователя.

```python
@router.get("/conversations")
async def list_conversations(
    user_id: Annotated[str, Depends(get_user_id)],
    limit: int = 30,
    repo: Annotated[ConversationsRepository, Depends(get_conversations_repo)] = None,
):
    return await repo.list_for_user(user_id, limit=limit)
```

`POST /api/conversations` — создание нового диалога.

```python
@router.post("/conversations")
async def create_conversation(
    user_id: Annotated[str, Depends(get_user_id)],
    body: dict,
    repo: Annotated[ConversationsRepository, Depends(get_conversations_repo)] = None,
):
    return await repo.create(user_id=user_id, ticket_id=body.get("ticket_id"))
```

`GET /api/conversations/{id}` — сообщения диалога.

`POST /api/conversations/{id}/feedback` — feedback на сообщение.

```python
class FeedbackRequest(BaseModel):
    message_id: str
    feedback: Literal[-1, 0, 1]
    comment: str | None = None

@router.post("/conversations/{conversation_id}/feedback")
async def submit_feedback(
    conversation_id: str,
    req: FeedbackRequest,
    repo: Annotated[ConversationsRepository, Depends(get_conversations_repo)] = None,
):
    await repo.update_feedback(
        message_id=req.message_id,
        feedback=req.feedback,
        comment=req.comment,
    )
    return {"status": "ok"}
```

### Evals

`POST /api/evals/run` — запуск eval-набора.

```python
class EvalRunRequest(BaseModel):
    case_set: str = "default"             # имя набора в evals/cases/
    sample_size: int | None = None        # для smoke-теста


@router.post("/evals/run")
async def run_evals(
    background: BackgroundTasks,
    req: EvalRunRequest,
    runner: Annotated[EvalRunner, Depends(get_eval_runner)],
):
    run_id = str(uuid.uuid4())
    background.add_task(runner.run, case_set=req.case_set,
                       sample_size=req.sample_size, run_id=run_id)
    return {"run_id": run_id, "status": "started"}
```

`GET /api/evals/runs` — список прогонов.

`GET /api/evals/runs/{run_id}` — детали прогона.

### Stats

`GET /api/stats/dashboard` — данные для главного дашборда.

```python
@router.get("/stats/dashboard")
async def dashboard_stats(
    period: Literal["day", "week", "month"] = "week",
    service: Annotated[StatsService, Depends(get_stats_service)],
):
    return {
        "tickets_indexed_total": await service.count_indexed_tickets(),
        "kb_articles_total": await service.count_kb_articles(),
        "tickets_by_module": await service.tickets_by_module(period),
        "tickets_by_status": await service.tickets_by_status(period),
        "assistant_usage": await service.assistant_usage(period),
        "feedback_summary": await service.feedback_summary(period),
        "avg_latency_p95_ms": await service.avg_latency_p95(period),
        "last_ingest": await service.last_ingest_summary(),
        "last_eval": await service.last_eval_summary(),
    }
```

## Schemas

Все Pydantic-схемы для входа/выхода API — в `api/schemas.py` или в файлах роутов (если специфичны для одного эндпоинта).

Принципы:
- Входные схемы — с валидацией (min_length, max_length, regex, Literal).
- Выходные — те же доменные модели из `core.models`, не дублируем.

## Обработка ошибок

`api/errors.py`:

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from adapters.llm.exceptions import (
    LLMAuthError, LLMRateLimitError, LLMTimeoutError, LLMError,
)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LLMRateLimitError)
    async def llm_rate_limit(req: Request, exc: LLMRateLimitError):
        return JSONResponse(
            status_code=429,
            content={"error": "llm_rate_limit",
                     "message": "LLM rate limit hit, please retry",
                     "retry_after": exc.retry_after},
        )

    @app.exception_handler(LLMAuthError)
    async def llm_auth(req: Request, exc: LLMAuthError):
        # 503 — у нас не получилось аутентифицироваться в LLM
        return JSONResponse(
            status_code=503,
            content={"error": "llm_auth_failed",
                     "message": "LLM authentication failed, contact admin"},
        )

    @app.exception_handler(LLMTimeoutError)
    async def llm_timeout(req: Request, exc: LLMTimeoutError):
        return JSONResponse(
            status_code=504,
            content={"error": "llm_timeout", "message": "LLM timeout"},
        )

    @app.exception_handler(LLMError)
    async def llm_generic(req: Request, exc: LLMError):
        return JSONResponse(
            status_code=502,
            content={"error": "llm_error", "message": str(exc)},
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation(req: Request, exc: ValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "validation", "details": exc.errors()},
        )
```

## DI (Dependency Injection)

`api/dependencies.py`:

```python
from functools import lru_cache
from typing import Annotated
from fastapi import Depends, Header
from config.settings import Settings, get_settings
from adapters.llm.factory import create_llm_client
from adapters.embeddings.factory import create_embeddings_client
from adapters.vector_store.factory import create_vector_store
from adapters.text_search.factory import create_text_search
from db.engine import get_engine
from db.repositories.tickets import TicketsRepository
# ... и т.д.


@lru_cache
def llm_client():
    return create_llm_client(get_settings())

@lru_cache
def embeddings_client():
    return create_embeddings_client(get_settings())

@lru_cache
def vector_store_client():
    return create_vector_store(get_settings())

# ...

def get_assistant_service(
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLMClient, Depends(llm_client)],
    # ... и т.д.
) -> AssistantService:
    return AssistantService(...)
```

## Middleware

### RateLimitMiddleware

In-memory (для MVP). Лимит на IP + user_id. Скользящее окно 1 минута.

```python
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict, deque
from time import time
from fastapi.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = 120, window: int = 60):
        super().__init__(app)
        self.limit = limit
        self.window = window
        self.requests: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        # Только для API
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        user_id = request.headers.get("X-User-Id", "anonymous")
        ip = request.client.host if request.client else "unknown"
        key = f"{user_id}:{ip}"
        now = time()
        q = self.requests[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limit"},
                headers={"Retry-After": str(self.window)},
            )
        q.append(now)
        return await call_next(request)
```

### AuditLogMiddleware

Логирует каждый запрос (метод, путь, статус, latency, user). PII не логируем.

```python
class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
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
```

## Контракт ошибок

Все ошибки API возвращают JSON в формате:

```json
{"error": "<machine_readable_code>", "message": "<human_readable>", "details": {...}}
```

Коды:
- `validation` — 422, Pydantic
- `rate_limit` — 429
- `not_found` — 404
- `llm_*` — 502/503/504
- `internal_error` — 500

## OpenAPI

FastAPI генерирует `/api/docs` автоматически в dev/local. В prod — отключаем (`docs_url=None`).

## Тесты API

См. `18-TESTING.md`. Минимум:

- Health endpoint всегда 200.
- Chat без авторизации — работает (user_id = anonymous).
- Chat с пустым query → 422.
- Chat со слишком длинным query → 422.
- Rate limit срабатывает после N запросов.
- Streaming SSE возвращает корректный поток.
- Ingest CSV: успешный сценарий с маленьким файлом.
