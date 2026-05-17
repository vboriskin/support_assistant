# Руководство по run-скриптам

Четыре точки входа для запуска стенда. Все они — обёртки над одним `run.sh`,
отличаются только комбинацией флагов `--demo` / `--fresh`.

## Когда какой брать

| Скрипт | Demo-режим | Снос хвостов | Когда |
|---|:---:|:---:|---|
| `./run.sh` | ✗ | ✗ | Боевой стенд: `.env` создаётся пустой, БД пустая. Настройки — через UI. |
| `./run_demo.sh` | ✓ | ✗ | Самый простой путь. mock-LLM + 200 демо-тикетов, всё работает без интернета. |
| `./run_fresh.sh` | ✗ | ✓ | Боевой, начисто. Снос `.venv`, БД, `.env`, токена. |
| `./run_demo_fresh.sh` | ✓ | ✓ | Demo, начисто. |

## Что делают (общий поток)

1. **Корп-токен** (sber pypi). Скрипт ищет токен в:
   1. переменной окружения `SBER_PYPI_TOKEN`,
   2. `./.sber_pypi_token` (рядом со скриптом),
   3. `~/.sber_pypi_token` (для всех проектов).

   Если не нашёл — интерактивно спрашивает (отключить — `--no-prompt`).
   Сохраняет в `.sber_pypi_token` с `chmod 600`. Файл в `.gitignore`.

2. **Auto-cleanup**: удаляет битые маркеры venv, осиротевшие `.db-wal/.db-shm`,
   `__pycache__` от чужих Python-версий.

3. **`--fresh` (если задан)**: с подтверждением сносит `.venv`,
   `data/app.db*`, `data/uploads`, `.env`, `.sber_pypi_token`,
   `logs/`, `evals/reports/`, `models/embeddings/`.

4. **venv**: создаёт `.venv`, если нет; активирует. Записывает `pip.conf`
   внутрь venv (URL корп-индекса с токеном + timeout 600s + retries 10).

5. **Зависимости**: ставит `pip / setuptools / wheel`, потом
   `pip install -e . --no-build-isolation`. Маркер `.venv/.installed_marker` —
   если есть, шаг пропускается. Сбросить — `--reset-deps`.

6. **`.env`** (если нет):
   - `--demo` → `cp .env.demo .env` (mock-LLM + mock-embeddings),
   - иначе → пустой `.env` с комментарием.

7. **Миграции**: `python -m scripts.init_db` — alembic upgrade до head.

8. **Demo-сидинг (только `--demo`)**: если в БД 0 тикетов и есть
   `data/sample_tickets.csv` → `python -m scripts.ingest_tickets ...`.

9. **uvicorn**: `--host 127.0.0.1 --port 8000 --reload`.

## Флаги

| Флаг | Что делает |
|---|---|
| `--demo` | Включает demo-режим (см. шаги 6 и 8) |
| `--fresh` | Сносит всё перед запуском, спрашивает подтверждение |
| `--reset` | Только БД: `rm data/app.db*` |
| `--reset-deps` | Удаляет `.installed_marker` → переустановка зависимостей |
| `--no-install` | Пропускает `pip install` (быстрый старт после правки кода) |
| `--no-prompt` | Без интерактивных вопросов (для CI/автоматизации) |
| `--port N` | Порт uvicorn (по умолчанию 8000) |
| `--host H` | Хост uvicorn (по умолчанию 127.0.0.1) |
| `--help`, `-h` | Полная справка |

## Типичные сценарии

### Первый запуск на новом ноуте

```bash
git clone https://github.com/vboriskin/support_assistant.git
cd support_assistant
./run_demo.sh
```

Скрипт спросит токен корп-pypi (если ходишь через сберовский индекс).
Ставит всё, поднимает на `http://127.0.0.1:8000/ui`.

### «Что-то пошло не так — хочу с нуля»

```bash
./run_demo_fresh.sh
```

Подтверди удаление — получишь стенд как при первом запуске.

### Боевой стенд (потом настрою через UI)

```bash
./run.sh
# открой http://127.0.0.1:8000/ui → Настройки → LLM
# вбей GIGACHAT_ACCESS_TOKEN или client_id/client_secret
# нажми «Сохранить» → перезапусти ./run.sh
```

### Только перезапустить uvicorn после правки кода

```bash
# uvicorn запущен с --reload — на изменения Python код перезагружается сам.
# Если правил JS/HTML — в UI нажми «Настройки → Обновить приложение»
# (сбросит кэш браузера и cache storage).
```

### Я меняю sample-тикеты в `data/`

```bash
# 1. Снести БД, чтобы при следующем сидинге залились новые данные
./run_demo.sh --reset
```

## Корпоративный pypi (Sber Sigma)

Если ходишь через корп-зеркало:

```bash
echo 'СВЕЖИЙ_ТОКЕН' > .sber_pypi_token
chmod 600 .sber_pypi_token
./run_demo.sh
```

Скрипт URL-encode'ит токен (важно для спецсимволов `+`/`/`/`=`), делает
sanity-check через curl и сразу скажет, валидный ли токен, не дожидаясь
pip-таймаутов на 5 минут.

При смене токена — просто перепиши файл и перезапусти `./run.sh --no-install`.

## sqlite_vec на macOS

На python.org-сборке macOS `enable_load_extension` отсутствует — vec0 не
загружается. У всех адаптеров есть graceful fallback на FTS5: поиск работает,
просто без семантической части. Если нужен полноценный vector store —
запускай в Linux-стенде с `python3-dev` и поддерживаемым sqlite.

Для тестов sqlite-vec нужен env-флаг `RUN_SQLITE_VEC_TESTS=1` (иначе скип).
