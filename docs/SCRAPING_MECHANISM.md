# Scraping Mechanism

## Source Model

All source adapters normalize into the common dataclasses in `airfare/sources/base.py`: `SearchRequest`, `StandardFareRecord`, `FareComponents`, and `AdapterResult`.

## CompareFlights

`airfare/sources/compareflights/offline_adapter.py` reads the saved normalized JSON run under `data/raw_airfare/compareflights/`, validates route and date fields, and exposes available offers, lead time, airline, flight, fare code, baggage, and payable fare. The current local integration is an imported run, not an unrestricted live scraper.

## Ixigo Airfare

`scraper/ota/ixigo.py` opens an Ixigo search with Playwright, listens for the `/flights/v2/search/stream` response, parses JSON/SSE/concatenated frames, validates route segments, deduplicates observations, and writes JSON under `data/raw_airfare/ixigo/`. It does not bypass CAPTCHA, rotate identities, evade access controls, or fabricate fare components.

## Ixigo Holidays

`scraper/ota/ixigo_holiday.py` fetches `https://www.ixigo.com/growth/api/v1/holidayCalendar` with a descriptive user agent and saves the original JSON response to `data/raw_airfare/ixigo/holidays.json`. The cached output is used as source context for holiday and seasonality work.

## Scheduling and Scope

The worker runs CompareFlights and Ixigo as separate APScheduler jobs. Their times are independent. `daily_route_pairs` is expanded through the DGCA basket: in bidirectional mode, Top 20 pairs becomes 40 directed searches for each source. The dashboard's Settings page writes the shared schedule file and displays the calculated count.

Ixigo uses Playwright in forced headless mode. The configured engine can be Chromium, Firefox, or WebKit, provided that browser has been installed with Playwright. Stream responses are captured as bytes, parsed across JSON, concatenated JSON, and SSE envelopes, and validated against the requested origin and destination. HTTP failures and rate limits are reported as `SOURCE_ERROR`; an HTTP-success response with no matching journeys is reported as `NO_DATA`.

## Useful Commands

```powershell
pip install -r requirements-scrapers.txt
Install Chrome or Chromium on the host and ensure it is available on `PATH`.
python -m scraper.ota.ixigo_holiday
python -m scraper.ota.ixigo DEL BOM --date 10102026 --headless
python scripts/run_worker.py
```
