# Scraping Mechanism

## Source Model

All source adapters normalize into the common dataclasses in `airfare/sources/base.py`: `SearchRequest`, `StandardFareRecord`, `FareComponents`, and `AdapterResult`.

## CompareFlights

`scraper/ota/compareflights.py` follows the live CompareFlights white-label search flow: it requests a search signature, starts the search, polls the source result endpoint, and normalizes ticket legs and proposals into airline, flight, fare, baggage, stop, and lead-time observations. Results are saved under `data/raw_airfare/compareflights/YYYY-MM-DD/ROUTE.json` with all configured T+n windows in one file. `airfare/sources/compareflights/offline_adapter.py` remains the compatibility parser for historical route files and the new live format.

## Ixigo Airfare

`scraper/ota/ixigo.py` opens an Ixigo search with Selenium Chromium, listens for the `/flights/v2/search/stream` response, parses JSON/SSE/concatenated frames, validates route segments, deduplicates observations, and writes date-partitioned JSON under `data/raw_airfare/ixigo/`. All T+ windows for a route are upserted into one route file, with a small `collection.json` manifest for the date. It does not bypass CAPTCHA, rotate identities, evade access controls, or fabricate fare components.

## Ixigo Holidays

`scraper/ota/ixigo_holiday.py` fetches `https://www.ixigo.com/growth/api/v1/holidayCalendar` with a descriptive user agent and saves the original JSON response to `data/raw_airfare/ixigo/holidays.json`. The cached output is used as source context for holiday and seasonality work.

## Scheduling and Scope

The worker runs CompareFlights and Ixigo as separate APScheduler jobs. Their times are independent. `daily_route_pairs` is expanded through the DGCA basket: in bidirectional mode, Top 20 pairs becomes 40 directed searches for each source. Each route is collected for the configured lead windows, so five windows produce 200 route-window tasks for 40 directions. The dashboard's Settings page writes the shared schedule file and displays the calculated count. A stopped or interrupted job reuses successful route-window files and retries only unfinished windows.

Ixigo uses Selenium Chromium. The source-specific headless setting can be enabled for unattended runs or disabled for an attended diagnostic run. Stream responses are captured as bytes, parsed across JSON, concatenated JSON, and SSE envelopes, and validated against the requested origin and destination. HTTP failures and rate limits are reported as `SOURCE_ERROR`; an HTTP-success response with no matching journeys is reported as `NO_DATA`.

## Useful Commands

```powershell
pip install -r requirements-scrapers.txt
Install Chrome or Chromium on the host and ensure it is available on `PATH`.
python -m scraper.ota.ixigo_holiday
python -m scraper.ota.ixigo DEL BOM --date 10102026 --headless
python scripts/run_worker.py
```
