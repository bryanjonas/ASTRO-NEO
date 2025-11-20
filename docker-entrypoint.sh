#!/usr/bin/env bash
set -euo pipefail

RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
ALEMBIC_MAX_RETRIES="${ALEMBIC_MAX_RETRIES:-10}"
ALEMBIC_RETRY_DELAY="${ALEMBIC_RETRY_DELAY:-3}"

run_migrations() {
  if [[ "$RUN_MIGRATIONS" == "0" ]]; then
    echo "RUN_MIGRATIONS=0; skipping Alembic upgrade."
    return
  fi

  echo "Applying database migrations (max ${ALEMBIC_MAX_RETRIES} attempts)..."
  local attempt=1
  while true; do
    if alembic upgrade head; then
      echo "Database migrations applied."
      return
    fi

    if [[ "$attempt" -ge "$ALEMBIC_MAX_RETRIES" ]]; then
      echo "Failed to apply migrations after ${ALEMBIC_MAX_RETRIES} attempts." >&2
      exit 1
    fi

    echo "Alembic upgrade failed (attempt ${attempt}/${ALEMBIC_MAX_RETRIES}); retrying in ${ALEMBIC_RETRY_DELAY}s..."
    attempt=$((attempt + 1))
    sleep "${ALEMBIC_RETRY_DELAY}"
  done
}

run_migrations

exec "$@"
