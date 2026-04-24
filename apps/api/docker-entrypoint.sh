#!/usr/bin/env sh
# Cloud Run / local entrypoint: run pending migrations, then start the API.
#
# Set SKIP_MIGRATIONS=1 to bypass — useful when migrations are handled by a
# separate Cloud Run Job (recommended for multi-instance deploys, since
# concurrent `alembic upgrade head` calls race on `alembic_version`).
set -eu

if [ "${SKIP_MIGRATIONS:-0}" != "1" ] && [ -n "${DATABASE_URL:-}" ]; then
  echo "→ Running alembic migrations…"
  alembic upgrade head
else
  echo "→ Skipping migrations (SKIP_MIGRATIONS=${SKIP_MIGRATIONS:-0}, DATABASE_URL ${DATABASE_URL:+set}${DATABASE_URL:-unset})"
fi

echo "→ Starting uvicorn on :${PORT:-8080}"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
