# Evaluator Q&A Prep

**Why is your index needed? Why not Google Flights / MakeMyTrip?**
Those platforms are optimized for booking/search — showing you the
cheapest available option right now — not for producing a consistent,
methodologically-defined time series suitable for inflation measurement.
Our system fixes route, cabin, and departure-bucket definitions over time
specifically so the resulting series is comparable period-over-period,
which a booking search result is not designed to be.

**Why scraping instead of asking airlines directly?**
Airlines don't currently publish a machine-readable time series of
historical fares. Scraping public-facing prices is the only presently
available method for consumer-facing airfare; a production system would
ideally replace this with authorized data-sharing agreements — stated
explicitly in LIMITATIONS.md.

**Why these routes? Why these weights?**
The basket is a representative prototype subset (27 routes, geographic
and carrier spread), explicitly not the official MoSPI basket. Weights
are currently a traffic-tier proxy — a documented, honest simplification,
not real DGCA passenger-count data — because the real dataset requires
institutional access we didn't pursue in a 15-day build. See
`index_engine/weights.py`'s own docstring, which says this directly.

**Why this index formula? Why not a simple average?**
A simple average of raw prices double-counts routes with disproportionate
sampling and ignores their differing importance to overall traffic. We
use geometric mean of price relatives at the route level (standard for
elementary aggregates — avoids upward bias from divergent relative
prices) and fixed-weight aggregation nationally (Laspeyres-style, chosen
over Paasche/Fisher because we don't have reliable current-period
expenditure weights — see METHODOLOGY.md §4).

**How do you handle dynamic pricing?**
By fixing a departure-bucket dimension (days-to-departure ranges) as part
of the "item" definition, so the index doesn't conflate normal
booking-curve price increases with genuine inflation.

**How do you detect fake/suspicious fares?**
Tier-2 statistical flagging: IQR fences per (route, bucket) cell,
corroborated by Isolation Forest on fare/days-to-departure/stops. Flagged,
never silently deleted — visible in `/api/anomalies` and `/api/data-quality`.

**What if a website changes its HTML / blocks you?**
The adapter interface (`scraper/base.py`) isolates this — a broken adapter
fails independently and is marked unavailable in source health; the rest
of the pipeline and the index computation are unaffected, using the last
valid observations.

**Why ML? Why this model?**
ML is used only for forecasting and anomaly detection — never for the
index itself, which stays a transparent formula. Model choice (Random
Forest over Linear Regression) was decided by comparing validation MAE on
real held-out data, not assumed — see `docs/PROJECT_READINESS_REPORT.md`
for the actual numbers we got (413 vs 1792 MAE on a test route).

**How do you prevent data leakage in forecasting?**
Time-aware train/validation split (train on earlier travel dates, validate
on later ones) — never a random shuffle, which would let the model see
future prices during training.

**How would MoSPI validate this?**
By comparing the prototype index's movements against known airfare events
(fuel price changes, holiday surges) and, ultimately, by replacing our
proxy weights with real DGCA/expenditure data and re-running the same
transparent formula — the code doesn't need to change, only the input
weights and route basket.

**How do you ensure reproducibility?**
The index engine (`index_engine/`) is pure functions with no I/O — same
input always produces the same output, proven by unit tests. The mock
generator is deterministically seeded.

**What are the legal/ethical scraping concerns?**
Not every airline/OTA site permits automated access under its ToS. This
is why no real adapter is live yet — `scraper/airlines/indigo.py` is an
honest skeleton that raises `NotImplementedError` pending a per-source
robots.txt/ToS review, rather than scraping first and asking later.
