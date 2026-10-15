#!/bin/sh
set -eu

if [ "${SERVICE_ROLE:-api}" = "worker" ]; then
  exec uvicorn app.worker_main:app --host 0.0.0.0 --port "${PORT:-8080}"
fi

if [ "${SERVICE_ROLE:-api}" = "migration" ]; then
  exec python -m app.migrate
fi

if [ "${AUTO_DB_MIGRATE:-true}" = "true" ]; then
  python -m app.migrate
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
