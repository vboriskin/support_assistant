"""End-to-end тест ингест-пайплайна на синтетических 5 тикетах.

Запускаем без сети: ``MockLLMClient`` (responses → подменяем выводы classifier и
summary), ``MockEmbeddingsClient`` для эмбеддингов, ``SQLiteFTS5`` для FTS,
``_InMemoryVectorStore`` вместо sqlite-vec (она зависит от системного сборки
sqlite3 c ``enable_load_extension``, на macOS python.org такой нет).

Что проверяем:

- 3 resolved-тикета → проиндексированы (tickets + summaries + векторы + FTS).
- 1 open + 1 cancelled → пропущены/сохранены без выжимки, в индекс не идут.
- Повторный прогон того же CSV → все 5 уходят в ``skipped: already_ingested``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.embeddings.mock import MockEmbeddingsClient
from adapters.llm.mock import MockLLMClient
from adapters.text_search.sqlite_fts import SQLiteFTS5
from adapters.ticket_source.csv_source import CSVTicketSource
from adapters.vector_store.base import VectorRecord, VectorSearchHit
from config.settings import Settings
from core.pii.pipeline import PIIMaskingPipeline
from db.base import Base
from db.models import Ticket as TicketORM
from db.models import TicketSummary as TicketSummaryORM
from db.repositories.tickets import TicketsRepository
from pipelines.ticket_ingestion.pipeline import TicketIngestionPipeline

DIM = 32


# ---------------------------------------------------------------------
# In-memory vector store — заменяет sqlite-vec/pgvector в тестах
# ---------------------------------------------------------------------


class _InMemoryVectorStore:
    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}

    async def upsert(self, records: list[VectorRecord]) -> None:
        for r in records:
            self._records[r.id] = r

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        to_remove = [
            r.id
            for r in self._records.values()
            if r.target_type == target_type and r.target_id in target_ids
        ]
        for rid in to_remove:
            del self._records[rid]
        return len(to_remove)

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
        metadata_filters: dict | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]:
        def _cos(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        candidates = []
        for r in self._records.values():
            if target_types and r.target_type not in target_types:
                continue
            if metadata_filters and not all(
                r.metadata.get(k) == v for k, v in metadata_filters.items()
            ):
                continue
            score = _cos(r.vector, query_vector)
            if score < min_score:
                continue
            candidates.append((score, r))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [
            VectorSearchHit(
                id=r.id,
                target_type=r.target_type,
                target_id=r.target_id,
                text=r.text,
                metadata=r.metadata,
                score=score,
            )
            for score, r in candidates[:top_k]
        ]

    async def count(self, target_type: str | None = None) -> int:
        if target_type is None:
            return len(self._records)
        return sum(1 for r in self._records.values() if r.target_type == target_type)

    async def health(self) -> bool:
        return True


# ---------------------------------------------------------------------
# CSV-фикстура — 5 тикетов
# ---------------------------------------------------------------------


def _write_csv(path: Path) -> None:
    rows = [
        # 3 resolved → должны быть проиндексированы
        {
            "external_id": "SM-1",
            "created_at": "2026-04-01T10:00:00",
            "status": "resolved",
            "subject": "Не загружается выписка PDF",
            "description": "Клиент пишет с alice@bank.ru, файл 8 МБ.",
            "module": "Документы",
            "channel": "email",
        },
        {
            "external_id": "SM-2",
            "created_at": "2026-04-02T11:00:00",
            "status": "resolved",
            "subject": "Зависает скоринг",
            "description": "После клика по «Рассчитать» страница виснет на 30 сек.",
            "module": "Скоринг",
            "channel": "sm",
        },
        {
            "external_id": "SM-3",
            "created_at": "2026-04-03T12:00:00",
            "status": "closed",
            "subject": "Не отправляется решение",
            "description": "При попытке отправить решение возникает 500.",
            "module": "Решение",
            "channel": "sm",
        },
        # 1 open
        {
            "external_id": "SM-4",
            "created_at": "2026-04-04T13:00:00",
            "status": "open",
            "subject": "Новый тикет",
            "description": "Только что появился.",
            "module": "Общее",
            "channel": "messenger",
        },
        # 1 cancelled — open-эквивалент, без выжимки
        {
            "external_id": "SM-5",
            "created_at": "2026-04-05T14:00:00",
            "status": "cancelled",
            "subject": "Отмена",
            "description": "Снято с рассмотрения.",
            "module": "Общее",
            "channel": "email",
        },
    ]
    header = list(rows[0].keys())
    extra = ["closed_at", "category", "priority", "author_role", "assignee", "tags", "conversation"]
    header = header + [c for c in extra if c not in header]
    lines = [",".join(header)]
    for r in rows:
        line = []
        for h in header:
            v = r.get(h, "")
            v = str(v).replace('"', '""')
            if "," in v:
                v = f'"{v}"'
            line.append(v)
        lines.append(",".join(line))
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------
# Mock-LLM с осмысленными ответами
# ---------------------------------------------------------------------


def _make_mock_llm() -> MockLLMClient:
    # MockLLMClient ищет ответ по подстроке в последнем user-message. Для каждой
    # «фазы» (classify_resolution / generate_summary) подсунем ответ.
    responses = {
        # Classifier: ищем по слову «resolution_status» в промпте — подходит к
        # шаблону classifier'а, отдадим resolved-вердикт.
        "Возможные значения resolution_status": json.dumps(
            {"resolution_status": "resolved", "reason": "решение есть"},
            ensure_ascii=False,
        ),
        # Summary-промпт характеризуется фразой «Поля выжимки» — отдаём JSON
        # подходящей формы.
        "Поля выжимки": json.dumps(
            {
                "summary_one_line": "Проблема решена.",
                "symptom": "Симптом из тикета.",
                "root_cause": None,
                "solution_steps": ["Шаг 1", "Шаг 2"],
                "affected_module": None,
                "user_role": "operator",
                "is_known_issue": False,
            },
            ensure_ascii=False,
        ),
    }
    return MockLLMClient(responses=responses, default_response='{"resolution_status": "unclear", "reason": "fallback"}')


# ---------------------------------------------------------------------
# Тест
# ---------------------------------------------------------------------


def _settings_with_dim() -> Settings:
    s = Settings()
    object.__setattr__(s.embeddings, "dimension", DIM)
    return s


@pytest.fixture
async def session_factory(vec_engine: AsyncEngine):
    async with vec_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(vec_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.integration
async def test_ingest_5_tickets_indexes_3_and_idempotent_on_rerun(
    tmp_path: Path,
    vec_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    csv_path = tmp_path / "tickets.csv"
    _write_csv(csv_path)

    settings = _settings_with_dim()
    llm = _make_mock_llm()
    embeddings = MockEmbeddingsClient(dimension=DIM)
    vector_store = _InMemoryVectorStore()
    text_search = SQLiteFTS5(settings, vec_engine)
    pii = PIIMaskingPipeline(settings)
    source = CSVTicketSource()

    pipeline = TicketIngestionPipeline(
        settings=settings,
        source=source,
        session_factory=session_factory,
        llm=llm,
        embeddings=embeddings,
        vector_store=vector_store,
        text_search=text_search,
        pii_pipeline=pii,
    )

    stats = await pipeline.run(str(csv_path))

    assert stats["total"] == 5
    assert stats["indexed"] == 3, stats
    assert stats["saved_without_summary"] == 2, stats
    assert stats["failed"] == 0
    assert stats["by_resolution"].get("resolved") == 3
    assert stats["by_resolution"].get("open") == 2

    # В БД ровно 5 тикетов и 3 summaries.
    async with session_factory() as s:
        tickets_count = await TicketsRepository(s).count()
        assert tickets_count == 5
        summaries = (await s.execute(select(TicketSummaryORM))).scalars().all()
        assert len(list(summaries)) == 3
        # У открытого тикета не должно быть summary
        open_tickets = (
            await s.execute(select(TicketORM).where(TicketORM.status == "open"))
        ).scalars().all()
        assert len(open_tickets) == 1

    # Векторов — 6 (по 2 на каждую выжимку), FTS — 3 записи
    assert await vector_store.count() == 6
    assert await vector_store.count("ticket_summary") == 3
    assert await vector_store.count("ticket_symptom") == 3
    assert await text_search.count() == 3

    # Повторный прогон — всё в skipped.
    stats2 = await pipeline.run(str(csv_path))
    assert stats2["total"] == 5
    assert stats2["skipped"] == 5
    assert stats2["by_skip_reason"].get("already_ingested") == 5
    assert stats2["indexed"] == 0
