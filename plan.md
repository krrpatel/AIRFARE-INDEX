# Airfare Price Index: Visualization and Frontend Plan

Updated: 2026-09-02

## Objective

Deliver an operational research dashboard for an India Airfare Price Index. The interface must help a senior reviewer understand the current index, its route and airline drivers, data freshness, source health, lead-time behavior, and quality limitations without relying on fabricated values.

## Product Scope

- National index with daily history, weekly/monthly movement, current publication date, route coverage, observation count, and quality flags.
- DGCA-derived route basket with visible window, rank, passenger weight, direction mode, airport names, and route-level index.
- Route explorer driven by selection rather than free-text search. Each route shows airport names and IATA codes, airline sections, T+1/T+7/T+15/T+30/T+45 observations, fare components when the source exposes them, offers, alerts, and an interactive historical chart.
- Airline analysis with sortable coverage/fare/index metrics, selectable airline route lines, configurable Top N routes, and weekly scraped-fare overview.
- India geographic view using real GeoJSON and map tiles. Route and airport hover states provide an overview; click states provide persistent detail.
- Analytics view with route ranking, cheapest and highest sectors, fare distribution, monthly seasonality, and route-airline CSV export with the active filters applied.
- Settings for DGCA monthly refresh day/time, OTA daily scrape time, direction mode, Top N basket, source links, and the merged data/method section.

## Data Contracts

Every observation should retain origin, destination, travel date, booking date, lead time, carrier code/name, flight number where available, fare class, base fare, taxes, statutory charges, convenience fee, total payable fare, currency, baggage, stops, source, source URL, retrieval timestamp, availability, and quality status. Missing components remain `null` and are labelled unavailable in the interface.

The active local source is the normalized CompareFlights OTA import. Direct airline collection controls are present as coming soon until each carrier's terms, permissions, and compliant adapter are configured. The dashboard must label simulated backcast/index history separately from imported observations.

DGCA collection runs once per month and checks for the previous month's workbook. If it is missing, the cycle is explicitly marked `NOT_UPLOADED_BY_DGCA` and the latest available month is retained. OTA collection runs once per day at the configured fixed time. Missing collection dates are displayed as missing, never interpolated.

## Index and Quality

- Use the DGCA passenger-weighted basket and retain basket snapshots so future route changes do not rewrite history.
- Calculate route, airline, and national index values from valid observations only; preserve flagged rows for audit.
- Expose week-over-week and month-over-month movement, outlier/anomaly counts, source health, revisions, and alert thresholds.
- Keep the research disclaimer visible: this is not an official MoSPI/CPI statistic until formally validated and adopted.
- Do not claim a MoSPI benchmark comparison unless a machine-readable official benchmark is actually present in the repository.

## Interaction Requirements

- Global pages must have loading, empty, and error states.
- Tables must support selection, sorting, useful filtering, readable pagination/limits, and airport names alongside codes.
- Route CSV export must include every filtered row, not only rows currently visible on screen.
- Charts must have labelled axes, legends, dates, values, hover tooltips, and zoom/reset controls where the series is long. Tooltips must include the route/airline, lead-time label, current fare/index, and an alert comparison when thresholds are exceeded.
- Use stable dimensions and responsive layouts; no chart may overflow or hide its labels on narrow screens.

## Current Implementation

- FastAPI endpoints under `backend/app/main.py` and SQLite query layer under `database/sqlite_store.py`.
- Next.js frontend under `frontend/app` with Overview, Routes, Airlines, India Map, Analytics, and Settings views.
- DGCA basket and airport registry under `airfare/`, imported OTA records under `data/raw_airfare/`, and raw/processed DGCA files under `data/dgca/`.
- Same-origin Next.js proxy at `frontend/app/api/backend/[...path]/route.js` for local browser operation.
- Leaflet + GeoJSON map, route-airline analytics endpoint, interactive route ranking/export, fare distribution, and seasonality views.

## Verification Checklist

1. Run the backend against `database/airfare_demo.db` and confirm `/api/health`, `/api/routes/analytics`, `/api/analytics`, `/api/map`, `/api/source-health`, and `/api/dgca/status`.
2. Run the frontend build and inspect Overview, Routes, Airlines, India Map, Analytics, and Settings at desktop and mobile widths.
3. Confirm route selection, airline/lead-time chart tooltips, map hover/click detail, CSV export, and the OTA scrape action.
4. Run the Python test suite in an environment containing the declared dependencies.
5. Keep generated caches, build output, and empty archive folders out of version control.

## Known Boundaries

The checked-in OTA run is an imported local collection, not a live production scraper. A production rollout still needs permissioned adapters, resilient scheduling, secrets management, rate limiting, retries, observability, durable storage, and compliance review. Direct airline sources remain visibly disabled until those controls exist.
