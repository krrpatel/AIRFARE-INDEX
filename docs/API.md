# API Reference

Base URL: `http://localhost:8000`. All responses are JSON. All endpoints
are GET (read-only). Interactive docs: `/docs` (FastAPI auto-generated).

Every index-related response includes a `disclaimer` field. Do not strip
it in any downstream use — see docs/METHODOLOGY.md for why.

## `GET /api/health`
`{"status": "ok", "mode": "mock"}`

## `GET /api/index/current`
Latest national index value.
```json
{
  "index_date": "2026-08-24", "index_value": 96.5654,
  "data_mode": "simulated_backcast", "mom_change_pct": -0.349,
  "wow_change_pct": -4.645, "routes_included": 27, "routes_excluded": 0,
  "disclaimer": "..."
}
```
404 if no index has been computed yet (run `scripts/run_pipeline_demo.py` first).

## `GET /api/index/history?start=YYYY-MM-DD&end=YYYY-MM-DD`
Full national index series. Both query params optional.

## `GET /api/index/top-movers?limit=5`
Top increasing/decreasing routes between the two most recent index dates.

## `GET /api/routes`
All basket routes with their DGCA-derived traffic-share weights.

## `GET /api/routes/{origin}/{destination}`
Route-level index history plus an imported market snapshot, airline roll-up,
fare-component availability, and up to 100 available flight observations,
e.g. `/api/routes/DEL/BOM`. 404 if not in basket.
Unknown or non-domestic IATA codes are rejected with `400`.

## `GET /api/map`
DGCA basket routes with canonical Indian airport coordinates and latest
stored route-index value where available. The frontend boundary layer is the
DataMeet India country GeoJSON, downloaded from its public maps repository.

## `GET /api/dgca/basket?top_n=50&direction_mode=bidirectional`
DGCA passenger-traffic basket and weights. `direction_mode` may be
`unidirectional`, `bidirectional`, or `merged`.

## `GET /api/lead-time?run_date=2026-08-26`
Lead-time fare summary computed from imported observed CompareFlights
output. It returns T+ windows only when observations exist.

## `GET /api/source-field-mapping`
Source-to-standard field mapping for the CompareFlights normalized JSON
adapter, including explicit `NULL` handling for unavailable fare components.

## `GET /api/forecast/{origin}/{destination}`
Trains Linear Regression + Random Forest on the fly from stored valid
observations (time-aware split — see `ml/forecasting.py`), returns both
models' validation metrics, which one was chosen, and a short forecast.
404 if the route has under 50 valid observations.

## `GET /api/data-quality`
Total/valid/suspicious observation counts and anomaly count.

## `GET /api/source-health`
Per-source status (ONLINE/DEGRADED/OFFLINE, last successful run).

## `GET /api/source-data/dates`
Returns available date-partitioned runs for the `compareflights` and `ixigo` source adapters.

## `GET /api/source-data?source=ixigo&run_date=YYYY-MM-DD`
Returns normalized, paginated source rows for the selected date. Use a larger `limit` for a complete CSV export. The adapter presents legacy Ixigo window files and the current merged route files through the same response shape.

## `GET /api/anomalies?limit=50`
Most recent flagged anomalies (route, date, detection method) — flags,
never deletions, per docs/METHODOLOGY.md §8.

## Error handling
- `404` — resource not found (route not in basket, no index computed, insufficient forecast data)
- `422` — invalid query parameters (FastAPI/Pydantic validation)
- All other errors surface as `500` with a generic message; check server logs.
