# Pipeline Mechanism

## Flow

The pipeline runs in this order:

1. Source rows are standardized into common fields.
2. Tier-1 validation checks Indian airports, dates, currency, non-negative fares, component consistency, and plausible duration.
3. Tier-2 IQR and Isolation Forest checks flag suspicious rows without deleting them.
4. Daily representative fares are calculated per route and departure bucket using the median of valid observations.
5. Route price relatives are compared with the base period.
6. DGCA passenger weights aggregate route relatives into the national index.
7. The clean adapter writes one compact, source/date JSON artifact only after all configured routes have usable observations.

## DGCA Basket

`airfare/dgca/pipeline.py` reads the latest complete monthly workbooks, aggregates passenger traffic, maps city pairs to airport codes, ranks pairs, and writes the basket output. Direction mode controls whether each pair is represented as one pair, a ranked direction, or both directions.

The expected missing-month behavior is explicit: `/api/dgca/status` returns `NOT_UPLOADED_BY_DGCA`, and the latest available month is retained rather than silently treated as current.

## Storage

Raw audit data stays in `data/raw_airfare/<source>/YYYY-MM-DD/`. The clean
publication layer writes `data/clean_airfare/<source>/DDMMYYYY.json` with
schema version, validation metadata, route summaries, lead windows, airline
identity, fare, and `number_of_stops`. The dashboard reads clean files first
and uses raw files only as a temporary fallback before adaptation. SQLite is
not used by the current dashboard or worker path.

## Useful Commands

```powershell
$env:PYTHONPATH = "."
python scripts/build_route_basket.py --help
python -m scraper.ota.ixigo_holiday
python -m compileall -q backend airfare scraper scripts
```
