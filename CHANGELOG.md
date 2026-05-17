# Changelog

Все значимые изменения проекта. Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added
- **E2E-аудит UI через Playwright** (`scripts/e2e_audit.py`) — обход 20 вкладок,
  7 ключевых сценариев, console errors / network failures, exit-code для CI.
- **CI-job `e2e (Playwright)`** в `.github/workflows/tests.yml` — на каждый
  push/PR. Артефакты (report.json, screenshots, uvicorn.log) сохраняются
  на 14 дней.
- **Branch protection** на `main` — обязательные чеки `pytest (3.11)`,
  `pytest (3.12)`, `lint`, `e2e (Playwright)`.
- **`workflow_dispatch`** на `tests.yml` — ручной запуск с опцией strict
  (console warnings и 4xx → FAIL).
- **`CHANGELOG.md`, `CONTRIBUTING.md`, `docs/RUN-SCRIPTS.md`, `docs/E2E-AUDIT.md`** — новая документация.

### Changed
- `run.sh` теперь стартует **боевой режим без сидинга**. Demo-сценарий —
  через `./run_demo.sh`. Полное обнуление — `./run_fresh.sh` /
  `./run_demo_fresh.sh`.
- `ruff` сконфигурирован: добавлены в ignore `RUF001/002/003` (кириллица в
  docstring'ах), `BLE001` (graceful fallback), `E701`, `SIM105/108`, `RUF005`.

### Fixed
- **Race condition в UI** при быстрой навигации: `health.js` / `costs.js`
  падали с `Cannot read properties of null` если контейнер успевал
  перерисоваться до завершения `await`. Добавлены null-checks.
- **`test_sqlite_vec_store`** на Ubuntu CI: skipif теперь по env-флагу
  `RUN_SQLITE_VEC_TESTS=1` — async aiosqlite не поддерживает
  `enable_load_extension`, а sync-проверка её не ловит.
- **`run.sh` парсинг кол-ва тикетов**: structlog-логи из импортируемых
  модулей попадали в захват и ломали условие. Берём `tail -n1` + чистим
  до числа через `tr -dc '0-9'`.

## [0.2.0] — 2026-05-17

### Added
- **`GIGACHAT_ACCESS_TOKEN`** — готовый Bearer-токен как альтернатива OAuth
  (`client_id` + `client_secret`). См. INTEGRATIONS_PORTING_GUIDE.
- **Вкладка «Настройки»** в UI — все настройки приложения с маскированием
  секретов, GET/PATCH `/api/settings` пишет в `.env`.
- **Кнопки «Выгрузить логи»** (`GET /api/diag` → JSON-дамп) и **«Обновить
  приложение»** (Cache Storage + sessionStorage cleanup + cache-buster).
- **Вкладка «Инструкция»** — 13 шагов для нового пользователя.
- **Static `Cache-Control: no-cache`** на `/ui/static/*` — чтобы кнопка
  «Обновить приложение» гарантированно подтянула свежий JS/CSS.

### Changed
- 4 скрипта запуска вместо одного: `run.sh` / `run_demo.sh` /
  `run_fresh.sh` / `run_demo_fresh.sh`.

## [0.1.0] — 2026-05-15

### Added (MVP — 13 этапов, +13 ideas, +artifacts page)
- Skeleton проекта, FastAPI + Pydantic v2 + SQLAlchemy 2.0 async.
- LLM-адаптеры: GigaChat (OAuth single-flight), OpenAI-compat, mock.
- Embeddings: sentence-transformers (e5-large), api, mock.
- Vector store: sqlite-vec + pgvector с автовыбором по `DB_BACKEND`.
- Text search: FTS5 + Postgres tsvector.
- PII pipeline: 13 типов через regex + Natasha NER + strict-mode sanity.
- Hybrid retrieval с RRF + опциональный reranker (LLM/cross-encoder/noop).
- Категоризатор: модуль / тип / срочность / suggested_assignee_group.
- Evals: judges (faithfulness + helpfulness), runner, per-case карточки,
  сравнение прогонов.
- UI: 19 вкладок без сборки, SSE-стрим для ассистента и ингеста,
  citation highlight, multi-turn clarify, CSRF auto-injection.
- 9 новых API-роутов: weak, audit, stale, pii, prompts, fewshot,
  alerts, settings, diag.
- Demo-данные в 3 вариантах (`demo_artifacts/v1_credit`, `v2_retail`,
  `v3_corp`) — 14 артефактов в каждом.
- 147 тестов проходят, GitHub Actions с pytest × 2 + lint.
