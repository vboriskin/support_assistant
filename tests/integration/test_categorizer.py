"""Интеграционный тест ``CategorizerService`` на mock-LLM."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.embeddings.mock import MockEmbeddingsClient
from adapters.llm.mock import MockLLMClient
from adapters.vector_store.base import VectorRecord
from config.settings import Settings
from core.pii.pipeline import PIIMaskingPipeline
from db.base import Base
from db.repositories.tickets import TicketsRepository
from services.categorizer import (
    CategorizeRequest,
    CategorizerService,
    extract_application_id,
)

from ._in_memory_vector_store import InMemoryVectorStore

pytestmark = pytest.mark.integration

DIM = 32


def _settings() -> Settings:
    s = Settings()
    object.__setattr__(s.embeddings, "dimension", DIM)
    return s


@pytest.fixture
async def factory(vec_engine: AsyncEngine):
    async with vec_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(vec_engine, expire_on_commit=False, class_=AsyncSession)


def _make_service(
    llm: MockLLMClient,
    session: AsyncSession,
    vector_store: InMemoryVectorStore,
) -> CategorizerService:
    settings = _settings()
    return CategorizerService(
        llm=llm,
        embeddings=MockEmbeddingsClient(dimension=DIM),
        vector_store=vector_store,
        tickets_repo=TicketsRepository(session),
        pii=PIIMaskingPipeline(_settings_without_ner()),
        settings=settings,
    )


def _settings_without_ner() -> Settings:
    s = Settings()
    object.__setattr__(s.pii, "ner_enabled", False)
    return s


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Помогите по заявке APP-12345678", "APP-12345678"),
        ("ЗПК-2026001 в работе", "ЗПК-2026001"),
        ("Какой-то текст про КЗ-9001 хочу узнать статус", "КЗ-9001"),
        ("ЗС_1234567890 — не открывается", "ЗС_1234567890"),
        ("Не работает кнопка", None),
    ],
)
def test_extract_application_id(text: str, expected: str | None) -> None:
    assert extract_application_id(text) == expected


async def test_categorize_returns_validated_result(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    llm_response = {
        "category": "Зависание модуля",
        "module": "Скоринг",
        "type": "bug",
        "urgency": "high",
        "confidence": 0.92,
        "suggested_assignee_group": "L2_dev",
        "reasoning": "Чёткое описание бага, блокирует работу.",
    }
    llm = MockLLMClient(
        responses={"Доступные модули системы": json.dumps(llm_response, ensure_ascii=False)},
    )
    async with factory() as session:
        svc = _make_service(llm, session, InMemoryVectorStore())
        result = await svc.categorize(
            CategorizeRequest(
                subject="Зависает модуль скоринга, заявка APP-87654321",
                description="При заходе страница висит 10 минут, потом 500. Email клиента a@b.ru",
                channel="messenger",
                author_role="operator",
            )
        )

    cat = result.categorization
    assert cat.module == "Скоринг"
    assert cat.type == "bug"
    assert cat.urgency == "high"
    assert cat.confidence == pytest.approx(0.92)
    # Application ID извлекается ДО маскирования
    assert cat.extracted_application_id == "APP-87654321"
    assert result.similar_open_tickets == []
    assert result.latency_ms >= 0


async def test_pii_does_not_leak_into_llm_request(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    llm = MockLLMClient(
        responses={
            "Доступные модули системы": json.dumps(
                {
                    "category": "Загрузка документов",
                    "module": "Документы",
                    "type": "bug",
                    "urgency": "normal",
                    "confidence": 0.7,
                    "suggested_assignee_group": "L1_support",
                    "reasoning": "ok",
                }
            )
        }
    )
    raw_subject = "Не загружается выписка, телефон +7 (495) 123-45-67"
    raw_description = "Клиент пишет с alice@bank.ru, заявка APP-12345"
    async with factory() as session:
        svc = _make_service(llm, session, InMemoryVectorStore())
        await svc.categorize(
            CategorizeRequest(subject=raw_subject, description=raw_description)
        )

    # MockLLMClient.calls — журнал тел запросов.
    assert len(llm.calls) == 1
    user_content = llm.calls[0]["messages"][-1]["content"]
    assert "+7" not in user_content
    assert "495" not in user_content
    assert "alice@bank.ru" not in user_content
    # ID заявки замаскирован в LLM-запросе (но возвращён нам отдельно)
    assert "APP-12345" not in user_content
    assert "<PHONE>" in user_content
    assert "<EMAIL>" in user_content


async def test_fallback_on_broken_llm_response(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    llm = MockLLMClient(
        responses={"Доступные модули системы": "not a json at all"},
    )
    async with factory() as session:
        svc = _make_service(llm, session, InMemoryVectorStore())
        result = await svc.categorize(
            CategorizeRequest(subject="x", description="y")
        )
    cat = result.categorization
    assert cat.category == "Общее"
    assert cat.type == "other"
    assert cat.urgency == "normal"
    assert cat.confidence == 0.0
    assert "parse_error" in cat.reasoning


async def test_similar_open_tickets_found_and_filtered_by_status(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """В vector_store два кандидата с одним и тем же текстом, в БД оба тикета:
    один ``open`` (должен попасть в результат), один ``resolved`` (должен быть
    отфильтрован)."""
    llm = MockLLMClient(
        responses={
            "Доступные модули системы": json.dumps(
                {
                    "category": "Зависание",
                    "module": "Скоринг",
                    "type": "bug",
                    "urgency": "normal",
                    "confidence": 0.7,
                    "suggested_assignee_group": "L1_support",
                    "reasoning": "ok",
                }
            )
        }
    )
    emb = MockEmbeddingsClient(dimension=DIM)
    vs = InMemoryVectorStore()
    # Mock-эмбеддинги детерминистические по тексту: чтобы cosine был ~1.0 и
    # порог 0.80 в сервисе срабатывал, кладём в индекс ровно тот же текст,
    # которым сервис ищет (масса masked_subject + "\n" + masked_description).
    request_subject = "Зависает скоринг"
    request_description = "страница расчёта виснет"
    shared_text = f"{request_subject}\n{request_description}"

    async with factory() as session:
        repo = TicketsRepository(session)
        await repo.create(
            id="t-open",
            external_id="SM-OPEN",
            channel="email",
            subject="Зависает скоринг",
            description="…",
            status="open",
            created_at=datetime(2026, 1, 1),
        )
        await repo.create(
            id="t-closed",
            external_id="SM-CLOSED",
            channel="email",
            subject="Старая проблема",
            description="…",
            status="resolved",
            created_at=datetime(2026, 1, 2),
        )
        await session.commit()

        await vs.upsert(
            [
                VectorRecord(
                    id="ts:t-open",
                    target_type="ticket_summary",
                    target_id="t-open",
                    text=shared_text,
                    metadata={},
                    vector=emb._vector(shared_text),
                ),
                VectorRecord(
                    id="ts:t-closed",
                    target_type="ticket_summary",
                    target_id="t-closed",
                    text=shared_text,
                    metadata={},
                    vector=emb._vector(shared_text),
                ),
            ]
        )

        svc = CategorizerService(
            llm=llm,
            embeddings=emb,
            vector_store=vs,
            tickets_repo=repo,
            pii=PIIMaskingPipeline(_settings_without_ner()),
            settings=_settings(),
        )
        result = await svc.categorize(
            CategorizeRequest(
                subject=request_subject,
                description=request_description,
            )
        )

    assert [t.ticket_id for t in result.similar_open_tickets] == ["t-open"]
    assert result.similar_open_tickets[0].status == "open"
