# Eval Baseline

Зафиксированные стартовые метрики для регрессионного сравнения. Любое изменение, опускающее цифру ниже baseline без явного объяснения — кандидат на блок.

## Текущий baseline (mock-LLM)

Запуск: `LLM_PROVIDER=mock EMBEDDINGS_PROVIDER=mock python -m scripts.run_evals`
Дата: 2026-05-15, run_id см. `evals/reports/`.
Набор: `default` (16 кейсов: 10 typical + 3 no_answer + 3 adversarial).

| Метрика | Mock-baseline | Цель (с GigaChat) | Комментарий |
|---|---|---|---|
| Recall@5 | 0.19 | > 0.85 | На mock-эмбеддингах поиск шумит; на реальных — должно резко вырасти |
| Recall@10 | 0.19 | > 0.90 | то же |
| MRR | 0.0 | > 0.6 | то же |
| Faithfulness | 1.00 | > 0.95 | На mock — все «no_sources» случаи зачтены как честный отказ |
| Helpfulness | 0.19 | > 0.75 | На mock судья не вызывается; цифра ≈ доле no_answer-кейсов |
| Must-mention rate | 0.0 | > 0.7 | На mock-LLM ответ не содержит ожидаемых ключей |
| Must-not-mention violations | 0 | 0 | Нарушений нет |
| **Adversarial pass rate** | **1.00** | **1.00** | **Hard requirement: не падает ни на одном кейсе** |
| **No-answer pass rate** | **1.00** | **> 0.9** | Ассистент честно отказывает |
| Errored cases | 0 / 16 | 0 | Стабильность пайплайна |
| Avg latency (ms) | 6 | < 5000 | Для GigaChat — несколько секунд на полный цикл |

## Что подтверждает mock-baseline

Mock-прогон **не оценивает качество модели** — мок отвечает заглушками. Он проверяет:

1. **Инфраструктура работает end-to-end**: загрузка кейсов, прогон ассистента, парсинг JSON судьями, агрегация, запись отчёта.
2. **Adversarial-защита**: в ответе нет запрещённых строк (`пароль`, `ignore`, `english`, `curl`/`rm -rf`).
3. **No-answer-логика**: при пустом retrieval ассистент честно говорит «нет информации» и судья helpfulness ставит 1.0.
4. **Нет упавших кейсов**: `errored_cases == 0`.

Эти три «hard»-метрики — фундамент. Падение любого из них на mock-прогоне == блокер.

## Что покажет реальный baseline

После выгрузки реальных тикетов и подключения GigaChat:

1. Подготовить индекс: `python -m scripts.ingest_tickets data/tickets.csv` (минимум 100 тикетов).
2. Скорректировать `expected_sources` в `evals/cases/typical/*.json` — указать реальные `target_id`, появившиеся после ингеста.
3. `python -m scripts.run_evals` — должен выдать Recall@5 > 0.85, MRR > 0.6.
4. Сохранить тот run_id рядом с этим документом как «v1.0-prod-baseline».

При каждой правке промпта / модели / retriever-параметров — новый прогон, диф с baseline. Если регрессия в must-have метрике — фикс или откат.

## Команды

```bash
# Полный прогон
python -m scripts.run_evals

# Только adversarial (быстро)
python -m scripts.run_evals --case-set adversarial

# Smoke на 5 кейсов
python -m scripts.run_evals --sample 5
```

Отчёты сохраняются в `evals/reports/<run_id>.json`. UI на `/ui#/evals` показывает список и aggregate.
