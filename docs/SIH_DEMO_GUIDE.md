# SIH Demo Guide

## Before you present
1. Run `docker compose up --build` (or the no-Docker path in README.md) and
   confirm http://localhost:8000/api/health returns `{"status":"ok"}`.
2. Have `docs/METHODOLOGY.md`, `LIMITATIONS.md`, and `PROJECT_READINESS_REPORT.md`
   open in another tab — you will get asked about at least one of these.
3. Decide who answers methodology questions vs technical/architecture
   questions — don't let one person try to cover everything live.

## Demo narrative (follow this order)
1. **Problem framing (30 sec):** Airfare is dynamic and hard to measure
   continuously; MoSPI has already signaled interest in web-scraped
   airfare data for the upcoming CPI series — this prototype demonstrates
   a feasible methodology for that.
2. **Show `/api/index/current`** — "This is a live-computed prototype
   national index, not a static number." Point out the `disclaimer` field
   — say out loud that this is intentional, not an oversight.
3. **Show the trend chart** (National Overview page) — talk through one
   real MoM/WoW figure.
4. **Switch to Route Explorer** — pick a route, show its own index history
   and the forecast table. Say explicitly: "Random Forest was chosen here
   because it beat Linear Regression on validation MAE — we didn't assume
   the fancier model was better."
5. **Show `/api/anomalies` and `/api/data-quality`** — this is the part
   that differentiates this from a booking site: statistical monitoring
   of the data itself, not just prices.
6. **Close with methodology, not code:** state the aggregation formula in
   one sentence (geometric mean at route level, fixed-weight aggregation
   nationally) and why (matches how real CPIs handle elementary
   aggregation without needing current-period expenditure data we don't
   have).

## If something breaks live
- Backend won't start → check `.env` exists (`cp .env.example .env`).
- `/api/index/current` returns 404 → the demo DB wasn't generated; run
  `python scripts/run_pipeline_demo.py` once, or use the pre-built
  `database/airfare_demo.db` shipped in the repo.
- Don't improvise a fix live — say "let me show you the underlying logic
  directly" and open `tests/test_index_engine.py` / `test_pipeline.py`
  instead, since those pass reliably and prove the same methodology.

## What to say when asked "is this real data?"
"No — this is a deterministic mock generator built to be statistically
realistic (seasonality, booking-curve pricing, holiday effects, injected
anomalies for testing). All methodology and code is designed to plug in
real scraped data without changes to the pipeline or index engine — the
adapter interface (`scraper/base.py`) is what a real source would
implement." Do not claim real data was used if it wasn't.
