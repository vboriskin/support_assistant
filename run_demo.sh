#!/usr/bin/env bash
# Запуск Support Assistant в DEMO-режиме.
#
# Что делает (поверх обычного run.sh):
#   - копирует .env.demo → .env, если .env нет (mock-LLM + mock-embeddings);
#   - если БД пустая, засеивает data/sample_tickets.csv (200 тикетов).
#
# Все остальные флаги run.sh прокидываются: --port, --host, --no-install, ...
#
# Пример: ./run_demo.sh --port 8080

cd "$(dirname "$0")"
exec ./run.sh --demo "$@"
