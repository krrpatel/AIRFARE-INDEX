# Pipeline Mechanism

## Flow

The pipeline runs in this order:

1. Source rows are standardized into common fields.
2. Tier-1 validation checks Indian airports, dates, currency, non-negative fares, component consistency, and plausible duration.
3. Tier-2 IQR and Isolation Forest checks flag suspicious rows without deleting them.
4. Daily representative fares are calculated per route and departure bucket using the median of valid observations.
5. Route price relatives are compared with the base period.
6. DGCA passenger weights aggregate route relatives into the national index.
7. Route, airline, anomaly, alert, and revision records are persisted in SQLite.

## DGCA Basket

`airfare/dgca/pipeline.py` reads the latest complete monthly workbooks, aggregates passenger traffic, maps city pairs to airport codes, ranks pairs, and writes the basket output. Direction mode controls whether each pair is represented as one pair, a ranked direction, or both directions.

The expected missing-month behavior is explicit: `/api/dgca/status` returns `NOT_UPLOADED_BY_DGCA`, and the latest available month is retained rather than silently treated as current.

## Storage

The local path is `database/sqlite_store.py` and `database/airfare_demo.db`. The PostgreSQL schema in `database/schema.sql` is the deployment target. Route and basket context are preserved so a future basket refresh does not rewrite historical observations.

## Useful Commands

```powershell
$env:PYTHONPATH = "."
python scripts/build_route_basket.py --help
python scripts/run_pipeline_demo.py --start 2026-01-01 --end 2026-08-24 --seed 42
python -c "from database import sqlite_store; print(sqlite_store.get_current_index('database/airfare_demo.db'))"
```
