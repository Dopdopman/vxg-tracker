#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
    echo "Running Alembic migrations and idempotent catalog seed..."
    alembic -c /app/packages/shared-py/alembic.ini upgrade head
    python -m vxg_shared.db.seed
fi

exec uvicorn vxg_api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips="*"

