"""Композиция шагов ингеста тикетов.

Контракт:

- Один битый тикет не валит весь job — ловим исключение, инкрементим ``failed``.
- ``too_old`` / ``already_ingested`` уходят в ``skipped`` с причиной.
- Открытые тикеты (статус не resolved/closed) сохраняются без выжимки и в
  индекс не идут — они ещё «в работе».
- LLM-вызовы (classify + summary) под общим семафором, чтобы не превысить
  лимит провайдера.

Сессия БД создаётся снаружи и передаётся через session_factory — пайплайн
открывает новую сессию на каждый тикет, чтобы ошибка одного не загрязнила
сессию остальных.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.embeddings.base import EmbeddingsClient
from adapters.llm.base import LLMClient
from adapters.text_search.base import TextSearch
from adapters.ticket_source.base import TicketSource
from adapters.vector_store.base import VectorStore
from config.logging import get_logger
from config.settings import Settings
from core.models import Ticket
from core.pii.pipeline import PIIMaskingPipeline
from db.repositories.tickets import TicketsRepository

from .classify_resolution import classify_resolution
from .deduplicate import find_duplicate_canonical
from .generate_summary import SummaryGenerationError, generate_summary
from .index import index_ticket
from .mask_pii_step import mask_ticket
from .normalize import normalize_ticket

logger = get_logger("pipelines.ticket_ingestion.pipeline")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]] | None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TicketIngestionPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        source: TicketSource,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        text_search: TextSearch,
        pii_pipeline: PIIMaskingPipeline,
    ) -> None:
        self.settings = settings
        self.source = source
        self.session_factory = session_factory
        self.llm = llm
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.text_search = text_search
        self.pii = pii_pipeline
        self._semaphore = asyncio.Semaphore(settings.ingest.llm_concurrency)
        self._max_age = timedelta(days=settings.ingest.max_ticket_age_days)

    async def run(
        self,
        source_uri: str,
        *,
        progress_callback: ProgressCallback = None,
    ) -> dict[str, Any]:
        # Прогрев схем индексов: SQLite-FTS / sqlite-vec создают свои таблицы
        # лениво в собственной транзакции. Если делать это внутри per-ticket
        # ``session.begin()``, можно получить "database is locked". Делаем один
        # раз до начала обработки. ``count()`` дешёвый, побочно вызывает
        # ``_ensure_schema()`` у обеих реализаций.
        #
        # Vector-store может быть недоступен (например, sqlite-vec на macOS
        # python.org без ``enable_load_extension``) — не валим весь job, а
        # просто логируем. Индивидуальные upsert'ы в индексе тоже могут
        # упасть; те ошибки логируются в ``index_ticket``.
        try:
            await self.vector_store.count()
        except Exception as e:
            logger.warning("ingest.vector_store_warmup_failed", error=str(e))
        try:
            await self.text_search.count()
        except Exception as e:
            logger.warning("ingest.text_search_warmup_failed", error=str(e))

        stats: dict[str, Any] = {
            "total": 0,
            "processed": 0,
            "indexed": 0,
            "saved_without_summary": 0,
            "skipped": 0,
            "failed": 0,
            "by_resolution": {},
            "by_skip_reason": {},
            "pii_audit_total": {},
        }
        async for raw in self.source.iter_tickets(source_uri):
            stats["total"] += 1
            try:
                result = await self._process_one(raw)
            except Exception as e:
                logger.exception(
                    "ingest.ticket_failed",
                    external_id=raw.external_id,
                    error=str(e),
                )
                stats["failed"] += 1
                if progress_callback:
                    await progress_callback(stats)
                continue

            status = result["status"]
            if status == "skipped":
                stats["skipped"] += 1
                reason = result.get("reason", "unknown")
                stats["by_skip_reason"][reason] = stats["by_skip_reason"].get(reason, 0) + 1
            elif status == "saved_without_summary":
                stats["processed"] += 1
                stats["saved_without_summary"] += 1
                res = result.get("resolution_status", "open")
                stats["by_resolution"][res] = stats["by_resolution"].get(res, 0) + 1
            elif status == "indexed":
                stats["processed"] += 1
                stats["indexed"] += 1
                res = result.get("resolution_status", "resolved")
                stats["by_resolution"][res] = stats["by_resolution"].get(res, 0) + 1

            audit = result.get("pii_audit") or {}
            for k, v in audit.items():
                stats["pii_audit_total"][k] = stats["pii_audit_total"].get(k, 0) + v

            if progress_callback:
                await progress_callback(stats)
        return stats

    async def _process_one(self, raw: Ticket) -> dict[str, Any]:
        normalized = normalize_ticket(raw)

        # Фильтры — без БД-сессии
        if normalized.created_at < _now() - self._max_age:
            return {"status": "skipped", "reason": "too_old"}

        # Идемпотентность по external_id
        async with self.session_factory() as session:
            existing = await TicketsRepository(session).exists_by_external_id(
                normalized.external_id
            )
        if existing:
            return {"status": "skipped", "reason": "already_ingested"}

        masked, pii_audit = mask_ticket(normalized, self.pii)
        masked.id = str(uuid.uuid4())
        masked_at = _now()

        # Открытые — без выжимки, без индекса
        if normalized.status not in ("resolved", "closed"):
            async with self.session_factory() as session, session.begin():
                await TicketsRepository(session).save_masked(
                    masked, pii_audit=pii_audit, masked_at=masked_at
                )
            return {
                "status": "saved_without_summary",
                "resolution_status": "open",
                "pii_audit": pii_audit,
            }

        # LLM-классификация (под семафор)
        async with self._semaphore:
            verdict = await classify_resolution(masked, self.llm, self.settings)

        if verdict.resolution_status in ("no_resolution", "unclear"):
            async with self.session_factory() as session, session.begin():
                await TicketsRepository(session).save_masked(
                    masked, pii_audit=pii_audit, masked_at=masked_at
                )
            return {
                "status": "saved_without_summary",
                "resolution_status": verdict.resolution_status,
                "pii_audit": pii_audit,
            }

        # LLM-выжимка
        try:
            async with self._semaphore:
                summary = await generate_summary(masked, verdict, self.llm, self.settings)
        except SummaryGenerationError as e:
            logger.warning(
                "ingest.summary_failed",
                external_id=masked.external_id,
                error=str(e),
            )
            async with self.session_factory() as session, session.begin():
                await TicketsRepository(session).save_masked(
                    masked, pii_audit=pii_audit, masked_at=masked_at
                )
            return {
                "status": "saved_without_summary",
                "resolution_status": "unclear",
                "pii_audit": pii_audit,
            }

        # Эмбеддинги — двух текстов сразу одним батчем
        summary_text = (
            f"{summary.summary_one_line}. Симптом: {summary.symptom}."
            + (" Решение: " + "; ".join(summary.solution_steps) if summary.solution_steps else "")
        )
        symptom_text = f"passage: {summary.symptom}"
        vectors = await self.embeddings.embed_documents([summary_text, symptom_text])

        canonical_id = await find_duplicate_canonical(vectors[0], self.vector_store, threshold=0.92)
        if canonical_id:
            summary.is_duplicate_of = canonical_id

        await index_ticket(
            ticket=masked,
            summary=summary,
            summary_vector=vectors[0],
            symptom_vector=vectors[1],
            session_factory=self.session_factory,
            vector_store=self.vector_store,
            text_search=self.text_search,
            pii_audit=pii_audit,
            masked_at=masked_at,
        )

        return {
            "status": "indexed",
            "resolution_status": verdict.resolution_status,
            "pii_audit": pii_audit,
        }
