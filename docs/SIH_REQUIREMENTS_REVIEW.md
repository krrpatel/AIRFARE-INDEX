# SIH Requirements Review

This review maps the current implementation to the SIH26056 expectations checked on 2026-09-02.

| Requirement area | Current implementation | Status |
|---|---|---|
| Daily airfare index dashboard | FastAPI + Next.js dashboard with national index, route views, airline view, and map. | Implemented prototype |
| DGCA route selection | DGCA pipeline is integrated under `airfare/dgca`, with cached Top 50 route-pair basket in `data/dgca/output/top50.json`. | Implemented |
| Representative city pairs and weights | Route-pair weights are loaded from DGCA output; bidirectional collection splits pair weight evenly by direction. | Implemented |
| T+1/T+7/T+15/T+30/T+45 windows | CompareFlights offline adapter imports the available 2026-08-26 observed output and `/api/lead-time` summarizes windows with real rows only. | Implemented for imported sample |
| Base fare, taxes, total fare | Current source exposes total/offer fares, fare code, and baggage fields, but not separate base fare or tax components. Missing components are stored/documented as `NULL`. | Partial, honestly disclosed |
| Ethical multi-source scraping | CompareFlights browser-capture adapter exists from prior source; production permission and terms review remain external confirmations. | Partial |
| Cleaned/deduplicated database | Local SQLite demo supports index-ready observations; expanded production schema documents raw preservation and nullable fare components. | Partial |
| ML/research component | Forecasting and anomaly modules exist; forecast endpoint trains on stored valid route observations when enough rows exist. | Implemented prototype |
| Data quality | Quality statuses and anomaly flags are exposed; availability/source statuses are present in source adapter dataclasses. | Partial |
| 30-day benchmark/backtest | Simulated backcast is clearly labeled; external DGCA average-fare benchmark still needs a verified public source. | Open |

## Remaining SIH Work

- Confirm source permissions before live scheduled scraping.
- Add a permitted live airline/OTA source adapter or official feed if available.
- Import future observed runs into the normalized database so live index values can replace the current simulated backcast track.
- Add a verified public benchmark dataset for the required 30-day comparison, or document why it is unavailable.
