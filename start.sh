#!/usr/bin/env bash
# Web service entrypoint: apply DB migrations, then serve the API.
set -euo pipefail

echo "[start] running migrations…"
alembic upgrade head

echo "[start] launching API on port ${PORT:-8000}…"
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
