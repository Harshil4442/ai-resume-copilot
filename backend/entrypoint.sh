#!/bin/sh
set -eu

if [ "${SERVICE_ROLE:-api}" = "worker" ]; then
  exec uvicorn app.worker_main:app --host 0.0.0.0 --port "${PORT:-8080}"
fi

python -m app.migrate
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
