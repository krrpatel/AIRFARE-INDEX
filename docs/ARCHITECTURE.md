# Architecture

## Pipeline
```
Mock generator / Source adapters (scraper/)
  -> Raw observations
  -> Validation (data_pipeline/validation.py) -- tier-1 reject
  -> Outlier flagging (data_pipeline/outliers.py) -- tier-2 IQR + Isolation Forest
  -> Route/bucket median pricing (data_pipeline/pipeline.py)
  -> Price relatives, geometric mean (index_engine/price_relative.py)
  -> National index, fixed-weight aggregation (index_engine/index.py)
  -> Persistence (database/sqlite_store.py demo | backend/app/db/ production)
  -> FastAPI (backend/app/main.py)
  -> Next.js dashboard (frontend/)
```

## Why a modular monolith, not microservices
Six-person team, 15-day window, single deployable unit that reads/writes
one database. Kafka/Kubernetes/multi-service architectures add
coordination overhead with no throughput requirement here to justify it.
A worker process (APScheduler) + one API process + one frontend covers the
actual load.

## Two persistence paths, on purpose
- **SQLite (`database/sqlite_store.py`)** — stdlib only, zero install
  dependencies, what's actually been run and verified in this build.
- **Postgres/SQLAlchemy (`backend/app/db/`)** — the production target
  (`database/schema.sql`), written but not yet exercised against a live
  Postgres instance (see LIMITATIONS.md). `main.py` currently calls the
  SQLite path; swapping to Postgres means changing the import in
  `main.py`, not rewriting endpoint logic, since both expose the same
  function contract.

## Why the index engine is pure functions
`index_engine/` takes no DB connection and does no I/O. Given the same
inputs it always returns the same output — this is what makes it
unit-testable (20 tests) and is a hard methodological requirement
(reproducibility), not a style preference.

## Source adapter interface
`scraper/base.py` defines `SourceAdapter.fetch()` returning a
`StandardFareRecord`. The pipeline only ever calls this method — it never
inspects a specific site's HTML/JSON shape. Adding a new source means
writing one adapter file, not touching the pipeline.

## Scheduling
APScheduler (in-process), not Celery — no distributed job queue is needed
at this scale; adding one would mean standing up a broker for no
measurable benefit. See `scripts/run_worker.py`.

## What's deliberately NOT here
Redis (no measured caching need yet), Kafka, Kubernetes, microservices,
multi-region deployment — all out of scope per the project's own
"don't overengineer" constraint.
