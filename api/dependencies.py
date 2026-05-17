"""FastAPI dependencies.

Все «тяжёлые» объекты (LLM, embeddings, vector_store, text_search, engine,
session_factory, assistant-service) — кэшированные singletons на процесс.
Сессии БД — через короткоживущий ``get_session()`` generator.

Тесты могут переопределить любой из этих провайдеров через
``app.dependency_overrides[<provider>] = lambda: ...``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.embeddings.base import EmbeddingsClient
from adapters.embeddings.factory import create_embeddings_client
from adapters.llm.base import LLMClient
from adapters.llm.factory import create_llm_client
from adapters.text_search.base import TextSearch
from adapters.text_search.factory import create_text_search
from adapters.ticket_source.csv_source import CSVTicketSource
from adapters.vector_store.base import VectorStore
from adapters.vector_store.factory import create_vector_store
from config.settings import Settings, get_settings
from core.pii.pipeline import PIIMaskingPipeline
from db.engine import get_engine, get_session_factory
from db.repositories.conversations import ConversationsRepository
from db.repositories.ingest_jobs import IngestJobsRepository
from db.repositories.llm_logs import LLMLogsRepository
from db.repositories.tickets import TicketsRepository
from pipelines.ticket_ingestion.pipeline import TicketIngestionPipeline
from services.answer_formatter import AnswerFormatter
from services.assistant import AssistantService
from services.categorizer import CategorizerService
from services.prompt_builder import PromptBuilder
from services.reranker import create_reranker
from services.retrieval import RetrievalService

# ----------------------------------------------------------------------
# Settings & user identity
# ----------------------------------------------------------------------


def settings_dep() -> Settings:
    return get_settings()


def get_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    return x_user_id or "anonymous"


# ----------------------------------------------------------------------
# Singletons (per-process)
# ----------------------------------------------------------------------


@lru_cache(maxsize=1)
def _engine() -> AsyncEngine:
    return get_engine()


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return get_session_factory()


@lru_cache(maxsize=1)
def _llm() -> LLMClient:
    return create_llm_client(get_settings())


@lru_cache(maxsize=1)
def _embeddings() -> EmbeddingsClient:
    return create_embeddings_client(get_settings())


@lru_cache(maxsize=1)
def _vector_store() -> VectorStore:
    return create_vector_store(get_settings(), _engine())


@lru_cache(maxsize=1)
def _text_search() -> TextSearch:
    return create_text_search(get_settings(), _engine())


@lru_cache(maxsize=1)
def _pii_pipeline() -> PIIMaskingPipeline:
    return PIIMaskingPipeline(get_settings())


# Публичные обёртки — это то, что роуты импортируют как зависимости.
def llm_client() -> LLMClient:
    return _llm()


def embeddings_client() -> EmbeddingsClient:
    return _embeddings()


def vector_store_client() -> VectorStore:
    return _vector_store()


def text_search_client() -> TextSearch:
    return _text_search()


def pii_pipeline_dep() -> PIIMaskingPipeline:
    return _pii_pipeline()


def reset_di_singletons() -> None:
    """Сбрасывает кэш — нужно в тестах между фикстурами."""
    _engine.cache_clear()
    _session_factory.cache_clear()
    _llm.cache_clear()
    _embeddings.cache_clear()
    _vector_store.cache_clear()
    _text_search.cache_clear()
    _pii_pipeline.cache_clear()


# ----------------------------------------------------------------------
# DB session per request
# ----------------------------------------------------------------------


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = _session_factory()
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ----------------------------------------------------------------------
# Repositories
# ----------------------------------------------------------------------


def tickets_repo(session: SessionDep) -> TicketsRepository:
    return TicketsRepository(session)


def conversations_repo(session: SessionDep) -> ConversationsRepository:
    return ConversationsRepository(session)


def llm_logs_repo(session: SessionDep) -> LLMLogsRepository:
    return LLMLogsRepository(session)


def ingest_jobs_repo(session: SessionDep) -> IngestJobsRepository:
    return IngestJobsRepository(session)


# ----------------------------------------------------------------------
# Composite services
# ----------------------------------------------------------------------


def retrieval_service(
    settings: Annotated[Settings, Depends(settings_dep)],
    embeddings: Annotated[EmbeddingsClient, Depends(embeddings_client)],
    vector_store: Annotated[VectorStore, Depends(vector_store_client)],
    text_search: Annotated[TextSearch, Depends(text_search_client)],
    llm: Annotated[LLMClient, Depends(llm_client)],
) -> RetrievalService:
    reranker = create_reranker(llm, settings)
    return RetrievalService(
        embeddings=embeddings,
        vector_store=vector_store,
        text_search=text_search,
        settings=settings,
        reranker=reranker,
    )


async def assistant_service(
    session: SessionDep,
    settings: Annotated[Settings, Depends(settings_dep)],
    retrieval: Annotated[RetrievalService, Depends(retrieval_service)],
    llm: Annotated[LLMClient, Depends(llm_client)],
    conv_repo: Annotated[ConversationsRepository, Depends(conversations_repo)],
    logs_repo: Annotated[LLMLogsRepository, Depends(llm_logs_repo)],
) -> AssistantService:
    pb = PromptBuilder(settings)
    # Активная версия системного промпта (если есть в БД)
    try:
        from sqlalchemy import select

        from db.models import FewShotExample, PromptVersion

        active = (
            await session.execute(
                select(PromptVersion).where(
                    PromptVersion.name == "system_assistant",
                    PromptVersion.is_active.is_(True),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if active is not None:
            pb.set_system_prompt(active.content)
        approved = (
            await session.execute(
                select(FewShotExample)
                .where(
                    FewShotExample.set_name == "assistant",
                    FewShotExample.status == "approved",
                )
                .limit(20)
            )
        ).scalars().all()
        for e in approved:
            pb.add_few_shot(user=e.user_text, assistant=e.assistant_text)
    except Exception:
        pass

    return AssistantService(
        retrieval=retrieval,
        llm=llm,
        prompt_builder=pb,
        formatter=AnswerFormatter(),
        settings=settings,
        conversations_repo=conv_repo,
        llm_logs_repo=logs_repo,
    )


def categorizer_service(
    settings: Annotated[Settings, Depends(settings_dep)],
    llm: Annotated[LLMClient, Depends(llm_client)],
    embeddings: Annotated[EmbeddingsClient, Depends(embeddings_client)],
    vector_store: Annotated[VectorStore, Depends(vector_store_client)],
    repo: Annotated[TicketsRepository, Depends(tickets_repo)],
    pii: Annotated[PIIMaskingPipeline, Depends(pii_pipeline_dep)],
) -> CategorizerService:
    return CategorizerService(
        llm=llm,
        embeddings=embeddings,
        vector_store=vector_store,
        tickets_repo=repo,
        pii=pii,
        settings=settings,
    )


def ingest_pipeline_dep(
    settings: Annotated[Settings, Depends(settings_dep)],
    llm: Annotated[LLMClient, Depends(llm_client)],
    embeddings: Annotated[EmbeddingsClient, Depends(embeddings_client)],
    vector_store: Annotated[VectorStore, Depends(vector_store_client)],
    text_search: Annotated[TextSearch, Depends(text_search_client)],
    pii: Annotated[PIIMaskingPipeline, Depends(pii_pipeline_dep)],
) -> TicketIngestionPipeline:
    return TicketIngestionPipeline(
        settings=settings,
        source=CSVTicketSource(),
        session_factory=_session_factory(),
        llm=llm,
        embeddings=embeddings,
        vector_store=vector_store,
        text_search=text_search,
        pii_pipeline=pii,
    )
