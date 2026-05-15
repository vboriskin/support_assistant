# 12. Categorization

Автокатегоризация — отдельный модуль, который превращает «сырой» входящий запрос в структурированную информацию: модуль, тип, срочность, кандидат на дубль среди открытых тикетов.

Цель — снять с оператора рутину разметки и помочь решить «куда отдать тикет» в первые секунды.

## Контракт

`services/categorizer.py`:

```python
from typing import Literal
from pydantic import BaseModel
from core.models import Categorization


class CategorizeRequest(BaseModel):
    subject: str
    description: str
    channel: str | None = None
    author_role: str | None = None        # роль автора (без ФИО)
    attachments: list[str] = []           # имена файлов, без содержимого


class CategorizationResult(BaseModel):
    categorization: Categorization
    similar_open_tickets: list[dict] = [] # потенциальные дубли среди открытых
    latency_ms: int
```

## Реализация

```python
import time
import json
import re
import structlog
from pydantic import ValidationError
from core.models import Categorization
from core.prompts.loader import load_prompt
from core.pii.pipeline import PIIMaskingPipeline
from adapters.llm.base import LLMClient, ChatMessage
from adapters.embeddings.base import EmbeddingsClient
from adapters.vector_store.base import VectorStore
from db.repositories.tickets import TicketsRepository

logger = structlog.get_logger(__name__)


# Таксономия настраивается под конкретный банковский продукт
DEFAULT_TAXONOMY = {
    "modules": [
        "Скоринг", "Документы", "Андеррайтинг",
        "Решение", "Подписание", "Интеграции", "Общее",
    ],
    "types": ["bug", "question", "access_request",
              "feature_request", "incident", "duplicate", "other"],
    "urgency": ["low", "normal", "high", "critical"],
}


class CategorizerService:
    """Автокатегоризация входящих обращений."""

    def __init__(
        self,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        tickets_repo: TicketsRepository,
        pii: PIIMaskingPipeline,
        settings,
    ):
        self.llm = llm
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.tickets_repo = tickets_repo
        self.pii = pii
        self.settings = settings
        self.taxonomy = DEFAULT_TAXONOMY

    async def categorize(self, request: CategorizeRequest) -> CategorizationResult:
        t0 = time.time()

        # 1. Маскирование PII перед LLM
        masked_subject = self.pii.mask(request.subject).masked_text
        masked_desc = self.pii.mask(request.description).masked_text

        # 2. Извлечение application_id (до маскирования — для возврата оператору)
        app_id = self._extract_application_id(request.subject + " " + request.description)

        # 3. LLM-категоризация
        cat = await self._llm_categorize(masked_subject, masked_desc, request)
        cat.extracted_application_id = app_id

        # 4. Поиск похожих открытых тикетов
        similar = await self._find_similar_open(masked_subject + " " + masked_desc)

        return CategorizationResult(
            categorization=cat,
            similar_open_tickets=similar,
            latency_ms=int((time.time() - t0) * 1000),
        )

    async def _llm_categorize(
        self, subject: str, description: str, request: CategorizeRequest,
    ) -> Categorization:
        template = load_prompt("categorization")
        user_prompt = template.format(
            subject=subject,
            description=description,
            channel=request.channel or "(не указан)",
            author_role=request.author_role or "(не указана)",
            modules=", ".join(self.taxonomy["modules"]),
            types=", ".join(self.taxonomy["types"]),
            urgencies=", ".join(self.taxonomy["urgency"]),
        )
        response = await self.llm.chat_completion(
            messages=[
                ChatMessage(role="system", content="Ты — классификатор обращений в техподдержку. Отвечай JSON."),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.1,
            max_tokens=400,
            json_mode=True,
        )

        text = _extract_json(response.text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("categorizer.parse_error", error=str(e), text_preview=text[:200])
            return self._fallback_categorization(reasoning=f"parse_error: {e}")

        try:
            return Categorization(
                category=data.get("category", "Общее"),
                module=data.get("module"),
                type=data.get("type", "other"),
                urgency=data.get("urgency", "normal"),
                confidence=float(data.get("confidence", 0.5)),
                suggested_assignee_group=data.get("suggested_assignee_group"),
                extracted_application_id=None,        # заполняется выше
                reasoning=data.get("reasoning", ""),
            )
        except (ValidationError, ValueError) as e:
            logger.warning("categorizer.validation_error", error=str(e))
            return self._fallback_categorization(reasoning=str(e))

    def _fallback_categorization(self, reasoning: str) -> Categorization:
        return Categorization(
            category="Общее",
            module=None,
            type="other",
            urgency="normal",
            confidence=0.0,
            suggested_assignee_group=None,
            extracted_application_id=None,
            reasoning=reasoning,
        )

    async def _find_similar_open(self, text: str) -> list[dict]:
        """Поиск похожих открытых тикетов."""
        try:
            query_vec = await self.embeddings.embed_query(text)
            hits = await self.vector_store.search(
                query_vector=query_vec,
                top_k=5,
                target_types=["ticket_summary", "ticket_symptom"],
                min_score=0.80,
            )
        except Exception as e:
            logger.warning("categorizer.similarity_search_failed", error=str(e))
            return []

        # Достаём метаданные тикетов
        results = []
        seen_ids = set()
        for hit in hits:
            if hit.target_id in seen_ids:
                continue
            seen_ids.add(hit.target_id)
            ticket = await self.tickets_repo.get(hit.target_id)
            if not ticket:
                continue
            # Включаем только открытые
            if ticket.status not in ("open", "in_progress"):
                continue
            results.append({
                "ticket_id": ticket.id,
                "external_id": ticket.external_id,
                "subject": ticket.subject,
                "status": ticket.status,
                "score": hit.score,
            })
            if len(results) >= 3:
                break
        return results

    def _extract_application_id(self, text: str) -> str | None:
        """Достаёт ID заявки из текста до маскирования."""
        pat = re.compile(r"\b(?:APP|ЗПК|КЗ|ЗС)-?\d{4,}\b")
        m = pat.search(text)
        return m.group(0) if m else None
```

## Промпт

`core/prompts/categorization.txt`:

```
Ты — классификатор обращений в техподдержку банковского ПО.
Тебе даны тема и описание обращения. Опиши его в структурированном виде.

Доступные модули системы (выбери один или null):
{modules}

Доступные типы обращений:
- bug — баг, что-то работает не так, как должно
- question — вопрос «как сделать», «что значит»
- access_request — запрос доступа, прав, учётной записи
- feature_request — запрос новой возможности
- incident — массовая проблема, инцидент
- duplicate — кажется, что это дубль уже существующего
- other — что-то ещё

Уровни срочности:
- critical — массовая блокировка работы / финансовые риски / SLA нарушен
- high — блокирует отдельных пользователей, нет workaround
- normal — мешает, но есть workaround
- low — улучшение, неудобство

Каналы поступления: email, messenger, chatbot, sm, phone, other.

Тема: {subject}
Описание: {description}
Канал: {channel}
Роль автора: {author_role}

Ответь строго в JSON-формате:
{{
  "category": "<краткая категория, 1-3 слова, нормализованная>",
  "module": "<один из списка модулей или null>",
  "type": "<bug | question | access_request | feature_request | incident | duplicate | other>",
  "urgency": "<low | normal | high | critical>",
  "confidence": <число от 0.0 до 1.0>,
  "suggested_assignee_group": "<L1_support | L2_dev | L2_analyst | infrastructure | security | null>",
  "reasoning": "<1-2 предложения, почему так классифицировано>"
}}

Если непонятно — выставляй confidence ниже 0.6 и в reasoning поясни, чего не хватает.
Никаких префиксов, никаких комментариев — только JSON-объект.
```

## Использование

В API endpoint `POST /api/categorize`:

```python
@router.post("/api/categorize")
async def categorize_endpoint(
    req: CategorizeRequest,
    service: Annotated[CategorizerService, Depends(get_categorizer_service)],
) -> CategorizationResult:
    return await service.categorize(req)
```

В UI (страница «Помощь по новому тикету») — оператор копипастит описание, нажимает «Категоризировать». Получает:
- Предложенный модуль, тип, срочность с уверенностью.
- 0–3 похожих открытых тикета (потенциальных дубликатов).
- Извлечённый ID заявки клиента, если был.

Дальше оператор либо принимает категоризацию, либо правит, и решает: создать новый тикет или прицепить к существующему.

## Когда не работает

- Текст крайне короткий («не работает») — confidence будет низким, в reasoning будет «недостаточно информации, нужно уточнение у автора».
- Несколько проблем в одном обращении — модель выберет одну, что обычно достаточно. Идеально — оператор разделит на несколько тикетов.
- Совершенно новая категория, которой не было в обучении модели — модель выберет «Общее»/«other», confidence низкий. Это сигнал расширить промпт.

## Совершенствование таксономии

После 100–200 категоризированных тикетов — статистика по `category`, `module`, `type`. Видим:
- Какие категории доминируют → возможно, разбить на подкатегории.
- Какие категории «пустые» (никогда не выбирались) → удалить.
- Куда модель ставит низкий confidence → добавить few-shot примеры в промпт.

Это итеративная работа — раз в месяц.

## Few-shot примеры

`core/prompts/few_shot/categorization_examples.json`:

```json
[
  {
    "input": {
      "subject": "Зависает страница со скорингом",
      "description": "При заходе в модуль скоринга страница висит 10 минут, потом 500 ошибка",
      "channel": "messenger"
    },
    "output": {
      "category": "Зависание модуля",
      "module": "Скоринг",
      "type": "bug",
      "urgency": "high",
      "confidence": 0.9,
      "suggested_assignee_group": "L2_dev",
      "reasoning": "Чёткое описание бага, блокирует работу, нужна команда разработки"
    }
  },
  {
    "input": {
      "subject": "Помогите",
      "description": "Не работает!",
      "channel": "email"
    },
    "output": {
      "category": "Общее",
      "module": null,
      "type": "other",
      "urgency": "normal",
      "confidence": 0.2,
      "suggested_assignee_group": "L1_support",
      "reasoning": "Текст крайне краткий, нужно уточнение у автора: что именно не работает, в каком модуле"
    }
  }
]
```

Сейчас не включаем few-shot в основной промпт (зависит от длины контекста и качества базовой модели). Но если confidence низкий — можно добавить.

## Метрики

В дашборде (см. `14-UI.md`):

- Распределение по категориям/модулям/типам/срочности — за день/неделю/месяц.
- Распределение confidence (гистограмма).
- Доля тикетов, где оператор изменил категоризацию (если в UI есть возможность поправить — фиксируем разницу).

## Тесты

См. `18-TESTING.md`. Минимум:

- На синтетическом наборе из 10–20 примеров — категоризация даёт ожидаемый module и type в > 80% случаев.
- PII в input не попадает в LLM-запрос (проверка через mock-llm).
- Application_id извлекается корректно для разных форматов.
- Похожие открытые тикеты возвращаются (mock vector store).
- Битый JSON-ответ от LLM → fallback к Общее/other.
