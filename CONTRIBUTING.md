# Contributing

Этот документ — для тех, кто пишет в этот репозиторий: автор, команда, contractor'ы.

## Поток изменений

1. **Создай ветку** от свежего `main`:
   ```bash
   git pull origin main
   git checkout -b feat/short-name
   ```
2. **Сделай изменения** + локальную проверку (см. ниже).
3. **Открой PR в `main`**. CI запустит 4 чека автоматически.
4. **Дождись зелёного CI**. Без всех 4 чеков GitHub не даст слить
   (branch protection).
5. **Squash & merge** через GitHub UI — линейная история в `main`.

## Локальная проверка перед PR

```bash
# 1. Линт
ruff check .

# 2. Unit + integration тесты (147 шт.)
pytest -q

# 3. E2E (опционально, нужно подняться сервер)
./run_demo.sh --port 8765 --no-prompt &
python -m scripts.e2e_audit --base-url http://127.0.0.1:8765
```

Если ruff жалуется — почти всё чинится `ruff check --fix .`. Что остаётся
автофиксу не подвластно — поправь руками или добавь `# noqa: <code>` если
осознанно.

## Branch protection

В `main` настроены 4 обязательных чека:

- `pytest (3.11)` — unit + integration на Python 3.11
- `pytest (3.12)` — то же на 3.12
- `lint` — `ruff check .`
- `e2e (Playwright)` — обход UI Chromium-headless'ом

Дополнительно:
- **strict mode** — ветка PR должна быть up-to-date с `main` перед мерджем.
  Если в `main` появились новые коммиты — GitHub попросит «Update branch».
- **force-push в `main` запрещён** для всех (включая admin).
- **Удаление `main` запрещено**.

Изменить эти правила можно в Settings → Branches → `main` (требует admin).

## Стиль кода

- **Python 3.11+**, type-hints везде кроме явно тривиальных мест.
- **Ruff** в проекте конфигурирует, что игнорим (см. `pyproject.toml` →
  `[tool.ruff.lint]`). Кириллица в docstring'ах ок (`RUF001/002/003` ignored),
  broad `except Exception` ок там, где это намеренный graceful fallback
  (`BLE001` ignored).
- **Async-friendly**: SQLAlchemy 2.0 async, никаких sync I/O в роутах.
- **Без новых зависимостей**, если не критично. Прежде чем добавить пакет
  — проверь, нельзя ли решить stdlib'ом.

## Тесты

- **Unit** — быстрые (без I/O), `@pytest.mark.unit`. Лежат в `tests/unit/`.
- **Integration** — с настоящей БД (sqlite in-memory), `@pytest.mark.integration`.
  Лежат в `tests/integration/`.
- **Real-LLM** — `@pytest.mark.real_llm`, скипаются без `RUN_REAL_LLM=1`.
- **sqlite_vec** — скипается без `RUN_SQLITE_VEC_TESTS=1` (требует
  совместимого стенда, см. [docs/RUN-SCRIPTS.md](./docs/RUN-SCRIPTS.md)).

При добавлении новой фичи:
1. **Unit** — для чистой логики (парсеры, маппинг, маскирование).
2. **Integration** — для всего, что трогает БД, индексы или ассистента.

Минимум — один тест на роут, проверяющий 200-статус и базовую структуру
ответа (см. `tests/integration/test_api.py` как образец).

## E2E

Если меняешь UI (`ui/**`) — обязательно прогони e2e локально перед PR:

```bash
./run_demo.sh --port 8765 --no-prompt &
python -m scripts.e2e_audit --base-url http://127.0.0.1:8765
# или с CI-флагом
python -m scripts.e2e_audit --base-url http://127.0.0.1:8765 --strict
```

Когда добавляешь новую вкладку:
1. Добавь её в `ROUTES` в `scripts/e2e_audit.py`.
2. Если есть ключевое действие (форма, кнопка с побочным эффектом) —
   допиши новый сценарий в секцию «Ключевые пользовательские сценарии».

Подробнее — [docs/E2E-AUDIT.md](./docs/E2E-AUDIT.md).

## Коммиты

Принят формат: `<area>: <short imperative>`. Примеры:

- `ui: фикс race condition при быстрой навигации`
- `api: добавил GET /api/diag для выгрузки логов`
- `ci: e2e-аудит через Playwright на каждый PR`
- `docs: обновил README под текущее состояние`

В body — почему изменение нужно, что было не так, что стало.

В конце коммита от Claude — обязательная атрибуция:
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## Не делать без согласования

- **Менять схему БД** без новой alembic-миграции и `git pull` теста на
  чистой БД.
- **Сносить или переименовывать роуты `/api/*`** — это публичный контракт
  UI и интеграций.
- **Удалять тесты** даже если они выглядят дублирующими.
- **Менять `core/security.py` / `core/redact.py`** без сильной причины —
  это слои защиты, любые правки требуют ревью.
