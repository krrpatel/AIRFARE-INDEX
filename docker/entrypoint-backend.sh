#!/bin/sh
# Populates the SQLite demo database (idempotent -- skips if already present)
# then starts the API. This makes `docker compose up` produce real numbers
# immediately, without requiring the Postgres/SQLAlchemy path to be wired
# first (that remains the production target, tracked separately).
set -e

if [ ! -f "${SQLITE_DB_PATH:-database/airfare_demo.db}" ]; then
    echo "No demo database found -- generating mock data and computing the index..."
    python scripts/run_pipeline_demo.py --start 2026-01-01 --end 2026-08-24 --seed 42 \
        --db "${SQLITE_DB_PATH:-database/airfare_demo.db}"
fi

exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
