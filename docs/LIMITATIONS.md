# Limitations

Being explicit about what this prototype does NOT do is part of the
deliverable, not an afterthought.

## Methodology limitations
- **Weights are a traffic-tier proxy**, not real DGCA passenger-count data
  or expenditure-share data. `index_engine/weights.py` says this directly
  in its module docstring — replacing `_ROUTE_TIER_PROXY` with real DGCA
  figures is required before any claim of representativeness.
- **Route basket (27 routes) is illustrative**, not the official basket a
  production system would use.
- **YoY change is not yet meaningful** — the system has under one year of
  data (simulated backcast from Jan 2026). YoY will only be statistically
  sound once genuine year-over-year coverage exists.
- The **suspicious-vs-genuine-spike distinction** (§8) is a best-effort
  heuristic (single-source-single-observation vs. corroborated), not a
  provably correct classifier — stated as an open problem, not solved.

## Engineering limitations
- **Production Postgres/SQLAlchemy path is written but never run** against
  a live Postgres instance in this build environment (no network access to
  install Postgres/SQLAlchemy here). The SQLite path is what's actually
  been executed and verified.
- **Live scraping mode raises `NotImplementedError` by design** — the
  worker's retry/backoff/mark-offline path is fully tested (verified: 2
  retries exhaust, source correctly marked OFFLINE), but there are no
  cleared source adapters to actually call. This is a legal/ToS review
  blocker, not a code gap — `scraper/airlines/indigo.py` is an honest
  skeleton, not a faked scraper.
- All current numbers come from the **mock data generator**, which is
  realistic by design but is not real market data.
- **Chain-linking (`index_engine/chain_link.py`) is implemented and tested**
  but not yet exercised against real data, since there is no live series
  to link the backcast onto until a real source goes live.
- **No caching layer beyond forecast results** — Redis was deliberately
  deferred (see Phase 1 notes) since no measured bottleneck justified it;
  forecast caching uses a simple SQLite table + TTL instead, which is
  sufficient at this scale.
- **India Map is an illustrative SVG projection** using static airport
  coordinates, not a real mapping library/tileset — fine for a prototype
  demo, not meant to be geographically precise.

## What would be required for production
- MoUs or authorized API access with airlines/OTAs, replacing scraping
  entirely where possible.
- Real DGCA/MoSPI expenditure or traffic data for weighting.
- Statistical review of the elementary-aggregation and outlier-handling
  methodology by MoSPI's own statisticians.
- A revision and publication policy matching how MoSPI publishes other
  provisional/revised indices (the mechanism exists here — see
  `index_revision_history` — but has only been exercised once, on
  simulated data).
- Load testing and monitoring appropriate for a public statistical release.
