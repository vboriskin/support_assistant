# 17. Deployment

Два контекста:
1. **Локальная разработка** — на машине разработчика, без инфраструктуры.
2. **Внутренний контур банка** — без интернета, фиксированные зависимости.

## Локальная разработка

### Требования

- Python 3.11+
- Опционально: Docker (только для запуска Postgres, если хочется поработать с pgvector)
- ~3 ГБ свободного диска (для модели эмбеддингов)
- ~4 ГБ RAM (модель + Python)

### Установка зависимостей

Используем `uv` (быстрый менеджер пакетов от Astral) или `poetry`. Рекомендация — `uv`.

```bash
# Если uv ещё не установлен
curl -LsSf https://astral.sh/uv/install.sh | sh

cd support-assistant
uv venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uv pip install -e .                 # установка по pyproject.toml
```

Или классически:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### pyproject.toml

```toml
[project]
name = "support-assistant"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    # Web
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    # DB
    "sqlalchemy[asyncio]>=2.0.25",
    "alembic>=1.13.0",
    "aiosqlite>=0.19.0",
    "asyncpg>=0.29.0",
    "sqlite-vec>=0.1.0",
    # HTTP
    "httpx>=0.26.0",
    # Embeddings
    "sentence-transformers>=2.3.0",
    # NER
    "natasha>=1.6.0",
    # Utilities
    "structlog>=24.1.0",
    "tenacity>=8.2.0",
    "beautifulsoup4>=4.12.0",
    "click>=8.1.0",
    "rich>=13.7.0",
    "python-multipart>=0.0.6",        # для file upload
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.1.0",
    "ruff>=0.1.0",
    "mypy>=1.8.0",
    "httpx>=0.26.0",                  # для тестов API
]

[project.scripts]
sa-ingest = "scripts.ingest_tickets:main"
sa-evals = "scripts.run_evals:main"
sa-init-db = "scripts.init_db:main"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### Первичная подготовка

```bash
# 1. Скопировать пример конфига
cp .env.example .env
# Отредактировать .env: вписать GIGACHAT_CLIENT_ID, CLIENT_SECRET

# 2. Скачать модель эмбеддингов
python -m scripts.download_models

# 3. Инициализировать БД
python -m scripts.init_db

# 4. (Опционально) Загрузить тестовые данные
python -m scripts.seed_demo_data
```

### Запуск

```bash
# Бэкенд
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

# UI доступен на http://localhost:8000/ui
# API docs: http://localhost:8000/api/docs
```

### Скрипты

```bash
# Ингест тикетов из CSV
python -m scripts.ingest_tickets ./data/tickets.csv

# Прогон evals (smoke)
python -m scripts.run_evals --sample 20

# Полный прогон evals
python -m scripts.run_evals

# Переиндексация всего (при смене модели эмбеддингов)
python -m scripts.reindex --all
```

### Postgres локально (опционально)

Если хочется проверить pgvector-режим:

```bash
# Docker
docker run -d --name pg-sa \
  -e POSTGRES_PASSWORD=dev \
  -e POSTGRES_DB=support_assistant \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# В .env:
DB_BACKEND=postgres
POSTGRES_PASSWORD=dev

# Применить миграции:
alembic upgrade head
```

## Внутренний контур банка

Особенности:
- Нет интернета (или ограничен whitelist'ом).
- Wheels — заранее скачаны и положены в локальный артефакт-репозиторий или просто в директорию.
- SSL — self-signed, нужен CA-bundle.
- GigaChat — внутренний адрес с корпоративным SSL.

### Подготовка пакетов

На машине с интернетом:

```bash
mkdir wheels
pip download --dest wheels -r requirements.txt
# Также — модель эмбеддингов отдельно
python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('intfloat/multilingual-e5-large', cache_folder='./models/embeddings')
"
# Архивируем
tar -czf sa-bundle.tar.gz wheels models pyproject.toml .env.example ui core api ... 
```

### Установка в контуре

```bash
# Распаковка
tar -xzf sa-bundle.tar.gz
cd support-assistant

# Установка без интернета
python -m venv .venv
source .venv/bin/activate
pip install --no-index --find-links=wheels -e .

# Конфиг
cp .env.example .env
# Вписать GIGACHAT_CLIENT_ID, CLIENT_SECRET, CA_BUNDLE_PATH

# Запуск
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### CA-bundle

Если GigaChat использует корпоративный self-signed:

```bash
# Получить корневой сертификат у безопасности
# Положить в проект:
cp /path/to/corp-ca.pem ./certs/corp-ca.pem

# В .env:
GIGACHAT_CA_BUNDLE_PATH=./certs/corp-ca.pem
GIGACHAT_VERIFY_SSL=true
```

### Запуск как сервис

systemd-unit `/etc/systemd/system/support-assistant.service`:

```ini
[Unit]
Description=Support Assistant API
After=network.target

[Service]
Type=simple
User=sa-user
WorkingDirectory=/opt/support-assistant
Environment="PATH=/opt/support-assistant/.venv/bin"
ExecStart=/opt/support-assistant/.venv/bin/uvicorn api.main:app \
    --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable support-assistant
systemctl start support-assistant
systemctl status support-assistant
journalctl -u support-assistant -f
```

### Несколько воркеров

Для production — несколько uvicorn-воркеров. Но есть нюансы:

- **Embedding-модель** — каждый worker загрузит свою копию в память (~2 ГБ). На сервере с 8 ГБ — максимум 2-3 воркера.
- **OAuth-токен GigaChat** — у каждого воркера свой кэш. Это нормально, просто чуть больше OAuth-вызовов.
- **SQLite** — не любит много writer'ов. Если БД — SQLite, не больше 1 воркера или переключаем на Postgres.

### Reverse proxy

nginx перед uvicorn:

```nginx
upstream sa_backend {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl;
    server_name support-assistant.bank.local;

    ssl_certificate /etc/nginx/certs/server.crt;
    ssl_certificate_key /etc/nginx/certs/server.key;

    # Размер upload (для CSV)
    client_max_body_size 50M;

    # SSE — отключить буферизацию
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;

    location / {
        proxy_pass http://sa_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Важно для SSE — `proxy_buffering off`, `proxy_read_timeout` достаточный (10 минут).

## Бэкапы

- **SQLite-БД.** Регулярный бэкап файла `data/app.db` (раз в сутки). Стандартный VACUUM + копирование.
- **Postgres.** Стандартный `pg_dump`.
- **Векторный индекс** — встроен в БД, бэкапится вместе.
- **Промпты, eval-кейсы, eval-отчёты** — в git.
- **CSV-выгрузки** — на исходных системах, копии не делаем.

## Мониторинг

Минимальный набор:

- **Liveness:** `GET /health` — простой пинг.
- **Readiness:** `GET /ready` — проверяет vector_store.
- **Логи:** структурный JSON. Парсятся внешней системой (Loki/ELK, если есть).
- **Метрики:** на старте — без Prometheus. Можно добавить `/api/stats/internal` с базовыми метриками для самих себя.

Алерты:
- 5xx > 1% за последние 5 минут.
- p95 latency > 10 секунд.
- LLM-ошибки (auth, timeout) > 5 за минуту.
- Embedding model не загрузилась.

## Версионирование

В `pyproject.toml` — semver. При деплое:

- Тег в git: `v0.1.0`.
- Сборка артефакта (tarball): `support-assistant-0.1.0.tar.gz`.
- В `/api/version` — текущая версия:

```python
@app.get("/api/version")
async def version():
    import importlib.metadata
    return {"version": importlib.metadata.version("support-assistant")}
```

## Миграции БД

Через Alembic.

```bash
# Создать новую миграцию
alembic revision --autogenerate -m "add_feedback_to_messages"

# Применить
alembic upgrade head

# Откатить на 1 шаг
alembic downgrade -1
```

При деплое — миграции применяются перед запуском нового кода:

```bash
alembic upgrade head && systemctl restart support-assistant
```

## Откат

При проблеме после деплоя:

```bash
# 1. Остановка
systemctl stop support-assistant

# 2. Восстановление кода
git checkout v0.0.9
pip install --no-index --find-links=wheels -e .

# 3. Откат миграций, если были
alembic downgrade -1

# 4. Запуск
systemctl start support-assistant
```

## Чек-лист первого деплоя

1. [ ] Получены клиентские credentials для GigaChat (client_id, client_secret).
2. [ ] Получен CA-bundle для GigaChat (если self-signed).
3. [ ] Скачана модель эмбеддингов локально.
4. [ ] Подготовлен bundle (wheels + model + код).
5. [ ] На сервере: создан пользователь `sa-user`, выделена директория `/opt/support-assistant`.
6. [ ] Установлены зависимости в venv.
7. [ ] Создан `.env` с продовыми значениями.
8. [ ] Применены миграции БД.
9. [ ] Запущен systemd-сервис.
10. [ ] Настроен nginx с SSL.
11. [ ] Проверены `/health`, `/ready`.
12. [ ] Сделан smoke-запрос к ассистенту (mock-LLM или с реальным GigaChat).
13. [ ] Прогнаны evals smoke (sample 5-10).
14. [ ] Настроен бэкап БД.
15. [ ] Настроен мониторинг логов.
