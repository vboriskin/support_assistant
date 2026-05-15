# 09. Pipeline: Ticket Ingestion

Пайплайн обработки тикетов превращает «сырую» CSV-выгрузку в индексированные знания. Это центральный ETL-процесс системы.

## Общая схема

```
CSV
 │
 ▼
[1] extract       → raw Ticket
 │
 ▼
[2] normalize     → нормализованный Ticket (без HTML, цитат, подписей)
 │
 ▼
[3] mask_pii      → Ticket с замаскированной PII + pii_audit
 │
 ▼
[4] filter_suitable → отсекаем неподходящие (no_resolution, старые, дубли по external_id)
 │
 ▼
[5] classify_resolution → resolved | no_resolution | workaround | unclear  (LLM)
 │
 ▼
[6] generate_summary    → TicketSummary (LLM)
 │   (только для resolved/workaround)
 │
 ▼
[7] embed         → векторы для выжимки и симптома
 │
 ▼
[8] deduplicate   → пометка is_duplicate_of для близких выжимок
 │
 ▼
[9] index         → запись в БД + векторный индекс + FTS
```

Каждый шаг — отдельный модуль с одной публичной функцией. Композиция — в `pipeline.py`.

## Композиция

`pipelines/ticket_ingestion/pipeline.py`:

```python
import asyncio
import structlog
from typing import AsyncIterator
from datetime import datetime, timedelta
import uuid

from core.models import Ticket
from core.pii.pipeline import PIIMaskingPipeline
from adapters.llm.base import LLMClient
from adapters.embeddings.base import EmbeddingsClient
from adapters.vector_store.base import VectorStore
from adapters.text_search.base import TextSearch
from adapters.ticket_source.base import TicketSource
from db.repositories.tickets import TicketsRepository
from config.settings import Settings

from .extract import parse_csv_row
from .normalize import normalize_ticket
from .mask_pii_step import mask_ticket
from .classify_resolution import classify_resolution
from .generate_summary import generate_summary
from .deduplicate import find_duplicate_canonical
from .index import index_ticket

logger = structlog.get_logger(__name__)


class TicketIngestionPipeline:
    """Композиция шагов ингеста тикетов."""

    def __init__(
        self,
        settings: Settings,
        source: TicketSource,
        repo: TicketsRepository,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        text_search: TextSearch,
        pii_pipeline: PIIMaskingPipeline,
    ):
        self.settings = settings
        self.source = source
        self.repo = repo
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
        job_id: str,
        progress_callback=None,
    ) -> dict:
        """Запуск пайплайна для одного CSV-источника.

        Возвращает summary: {total, processed, skipped, failed, by_resolution: {...}}.
        """
        stats = {
            "total": 0, "processed": 0, "skipped": 0, "failed": 0,
            "by_resolution": {},
        }

        # Шаг 1: чтение источника (генератор)
        async for raw_ticket in self.source.iter_tickets(source_uri):
            stats["total"] += 1
            try:
                result = await self._process_one(raw_ticket)
                if result["status"] == "skipped":
                    stats["skipped"] += 1
                else:
                    stats["processed"] += 1
                    res = result.get("resolution_status", "unknown")
                    stats["by_resolution"][res] = stats["by_resolution"].get(res, 0) + 1
            except Exception as e:
                logger.exception(
                    "ingest.ticket_failed",
                    external_id=raw_ticket.external_id,
                    error=str(e),
                )
                stats["failed"] += 1
            if progress_callback:
                await progress_callback(stats)
        return stats

    async def _process_one(self, raw_ticket: Ticket) -> dict:
        # Шаг 2: нормализация
        normalized = normalize_ticket(raw_ticket)

        # Фильтры:
        if normalized.created_at < datetime.now() - self._max_age:
            return {"status": "skipped", "reason": "too_old"}

        if await self.repo.exists_by_external_id(normalized.external_id):
            return {"status": "skipped", "reason": "already_ingested"}

        # Шаг 3: маскирование PII
        masked, pii_audit = mask_ticket(normalized, self.pii)
        masked.id = str(uuid.uuid4())

        # Шаг 4 (предварительная фильтрация по статусу)
        if normalized.status not in ("resolved", "closed"):
            # Открытые/в работе — сохраняем без выжимки, в индекс не идут
            await self.repo.save(masked, pii_audit=pii_audit)
            return {"status": "saved_without_summary", "resolution_status": "open"}

        # Шаг 5: классификация резолюции (LLM)
        async with self._semaphore:
            resolution = await classify_resolution(masked, self.llm, self.settings)
        if resolution.resolution_status in ("no_resolution", "unclear"):
            await self.repo.save(masked, pii_audit=pii_audit)
            return {
                "status": "saved_without_summary",
                "resolution_status": resolution.resolution_status,
            }

        # Шаг 6: генерация выжимки (LLM)
        async with self._semaphore:
            summary = await generate_summary(masked, resolution, self.llm, self.settings)

        # Шаг 7: эмбеддинги (на выжимку и на симптом отдельно)
        summary_text = f"{summary.summary_one_line}. Симптом: {summary.symptom}. Решение: {'; '.join(summary.solution_steps)}"
        symptom_text = f"passage: {summary.symptom}"

        vectors = await self.embeddings.embed_documents([summary_text, symptom_text])

        # Шаг 8: поиск дубликатов
        canonical_id = await find_duplicate_canonical(
            summary, vectors[0], self.vector_store, self.repo, threshold=0.92,
        )
        if canonical_id:
            summary.is_duplicate_of = canonical_id

        # Шаг 9: запись в БД + индексы
        await index_ticket(
            ticket=masked,
            summary=summary,
            summary_text=summary_text,
            symptom_text=symptom_text,
            summary_vector=vectors[0],
            symptom_vector=vectors[1],
            repo=self.repo,
            vector_store=self.vector_store,
            text_search=self.text_search,
            pii_audit=pii_audit,
        )

        return {"status": "indexed", "resolution_status": resolution.resolution_status}
```

## Шаги по отдельности

### Шаг 1. Extract

`pipelines/ticket_ingestion/extract.py`. Парсинг одной строки CSV в `Ticket`. Использует адаптер `TicketSource` (см. ниже), который читает CSV построчно.

```python
from datetime import datetime
import json
from core.models import Ticket, TicketComment


def parse_csv_row(row: dict) -> Ticket:
    """Парсит одну CSV-строку в Ticket."""
    # Обязательные поля
    required = ["external_id", "created_at", "status", "subject", "description"]
    for f in required:
        if not row.get(f):
            raise ValueError(f"Missing required field: {f}")

    # Парсинг даты
    try:
        created_at = datetime.fromisoformat(row["created_at"])
    except ValueError as e:
        raise ValueError(f"Invalid created_at format: {row['created_at']}") from e

    closed_at = None
    if row.get("closed_at"):
        try:
            closed_at = datetime.fromisoformat(row["closed_at"])
        except ValueError:
            pass

    # Парсинг conversation (опционально)
    conversation = []
    if row.get("conversation"):
        try:
            conv_raw = json.loads(row["conversation"])
            for c in conv_raw:
                conversation.append(TicketComment(
                    author_role=c.get("author_role"),
                    content=c.get("content", ""),
                    created_at=datetime.fromisoformat(c["created_at"]),
                    is_internal=c.get("is_internal", False),
                ))
        except (json.JSONDecodeError, KeyError, ValueError):
            # Не падаем, просто пропускаем поле
            pass

    tags = []
    if row.get("tags"):
        tags = [t.strip() for t in row["tags"].split(",") if t.strip()]

    return Ticket(
        id="",                             # будет сгенерирован
        external_id=row["external_id"],
        channel=row.get("channel", "other"),
        category=row.get("category"),
        module=row.get("module"),
        subject=row["subject"],
        description=row["description"],
        conversation=conversation,
        author_role=row.get("author_role"),
        assignee=row.get("assignee"),
        status=row["status"],
        priority=row.get("priority"),
        tags=tags,
        created_at=created_at,
        closed_at=closed_at,
        raw_fields=row,
    )
```

Адаптер `CSVTicketSource` (`adapters/ticket_source/csv_source.py`):

```python
import csv
from pathlib import Path
from typing import AsyncIterator
import structlog
from core.models import Ticket
from pipelines.ticket_ingestion.extract import parse_csv_row

logger = structlog.get_logger(__name__)


class CSVTicketSource:
    """Источник тикетов из CSV-файла."""

    async def iter_tickets(self, source_uri: str) -> AsyncIterator[Ticket]:
        path = Path(source_uri)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for line_num, row in enumerate(reader, start=2):
                try:
                    ticket = parse_csv_row(row)
                    yield ticket
                except ValueError as e:
                    logger.warning("csv.row.invalid", line=line_num, error=str(e))
                    continue
```

### Шаг 2. Normalize

`pipelines/ticket_ingestion/normalize.py`. Удаление HTML, цитат, подписей.

```python
import re
from bs4 import BeautifulSoup
from core.models import Ticket


_QUOTE_PATTERNS = [
    re.compile(r"^>+.*$", re.MULTILINE),
    re.compile(r"^-----\s*Original Message\s*-----.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"\nОт:.*?\n(?=\n)", re.DOTALL),    # «От: ..., Кому: ..., Тема: ...»
    re.compile(r"\n[-—]\s*$.*", re.DOTALL),         # подпись после «-- »
]

_SIGNATURE_HINTS = [
    "С уважением", "С наилучшими пожеланиями", "Best regards", "Regards,",
]


def _strip_html(s: str) -> str:
    if "<" not in s:
        return s
    return BeautifulSoup(s, "html.parser").get_text(separator=" ", strip=False)


def _strip_quotes_and_signatures(s: str) -> str:
    for pat in _QUOTE_PATTERNS:
        s = pat.sub("", s)
    # Удаляем подписи: всё после "С уважением," в последних строках
    lines = s.splitlines()
    cut_at = None
    for i in range(max(0, len(lines) - 10), len(lines)):
        for hint in _SIGNATURE_HINTS:
            if hint in lines[i]:
                cut_at = i
                break
        if cut_at:
            break
    if cut_at is not None:
        s = "\n".join(lines[:cut_at])
    return s


def _normalize_whitespace(s: str) -> str:
    s = re.sub(r"\r\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def normalize_text(s: str) -> str:
    s = _strip_html(s)
    s = _strip_quotes_and_signatures(s)
    s = _normalize_whitespace(s)
    return s


def normalize_ticket(ticket: Ticket) -> Ticket:
    """Возвращает новый Ticket с очищенными полями."""
    normalized = ticket.model_copy(deep=True)
    normalized.subject = normalize_text(ticket.subject)
    normalized.description = normalize_text(ticket.description)
    new_conv = []
    for c in normalized.conversation:
        c.content = normalize_text(c.content)
        # Удаляем пустые
        if c.content:
            new_conv.append(c)
    normalized.conversation = new_conv
    return normalized
```

### Шаг 3. Mask PII

`pipelines/ticket_ingestion/mask_pii_step.py` — просто обёртка над `core/pii/ticket_masking.py::mask_ticket`. Уже описано в `08-PII-MASKING.md`.

### Шаг 5. Classify resolution

`pipelines/ticket_ingestion/classify_resolution.py`. LLM-вызов с промптом из `core/prompts/ticket_resolution_classifier.txt`.

```python
import json
from pydantic import BaseModel, ValidationError
from typing import Literal
from core.models import Ticket
from core.prompts.loader import load_prompt
from adapters.llm.base import LLMClient, ChatMessage
from adapters.llm.exceptions import LLMError
from config.settings import Settings


class ResolutionVerdict(BaseModel):
    resolution_status: Literal["resolved", "no_resolution", "workaround", "unclear"]
    reason: str


async def classify_resolution(
    ticket: Ticket,
    llm: LLMClient,
    settings: Settings,
) -> ResolutionVerdict:
    template = load_prompt("ticket_resolution_classifier")
    ticket_text = _format_ticket_for_classification(ticket)
    user_prompt = template.format(ticket_text=ticket_text)

    response = await llm.chat_completion(
        messages=[
            ChatMessage(role="system", content="Ты — классификатор тикетов поддержки. Отвечай строго в JSON."),
            ChatMessage(role="user", content=user_prompt),
        ],
        temperature=0.0,
        max_tokens=200,
        json_mode=True,
    )

    # Парсинг ответа. Защита от мусора.
    text = response.text.strip()
    text = _extract_json(text)
    try:
        return ResolutionVerdict.model_validate_json(text)
    except (json.JSONDecodeError, ValidationError) as e:
        # Возможно, модель ответила «как умеет» — нужен fallback
        return ResolutionVerdict(resolution_status="unclear", reason=f"parse_error: {e}")


def _extract_json(s: str) -> str:
    """Достаёт первый JSON-объект из строки."""
    s = s.strip()
    # Если модель завернула в ```json ... ```
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    return s


def _format_ticket_for_classification(t: Ticket) -> str:
    lines = [
        f"Категория: {t.category or '(не указана)'}",
        f"Модуль: {t.module or '(не указан)'}",
        f"Статус: {t.status}",
        f"Тема: {t.subject}",
        f"Описание: {t.description}",
    ]
    if t.conversation:
        lines.append("Переписка:")
        for c in t.conversation:
            role = c.author_role or "?"
            lines.append(f"  [{role}] {c.content}")
    return "\n".join(lines)
```

### Шаг 6. Generate summary

`pipelines/ticket_ingestion/generate_summary.py`. Аналогично, но генерируем структурированную выжимку.

```python
import json
from datetime import datetime
from pydantic import BaseModel, ValidationError
from core.models import Ticket, TicketSummary
from core.prompts.loader import load_prompt
from adapters.llm.base import LLMClient, ChatMessage
from config.settings import Settings


class _SummaryLLMResponse(BaseModel):
    summary_one_line: str
    symptom: str
    root_cause: str | None = None
    solution_steps: list[str]
    affected_module: str | None = None
    user_role: str | None = None
    is_known_issue: bool


async def generate_summary(
    ticket: Ticket,
    resolution,
    llm: LLMClient,
    settings: Settings,
) -> TicketSummary:
    template = load_prompt("ticket_summary")
    few_shot = load_prompt("ticket_summary_examples")    # из few_shot/
    ticket_text = _format_ticket(ticket)
    user_prompt = template.format(
        few_shot=few_shot,
        ticket_text=ticket_text,
    )

    response = await llm.chat_completion(
        messages=[
            ChatMessage(role="system", content=load_prompt("system_ingest")),
            ChatMessage(role="user", content=user_prompt),
        ],
        temperature=0.1,
        max_tokens=600,
        json_mode=True,
    )

    text = _extract_json(response.text)
    try:
        parsed = _SummaryLLMResponse.model_validate_json(text)
    except (json.JSONDecodeError, ValidationError) as e:
        # Один retry с более явной просьбой про JSON
        retry_resp = await llm.chat_completion(
            messages=[
                ChatMessage(role="system", content="Ты возвращаешь ТОЛЬКО валидный JSON-объект, без обрамления."),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.0,
            max_tokens=600,
        )
        parsed = _SummaryLLMResponse.model_validate_json(_extract_json(retry_resp.text))

    return TicketSummary(
        ticket_id=ticket.id,
        summary_one_line=parsed.summary_one_line,
        symptom=parsed.symptom,
        root_cause=parsed.root_cause,
        solution_steps=parsed.solution_steps,
        affected_module=parsed.affected_module or ticket.module,
        user_role=parsed.user_role,
        is_known_issue=parsed.is_known_issue,
        resolution_status=resolution.resolution_status,
        is_duplicate_of=None,
        generated_at=datetime.utcnow(),
        model_used=response.model,
    )
```

### Шаг 8. Deduplicate

`pipelines/ticket_ingestion/deduplicate.py`. Поиск похожих выжимок по эмбеддингу.

```python
from core.models import TicketSummary
from adapters.vector_store.base import VectorStore


async def find_duplicate_canonical(
    summary: TicketSummary,
    summary_vector: list[float],
    vector_store: VectorStore,
    repo,
    *,
    threshold: float = 0.92,
) -> str | None:
    """Возвращает ticket_id канонической записи, если найден дубль."""
    hits = await vector_store.search(
        query_vector=summary_vector,
        top_k=5,
        target_types=["ticket_summary"],
        min_score=threshold,
    )
    if not hits:
        return None
    # Самый похожий — кандидат на канонику.
    # Но: текущий тикет может быть свежее → стоит сравнить даты.
    # Простая логика — берём существующий как канонику.
    return hits[0].target_id
```

### Шаг 9. Index

`pipelines/ticket_ingestion/index.py`. Запись в БД + индексы. Атомарность — на уровне БД-транзакции для своих данных, vector_store и text_search — отдельные операции (failure recovery — переиндексация).

```python
import uuid
from core.models import Ticket, TicketSummary
from adapters.vector_store.base import VectorStore, VectorRecord
from adapters.text_search.base import TextSearch
from db.repositories.tickets import TicketsRepository


async def index_ticket(
    *,
    ticket: Ticket,
    summary: TicketSummary,
    summary_text: str,
    symptom_text: str,
    summary_vector: list[float],
    symptom_vector: list[float],
    repo: TicketsRepository,
    vector_store: VectorStore,
    text_search: TextSearch,
    pii_audit: dict[str, int],
) -> None:
    # 1. Запись тикета и выжимки в БД
    await repo.save_with_summary(ticket, summary, pii_audit=pii_audit)

    # 2. Векторный индекс — две записи (выжимка и симптом)
    summary_emb_id = f"ts:{summary.ticket_id}"
    symptom_emb_id = f"sm:{summary.ticket_id}"
    metadata = {
        "module": summary.affected_module or "",
        "is_known_issue": summary.is_known_issue,
        "resolution_status": summary.resolution_status,
        "created_at": ticket.created_at.isoformat(),
    }
    await vector_store.upsert([
        VectorRecord(
            id=summary_emb_id, target_type="ticket_summary",
            target_id=summary.ticket_id, text=summary_text,
            metadata=metadata, vector=summary_vector,
        ),
        VectorRecord(
            id=symptom_emb_id, target_type="ticket_symptom",
            target_id=summary.ticket_id, text=symptom_text,
            metadata=metadata, vector=symptom_vector,
        ),
    ])

    # 3. Полнотекстовый индекс — одна запись для тикета
    await text_search.upsert([{
        "id": f"ts:{summary.ticket_id}",
        "target_type": "ticket_summary",
        "target_id": summary.ticket_id,
        "title": summary.summary_one_line,
        "content": f"{summary.symptom}\n{ticket.description}\n" +
                   "\n".join(summary.solution_steps),
    }])

    # 4. Пометка в БД, что тикет проиндексирован
    await repo.mark_indexed(summary.ticket_id)
```

## Запуск через API

См. `13-API.md`. Endpoint `POST /api/ingest/csv` принимает upload (или путь к файлу) и запускает пайплайн в фоне через FastAPI `BackgroundTasks` или `asyncio.create_task`. Прогресс — через GET `/api/ingest/jobs/{job_id}`.

## Запуск через CLI

`scripts/ingest_tickets.py`:

```python
"""CLI для запуска ингеста."""
import asyncio
import click
from rich.progress import Progress
from config.settings import get_settings
# ... импорты адаптеров

@click.command()
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
def main(csv_path: str):
    asyncio.run(_run(csv_path))

async def _run(csv_path: str):
    settings = get_settings()
    pipeline = TicketIngestionPipeline(
        settings=settings,
        source=CSVTicketSource(),
        # ... остальные зависимости
    )
    with Progress() as progress:
        task = progress.add_task("Ingesting tickets...", total=None)
        stats = await pipeline.run(
            csv_path,
            job_id="cli",
            progress_callback=lambda s: progress.update(task, completed=s["total"]),
        )
    print(stats)

if __name__ == "__main__":
    main()
```

Запуск: `python -m scripts.ingest_tickets ./data/tickets.csv`.

## Обработка ошибок

- Ошибка парсинга CSV-строки → лог + пропуск этой строки.
- Ошибка LLM на классификации → тикет сохраняется без выжимки (с пометкой `unclear`).
- Ошибка LLM на генерации summary → один retry, потом тикет сохраняется без выжимки.
- Ошибка PII-маскирования в strict mode → весь тикет пропускается + лог.
- Ошибка записи в БД → весь тикет пропускается + лог + counter `failed`.

Один битый тикет не должен ломать весь job. Job завершается со статистикой `total/processed/skipped/failed`.

## Идемпотентность

Тикет с уже существующим `external_id` — пропускается. Чтобы переиндексировать — отдельный endpoint `POST /api/tickets/{id}/reindex`, который удалит старые векторы и FTS-записи и пройдёт пайплайн заново.

## Параллельность

- LLM-вызовы — через семафор `INGEST_LLM_CONCURRENCY` (по умолчанию 4). Чтобы не превысить лимиты GigaChat.
- Эмбеддинги — батчем (по 32-64 текста на вызов).
- БД-запись — одна транзакция на тикет.

При желании можно увеличить `INGEST_LLM_CONCURRENCY` если у GigaChat большие лимиты, но не больше 8-16 — иначе можно получить 429.

## Метрики

В конце job-а сохраняем в `ingest_jobs`:
- `total_items`, `processed_items`, `failed_items`.
- Распределение `resolution_status`.
- Сумма PII-замен по типам.
- Время выполнения.

Это видно в UI на странице «Ингесты» — список всех прогонов с метриками.
