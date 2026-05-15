#!/usr/bin/env bash
# Запуск Support Assistant локально.
#
# Делает всё, что нужно для холодного старта:
#   0) интерактивно спрашивает корп-токен (если не сохранён) и режим работы;
#   1) подчищает «хвосты» предыдущих попыток (битые маркеры, кэши, locked-db);
#   2) поднимает .venv (создаёт, если нет);
#   3) ставит pip/setuptools/wheel в venv (Python 3.12+ их не кладёт сам);
#   4) ставит проект (pip install -e . --no-build-isolation);
#   5) применяет alembic-миграции до head;
#   6) при пустой БД — засеивает sample_tickets.csv (200 синтетических тикетов);
#   7) поднимает uvicorn на http://127.0.0.1:8000/ui.
#
# Флаги:
#   --fresh         снести всё (.venv, БД, кэши, .env, токен) и спросить заново
#   --reset         пересоздать БД с нуля (rm data/app.db*)
#   --reset-deps    переустановить зависимости (стирает marker, не сам venv)
#   --no-seed       не засеивать sample_tickets.csv даже при пустой БД
#   --no-install    пропустить шаг pip install (быстрый старт)
#   --no-prompt     не задавать интерактивных вопросов (для CI)
#   --port N        порт uvicorn (по умолчанию 8000)
#   --host H        хост uvicorn (по умолчанию 127.0.0.1)
#
# Использование:
#   ./run.sh                       # обычный старт (спросит токен/режим, если нужно)
#   ./run.sh --fresh               # «начисто» — снести всё и начать с нуля
#   ./run.sh --reset --port 8080   # сбросить БД, другой порт

set -euo pipefail

cd "$(dirname "$0")"

# ---------- args ----------
FRESH=0
RESET=0
RESET_DEPS=0
NO_SEED=0
NO_INSTALL=0
NO_PROMPT=0
PORT=8000
HOST="127.0.0.1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)        FRESH=1; shift ;;
    --reset)        RESET=1; shift ;;
    --reset-deps)   RESET_DEPS=1; shift ;;
    --no-seed)      NO_SEED=1; shift ;;
    --no-install)   NO_INSTALL=1; shift ;;
    --no-prompt)    NO_PROMPT=1; shift ;;
    --port)         PORT="$2"; shift 2 ;;
    --host)         HOST="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Неизвестный флаг: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }
ask()  { printf '\033[1;35m?\033[0m %s' "$*"; }

# ---------- 0. Python ----------
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 не найден в PATH"
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJOR_MINOR="${PY_VER%.*}${PY_VER#*.}"
if [[ "$PY_MAJOR_MINOR" -lt 311 ]]; then
  err "Требуется Python 3.11+, у тебя $PY_VER"
  exit 1
fi

# ---------- 1. --fresh: снести всё и начать с нуля ----------
if [[ $FRESH -eq 1 ]]; then
  if [[ $NO_PROMPT -eq 0 ]]; then
    ask "--fresh: снесу .venv, data/app.db*, .env, .sber_pypi_token, кэши. Продолжить? [y/N] "
    read -r CONFIRM || CONFIRM=""
    case "$CONFIRM" in
      y|Y|yes|Yes|YES|д|Д|да|Да|ДА) ;;
      *) err "Отменено"; exit 1 ;;
    esac
  fi
  warn "Чищу всё…"
  rm -rf .venv
  rm -f data/app.db data/app.db-shm data/app.db-wal
  rm -rf data/uploads
  rm -f .env .sber_pypi_token
  rm -rf logs/* evals/reports/* models/embeddings/* 2>/dev/null || true
fi

# ---------- 2. Auto-cleanup «хвостов» (всегда, перед стартом) ----------
# Битый marker от прошлой не-доставшейся установки → ставим заново.
if [[ -f .venv/.installed_marker && ! -f .venv/bin/python ]]; then
  warn "Найден битый marker без venv — чищу"
  rm -f .venv/.installed_marker
fi
# Зависший SQLite WAL после грубого Ctrl+C — может быть несовместим со схемой
# после нового набора миграций. Сами .db не трогаем, только спутники.
if [[ ! -f data/app.db && ( -f data/app.db-wal || -f data/app.db-shm ) ]]; then
  warn "Найдены .db-wal/.db-shm без .db — удаляю"
  rm -f data/app.db-wal data/app.db-shm
fi
# Старые pycache из чужого Python — убираем, чтобы не было ImportError'ов
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# ---------- 3. Интерактив: токен корп-зеркала ----------
SBER_HOST="sberosc.sigma.sbrf.ru"
SBER_INDEX_PATH="/repo/pypi/simple"

resolve_token() {
  if [[ -n "${SBER_PYPI_TOKEN:-}" ]]; then
    printf '%s' "$SBER_PYPI_TOKEN"; return
  fi
  if [[ -f .sber_pypi_token ]]; then
    head -n1 .sber_pypi_token | tr -d ' \r\n'; return
  fi
  if [[ -f "$HOME/.sber_pypi_token" ]]; then
    head -n1 "$HOME/.sber_pypi_token" | tr -d ' \r\n'; return
  fi
  printf ''
}

TOKEN="$(resolve_token)"
if [[ -z "$TOKEN" && $NO_PROMPT -eq 0 ]]; then
  echo
  log "Не нашёл токен для корп-зеркала $SBER_HOST."
  log "Откуда брать: внутренний сервис выдачи pypi-токенов."
  ask "Ввести токен сейчас? (Enter без значения = пропустить и использовать pypi.org): "
  # -s чтобы не светить токен в терминале
  IFS= read -rs TOKEN_INPUT || TOKEN_INPUT=""
  echo
  if [[ -n "$TOKEN_INPUT" ]]; then
    printf '%s\n' "$TOKEN_INPUT" > .sber_pypi_token
    chmod 600 .sber_pypi_token
    ok "Токен сохранён в .sber_pypi_token (chmod 600, файл в .gitignore)"
    TOKEN="$TOKEN_INPUT"
  else
    warn "Без токена — pip будет ходить на pypi.org (если разрешён)"
  fi
fi

if [[ -n "$TOKEN" ]]; then
  TOKEN_ENC="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$TOKEN")"
  export PIP_INDEX_URL="https://token:${TOKEN_ENC}@${SBER_HOST}${SBER_INDEX_PATH}"
  export PIP_TRUSTED_HOST="$SBER_HOST"
  log "Корп-индекс: ${SBER_HOST} (длина токена: ${#TOKEN})"

  # Sanity-check токена через curl
  if command -v curl >/dev/null 2>&1; then
    HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
      -u "token:${TOKEN}" \
      "https://${SBER_HOST}${SBER_INDEX_PATH}/setuptools/" || printf '000')"
    case "$HTTP_CODE" in
      200|301|302) ok "Зеркало отвечает $HTTP_CODE — токен валиден" ;;
      401|403)
        err "Зеркало отвечает $HTTP_CODE — токен НЕ принят."
        if [[ $NO_PROMPT -eq 0 ]]; then
          ask "Ввести другой токен? [y/N] "
          read -r RETRY || RETRY=""
          case "$RETRY" in
            y|Y|yes|Yes|YES|д|Д|да|Да|ДА)
              rm -f .sber_pypi_token
              exec "$0" "$@"
              ;;
          esac
        fi
        exit 1
        ;;
      000) warn "curl не достучался — нет сети/VPN? Продолжаю на свой риск." ;;
      *)   warn "Зеркало отвечает $HTTP_CODE — это необычно, пробую pip" ;;
    esac
  fi
fi

# Длинный таймаут + ретраи (torch и т.п. — 100+ МБ)
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-600}"
export PIP_RETRIES="${PIP_RETRIES:-10}"
export PIP_PROGRESS_BAR="${PIP_PROGRESS_BAR:-on}"

# ---------- 4. Интерактив: demo-режим / реальные интеграции ----------
if [[ ! -f .env && $NO_PROMPT -eq 0 ]]; then
  echo
  log "Файла .env нет — нужно выбрать режим работы."
  log "Demo-режим:  mock-LLM + mock-эмбеддинги — всё локально, без интернета."
  log "Боевой:       реальные GigaChat/OpenAI/HuggingFace — настроишь сам в вкладке «Настройки»."
  ask "Использовать demo-режим? [Y/n] "
  read -r DEMO || DEMO=""
  case "$DEMO" in
    n|N|no|No|NO|нет|Нет|НЕТ)
      log "Боевой режим — создаю пустой .env (всё на дефолтах)."
      echo "# Создан run.sh — настрой через UI «Настройки» или вручную" > .env
      ;;
    *)
      if [[ -f .env.demo ]]; then
        cp .env.demo .env
        ok ".env скопирован из .env.demo (mock-LLM + mock-эмбеддинги)"
      else
        warn ".env.demo не найден — создаю минимальный .env с mock-провайдерами"
        cat > .env <<'ENVMOCK'
LLM_PROVIDER=mock
EMBEDDINGS_PROVIDER=mock
ENVMOCK
      fi
      ;;
  esac
fi

# ---------- 5. .venv ----------
FRESH_VENV=0
if [[ ! -d .venv ]]; then
  log "Создаю .venv (python3 $PY_VER)"
  python3 -m venv .venv
  FRESH_VENV=1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
log "venv: $(python --version)"

# Записываем pip.conf в venv, чтобы ЛЮБОЙ pip install foo в этой venv
# ходил с токеном без env-переменных.
if [[ -n "$TOKEN" ]]; then
  cat > .venv/pip.conf <<PIPCONF
[global]
index-url = ${PIP_INDEX_URL}
trusted-host = ${SBER_HOST}
timeout = 600
retries = 10
PIPCONF
  chmod 600 .venv/pip.conf
fi

# ---------- 6. Зависимости ----------
if [[ $RESET_DEPS -eq 1 ]]; then
  warn "Сбрасываю marker зависимостей — будет переустановка"
  rm -f .venv/.installed_marker
fi

ensure_build_tools() {
  log "Обновляю pip / ставлю setuptools+wheel в venv (timeout=${PIP_DEFAULT_TIMEOUT}s, retries=${PIP_RETRIES})"
  python -m pip install --upgrade pip setuptools wheel
}

install_project() {
  log "Устанавливаю проект (pip install -e . --no-build-isolation)"
  python -m pip install --no-build-isolation -e .
}

if [[ $NO_INSTALL -eq 0 ]]; then
  if [[ $FRESH_VENV -eq 1 ]] || [[ ! -f .venv/.installed_marker ]]; then
    ensure_build_tools
    install_project
    touch .venv/.installed_marker
    ok "Зависимости установлены"
  else
    log "Зависимости уже установлены (--reset-deps чтобы переустановить)"
  fi
fi

# ---------- 7. Сброс БД ----------
mkdir -p data logs models/embeddings

if [[ $RESET -eq 1 ]]; then
  warn "Удаляю data/app.db* — БД будет создана заново"
  rm -f data/app.db data/app.db-shm data/app.db-wal
fi

# ---------- 8. Миграции ----------
log "Применяю alembic-миграции"
python -m scripts.init_db

# ---------- 9. Опциональный посев ----------
if [[ $NO_SEED -eq 0 ]]; then
  # Берём только последнюю строку и глушим логи в stderr — иначе structlog'и
  # из импортируемых модулей (например, sqlite_vec.load_failed на macOS)
  # склеиваются с числом и ломают условие.
  TICKETS_COUNT="$(python - 2>/dev/null <<'PY' | tail -n1
import asyncio, sys
from sqlalchemy import select, func
from db.engine import get_session_factory, dispose_engine
from db.models import Ticket

async def main():
    f = get_session_factory()
    async with f() as s:
        n = (await s.execute(select(func.count()).select_from(Ticket))).scalar() or 0
    await dispose_engine()
    sys.stdout.write(str(int(n)) + "\n")

asyncio.run(main())
PY
)"
  # Чистим до чисто-числа на всякий случай
  TICKETS_COUNT="$(printf '%s' "$TICKETS_COUNT" | tr -dc '0-9')"
  TICKETS_COUNT="${TICKETS_COUNT:-0}"
  if [[ "$TICKETS_COUNT" -eq 0 && -f data/sample_tickets.csv ]]; then
    log "БД пустая — запускаю ингест data/sample_tickets.csv (200 тикетов, ~1-2 мин на mock-LLM)"
    python -m scripts.ingest_tickets data/sample_tickets.csv || \
      warn "Ингест завершился с ошибкой. UI поднимется, но индекс будет пустым."
  else
    log "В БД уже $TICKETS_COUNT тикетов — пропускаю seed"
  fi
fi

# ---------- 10. Uvicorn ----------
ok "Готов: http://${HOST}:${PORT}/ui   (docs: /api/docs)"
log "Ctrl+C — остановить."
exec python -m uvicorn api.main:app --host "$HOST" --port "$PORT" --reload
