#!/usr/bin/env bash
# Запуск Support Assistant в БОЕВОМ режиме, начисто.
#
# Перед запуском сносит .venv, data/app.db*, .env, .sber_pypi_token и кэши,
# затем запускает run.sh без демо-данных.
#
# Спросит подтверждение перед удалением (если не задан --no-prompt).

cd "$(dirname "$0")"
exec ./run.sh --fresh "$@"
