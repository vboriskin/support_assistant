# 10. Retrieval

Retrieval — самый важный компонент RAG. Если retrieval не нашёл нужные источники — LLM не сможет дать правильный ответ, даже самый умный. Поэтому строим **гибридный поиск с переранжированием**.

## Архитектура

```
              query (строка от пользователя)
                       │
                       ▼
            ┌──────────────────────┐
            │  query_preprocessor  │  ← коррекция, расширение, выделение фильтров
            └──────────┬───────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌─────────────┐ ┌─────────────┐
│ Vector search│ │ Text search │ │  filters    │
│  top-30      │ │   top-30    │ │ by module/  │
│              │ │  (BM25/FTS) │ │ category/   │
│              │ │             │ │ date        │
└──────┬───────┘ └──────┬──────┘ └─────────────┘
       │                │
       ▼                ▼
    ┌─────────────────────┐
    │  Reciprocal Rank    │  ← объединение ranking из двух источников
    │  Fusion (RRF)       │
    │  → top-15 кандидатов │
    └──────────┬──────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Reranker            │  ← LLM-as-reranker или cross-encoder
    │  → top-5..8          │
    └──────────┬───────────┘
               │
               ▼
       список Source для промпта
```

## Базовый интерфейс

`services/retrieval.py`:

```python
from typing import Literal
from pydantic import BaseModel
from core.models import Source


class RetrievalFilters(BaseModel):
    """Фильтры для retrieval."""
    target_types: list[str] | None = None     # ["kb_chunk", "ticket_summary"]
    modules: list[str] | None = None
    categories: list[str] | None = None
    min_score: float = 0.0
    only_known_issues: bool = False
    date_from: str | None = None              # ISO datetime
    date_to: str | None = None


class RetrievalResult(BaseModel):
    sources: list[Source]
    debug: dict = {}                          # отладочная информация


class RetrievalService:
    """Гибридный поиск с RRF."""

    def __init__(
        self,
        embeddings,
        vector_store,
        text_search,
        reranker,
        settings,
    ):
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.text_search = text_search
        self.reranker = reranker
        self.settings = settings

    async def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        ...
```

## Vector search

Один из двух поисковых каналов. Использует эмбеддинги.

```python
async def _vector_search(
    self, query: str, filters: RetrievalFilters,
) -> list[VectorSearchHit]:
    query_vector = await self.embeddings.embed_query(query)
    return await self.vector_store.search(
        query_vector=query_vector,
        top_k=self.settings.vector_store.search_top_k,
        target_types=filters.target_types,
        metadata_filters=self._build_metadata_filters(filters),
        min_score=filters.min_score,
    )
```

`_build_metadata_filters` — преобразование `RetrievalFilters` в плоский dict для vector store:

```python
def _build_metadata_filters(self, filters: RetrievalFilters) -> dict | None:
    md: dict = {}
    if filters.modules and len(filters.modules) == 1:
        md["module"] = filters.modules[0]
    if filters.only_known_issues:
        md["is_known_issue"] = True
    return md or None
```

(Если фильтр по нескольким модулям — на уровне vector store SQL это сложнее; постфильтруем после получения top-K.)

## Text search (BM25 / FTS)

Второй канал — полнотекстовый поиск. Хорошо ловит точные совпадения терминов, имён ошибок, идентификаторов.

```python
async def _text_search(
    self, query: str, filters: RetrievalFilters,
) -> list[TextSearchHit]:
    return await self.text_search.search(
        query=query,
        top_k=self.settings.vector_store.text_search_top_k,
        target_types=filters.target_types,
    )
```

### Подготовка query для FTS

FTS5 в SQLite чувствителен к синтаксису. Опасные символы (`-`, `"`, `*`) надо экранировать или использовать в `phrase`-режиме.

```python
def _sanitize_fts_query(q: str) -> str:
    """Готовит query для FTS5: убирает спецсимволы, формирует OR-запрос."""
    # Убираем спецсимволы FTS5
    safe = re.sub(r"[^\w\s]", " ", q)
    # Минимум 2 символа на токен
    tokens = [t for t in safe.split() if len(t) >= 2]
    if not tokens:
        return q
    # OR-запрос: «токен1 OR токен2 OR ...»
    return " OR ".join(tokens)
```

Для Postgres — `plainto_tsquery` сам справляется, не нужно санитизировать.

## Reciprocal Rank Fusion (RRF)

Объединение ranking из двух источников. Главный плюс: не требует калибровки шкал (vector score и BM25 score — разные диапазоны).

Формула: для документа `d`, который занял позиции `r_vec(d)` и `r_text(d)` в двух списках:

```
RRF_score(d) = sum_over_sources( 1 / (k + rank(d)) )
```

где `k` — параметр сглаживания (60 — стандартный).

```python
def _rrf_merge(
    self,
    vector_hits: list[VectorSearchHit],
    text_hits: list[TextSearchHit],
    *,
    k: int = 60,
    top_k: int = 15,
) -> list[tuple[str, float, dict]]:
    """Объединяет два ранжированных списка через RRF.

    Возвращает: [(unified_id, rrf_score, data), ...]
    где unified_id = "target_type:target_id"
         data = {target_type, target_id, text, metadata, vector_rank?, text_rank?, scores}
    """
    fused: dict[str, dict] = {}

    for rank, hit in enumerate(vector_hits, start=1):
        key = f"{hit.target_type}:{hit.target_id}"
        fused.setdefault(key, {
            "target_type": hit.target_type,
            "target_id": hit.target_id,
            "text": hit.text,
            "metadata": hit.metadata,
            "vector_score": hit.score,
            "text_score": 0.0,
            "vector_rank": rank,
            "text_rank": None,
            "rrf_score": 0.0,
        })
        fused[key]["rrf_score"] += 1.0 / (k + rank)

    for rank, hit in enumerate(text_hits, start=1):
        key = f"{hit.target_type}:{hit.target_id}"
        if key in fused:
            fused[key]["text_score"] = hit.score
            fused[key]["text_rank"] = rank
        else:
            fused[key] = {
                "target_type": hit.target_type,
                "target_id": hit.target_id,
                "text": hit.content,             # для текста это content
                "metadata": {},
                "vector_score": 0.0,
                "text_score": hit.score,
                "vector_rank": None,
                "text_rank": rank,
                "rrf_score": 0.0,
            }
        fused[key]["rrf_score"] += 1.0 / (k + rank)

    sorted_keys = sorted(
        fused.keys(),
        key=lambda kk: fused[kk]["rrf_score"],
        reverse=True,
    )
    result = []
    for key in sorted_keys[:top_k]:
        d = fused[key]
        result.append((key, d["rrf_score"], d))
    return result
```

## Reranker

После RRF имеем 15 кандидатов. Reranker — финальный этап, выбирает 5–8 наиболее релевантных. Два варианта реализации.

### Вариант A: LLM-as-reranker

Используем тот же LLM (или меньший, если доступен). Промпт: «Оцени релевантность каждого источника запросу от 0 до 10».

`services/reranker.py`:

```python
from core.prompts.loader import load_prompt
from adapters.llm.base import LLMClient, ChatMessage


class LLMReranker:
    """Переранжирование через LLM."""

    def __init__(self, llm: LLMClient, settings):
        self.llm = llm
        self.settings = settings

    async def rerank(
        self,
        query: str,
        candidates: list[dict],     # выход RRF
        *,
        top_k: int = 8,
    ) -> list[dict]:
        if not candidates:
            return []
        if len(candidates) <= top_k:
            return candidates

        template = load_prompt("reranker")
        # Формируем нумерованный список сниппетов
        snippets_block = "\n".join(
            f"[{i}] {c['text'][:400]}"
            for i, c in enumerate(candidates)
        )
        user_prompt = template.format(
            query=query,
            snippets=snippets_block,
            top_k=top_k,
        )
        response = await self.llm.chat_completion(
            messages=[
                ChatMessage(role="system", content="Ты — переранжировщик источников. Отвечай JSON."),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.0,
            max_tokens=300,
            json_mode=True,
        )

        # Ожидаемый формат ответа: {"indices": [0, 5, 2, ...], "scores": [9, 8, 7, ...]}
        try:
            data = _extract_json(response.text)
            parsed = json.loads(data)
            indices = parsed.get("indices", [])
        except (json.JSONDecodeError, KeyError):
            # Fallback: возвращаем top по RRF
            return candidates[:top_k]

        # Валидация индексов
        valid_indices = [i for i in indices if 0 <= i < len(candidates)]
        if not valid_indices:
            return candidates[:top_k]
        reranked = [candidates[i] for i in valid_indices[:top_k]]
        # Добавляем поле rerank_rank
        for r, item in enumerate(reranked):
            item["rerank_rank"] = r
        return reranked
```

Промпт `core/prompts/reranker.txt`:

```
Тебе дан запрос пользователя и {top_k} (или больше) сниппетов из базы знаний и истории тикетов. 
Выбери {top_k} сниппетов, наиболее релевантных запросу, и упорядочь их от самого релевантного к наименее релевантному.

Критерии релевантности:
- Сниппет напрямую отвечает на запрос или содержит решение упомянутой проблемы.
- Сниппет описывает аналогичную ситуацию с известным решением.
- Сниппет содержит ключевые термины запроса в правильном контексте.

НЕ выбирай сниппеты, которые:
- Упоминают тему запроса, но не содержат полезной информации.
- Описывают противоположную ситуацию.

Запрос: {query}

Сниппеты:
{snippets}

Ответь СТРОГО JSON в формате:
{{"indices": [<номер сниппета>, ...], "reasoning": "<краткое обоснование>"}}

Только индексы, не повторяй текст. Длина indices = {top_k} или меньше, если релевантных меньше.
```

### Вариант B: Cross-encoder

Локальная модель cross-encoder (например `BAAI/bge-reranker-v2-m3`). Гораздо быстрее LLM, но требует ещё одной модели в памяти.

```python
class CrossEncoderReranker:
    def __init__(self, settings):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(settings.reranker.model)

    async def rerank(self, query, candidates, *, top_k=8):
        if not candidates:
            return []
        pairs = [(query, c["text"]) for c in candidates]
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(None, lambda: self.model.predict(pairs))
        scored = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:top_k]]
```

### Когда что использовать

- **LLM reranker** — лучший выбор для MVP. Не требует ещё одной модели в памяти. Качество хорошее. Минус — латентность +1-3 сек.
- **Cross-encoder** — для production с высокой нагрузкой. Латентность ~100ms даже на CPU.

В `.env` переключение через `RERANKER_TYPE=llm|cross_encoder|none`.

## Полный метод search

```python
async def search(
    self,
    query: str,
    *,
    filters: RetrievalFilters | None = None,
    top_k: int | None = None,
) -> RetrievalResult:
    filters = filters or RetrievalFilters()
    top_k = top_k or self.settings.retrieval.final_top_k

    # 1. Параллельный поиск
    vector_task = self._vector_search(query, filters)
    text_task = self._text_search(query, filters)
    vector_hits, text_hits = await asyncio.gather(vector_task, text_task)

    # 2. RRF
    fused = self._rrf_merge(
        vector_hits, text_hits,
        k=self.settings.retrieval.rrf_k,
        top_k=15,
    )

    # 3. Постфильтрация
    if filters.modules and len(filters.modules) > 1:
        fused = [
            (k, s, d) for k, s, d in fused
            if d["metadata"].get("module") in filters.modules
        ]
    # ... другие фильтры

    # 4. Reranker
    candidates = [d for _, _, d in fused]
    if self.reranker and self.settings.reranker.enabled:
        reranked = await self.reranker.rerank(query, candidates, top_k=top_k)
    else:
        reranked = candidates[:top_k]

    # 5. Преобразование в Source
    sources = []
    for rank, c in enumerate(reranked):
        sources.append(Source(
            source_type=c["target_type"],
            source_id=c["target_id"],
            title=self._build_title(c),
            content=c["text"],
            metadata=c["metadata"],
            score=c.get("rrf_score", 0.0),
            rank=rank,
        ))

    return RetrievalResult(
        sources=sources,
        debug={
            "vector_count": len(vector_hits),
            "text_count": len(text_hits),
            "fused_count": len(fused),
            "reranked_count": len(reranked),
        },
    )


def _build_title(self, candidate: dict) -> str:
    """Заголовок источника для UI и для промпта."""
    tt = candidate["target_type"]
    md = candidate["metadata"]
    if tt == "kb_chunk":
        return md.get("article_title", "KB-статья")
    if tt == "ticket_summary":
        return md.get("summary_one_line", candidate["text"][:80])
    if tt == "ticket_symptom":
        return "Симптом: " + candidate["text"][:60]
    return candidate["text"][:80]
```

## Hydration

После reranker'а в candidates лежит только то, что мы успели вытащить через индексы (text, metadata). Если для промпта нужны более полные данные — догружаем из БД:

```python
async def _hydrate_sources(self, sources: list[Source]) -> list[Source]:
    """Догружает полный текст из БД, если в индексе хранится только снippet."""
    for s in sources:
        if s.source_type == "ticket_summary":
            summary = await self.repo.get_summary(s.source_id)
            if summary:
                s.content = (
                    f"Симптом: {summary.symptom}\n"
                    f"Причина: {summary.root_cause or 'не указана'}\n"
                    f"Решение:\n" +
                    "\n".join(f"  {i+1}. {st}" for i, st in enumerate(summary.solution_steps))
                )
        elif s.source_type == "kb_chunk":
            # Можно достать соседние чанки той же статьи для контекста
            pass
    return sources
```

## Query preprocessing

Опционально, но повышает recall:

```python
def _preprocess_query(self, query: str) -> str:
    """Лёгкая нормализация запроса."""
    # Нижний регистр
    q = query.lower()
    # Замена синонимов из словаря
    for pattern, replacement in self._synonyms.items():
        q = q.replace(pattern, replacement)
    return q

# Словарь синонимов — в core/synonyms.json:
# {
#   "не открывается": "ошибка при открытии",
#   "не работает": "не функционирует ошибка"
# }
```

Глубокую перепись (LLM-paraphrasing) не делаем — на масштабе 100 запросов в день нет смысла удваивать LLM-вызовы.

## Метрики качества

Для `15-EVALS.md`. На эталонном наборе:

- **Recall@K** — доля кейсов, где хотя бы один из `expected_sources` попал в top-K.
- **MRR (Mean Reciprocal Rank)** — средний обратный ранг первого релевантного источника.

```python
def compute_recall_at_k(retrieved: list[Source], expected: list[str], k: int) -> float:
    retrieved_ids = {s.source_id for s in retrieved[:k]}
    return float(any(eid in retrieved_ids for eid in expected))

def compute_mrr(retrieved: list[Source], expected: list[str]) -> float:
    for i, s in enumerate(retrieved, start=1):
        if s.source_id in expected:
            return 1.0 / i
    return 0.0
```

Целевые значения:
- Recall@5 > 0.85
- MRR > 0.6

## Кэширование

В MVP не кэшируем. Если retrieval начнёт упираться — кэш по query-hash на короткий TTL (5 минут). Inmemory или Redis.

## Тестирование

См. `18-TESTING.md`. Минимум:

- RRF возвращает осмысленный порядок на синтетических ranking.
- Поиск возвращает Source с непустыми полями.
- Фильтр по target_types работает.
- Фильтр по metadata работает (через mock vector store).
- Reranker возвращает не больше top_k.
