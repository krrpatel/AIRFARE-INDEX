# Airfare Price Index Dashboard

An auditable research dashboard for Indian domestic airfares. It combines a DGCA passenger-weighted route basket, normalized OTA observations, route and airline analysis, lead-time views, source health, and quality monitoring.

This is a research indicator, not an official MoSPI/CPI statistic. Missing fare components are shown as unavailable; values are never invented to fill a source gap.

## Quick Start

On Windows, run `setup.bat` once and then `start.bat` to open the backend and frontend automatically. On Linux, use the documented `setup.sh` package setup, activate `.venv`, then start the backend and frontend commands below.

### Windows batch-file safety

Only mark these files safe when they came from this project and you have reviewed them:

1. Right-click `setup.bat` or `start.bat`, choose `Properties`, select `Unblock` if shown, then choose `Apply`.
2. If Windows Security quarantines a file, open **Protection history**, inspect the detection path, and allow it only when it is the expected project file.
3. From PowerShell, an administrator can remove the downloaded-file mark with `Unblock-File -Path .\setup.bat, .\start.bat`.
4. Do not disable Defender globally. Run the scripts from the project directory and keep the two command windows visible while troubleshooting.

Open two PowerShell terminals from the project directory.

### 1. Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Prepare or refresh the local index database

The repository includes `database/airfare_demo.db`. To regenerate the deterministic research dataset:

```powershell
$env:PYTHONPATH = "."
python scripts/run_pipeline_demo.py --start 2026-01-01 --end 2026-08-24 --seed 42
```

### 3. Start the backend

```powershell
$env:PYTHONPATH = "."
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend API and interactive API documentation:

- Dashboard API: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/api/health`

### 4. Start the frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:3000`.

For a production frontend build:

```powershell
cd frontend
npm run build
npm run start
```

## Scraper Setup

The active local OTA source is the normalized CompareFlights import. Ixigo collection is implemented as a permission-aware Selenium Chromium stream collector. Install its scraper dependencies only when running Ixigo:

```powershell
pip install -r requirements-scrapers.txt
Install Chrome or Chromium on the VPS and ensure it is available on `PATH`.
```

Ixigo uses Selenium Chromium/CDP capture. Each source has an independent `headless` setting in the dashboard: `true` hides its browser, while `false` opens a visible browser for diagnostics. A source-side HTTP 403 or rate limit is recorded as `SOURCE_ERROR`, not as a false `NO_DATA` result.

VPS stability defaults are also stored in `data/runtime/scraper_config.json`: 1920x1080 viewport, a shared maximum parallel-driver count, a 3-second route delay, and two bounded retries. Ixigo writes one route file per date containing all T+ lead-time windows, plus a small date-level collection manifest. These settings reduce accidental request bursts; they do not bypass source access controls.

Run the holiday calendar collector:

```powershell
$env:PYTHONPATH = "."
python -m scraper.ota.ixigo_holiday
```

Run one Ixigo route manually:

```powershell
$env:PYTHONPATH = "."
python -m scraper.ota.ixigo DEL BOM --date 10102026 --headless
```

The dashboard Settings page provides independent background actions for CompareFlights and Ixigo. Direct airline scrapers remain disabled until their permissions and collection paths are approved.

## Worker Scheduling

The worker reads `data/runtime/scraper_config.json` at startup. The Settings page writes the same file through `/api/scraper-config`.

```powershell
$env:PYTHONPATH = "."
python scripts/run_worker.py
```

Default schedule:

- CompareFlights: `06:00` local time
- Ixigo: `06:30` local time
- DGCA basket check: day 1 at `09:30`
- Daily source scope: 20 DGCA pairs in bidirectional mode = 40 directed routes per source
- Browser collection is queued in the background and continues when the dashboard page changes

Environment variables can override defaults for deployment, including `COMPAREFLIGHTS_SCRAPE_HOUR`, `COMPAREFLIGHTS_SCRAPE_MINUTE`, `IXIGO_SCRAPE_HOUR`, `IXIGO_SCRAPE_MINUTE`, `DAILY_ROUTE_PAIRS`, `DGCA_TOP_N`, and `DGCA_DIRECTION_MODE`.

## Tests and Checks

```powershell
$env:PYTHONPATH = "."
python -m compileall -q backend database airfare scraper scripts index_engine data_pipeline ml
pytest tests -q
cd frontend
npm run build
```

## Documentation

- [ML mechanism](docs/ML_MECHANISM.md)
- [Pipeline mechanism](docs/PIPELINE_MECHANISM.md)
- [Scraping mechanism](docs/SCRAPING_MECHANISM.md)
- [Frontend mechanism](docs/FRONTEND_MECHANISM.md)
- [Backend mechanism](docs/BACKEND_MECHANISM.md)
- [Methodology](docs/METHODOLOGY.md)
- [API reference](docs/API.md)

## Repository Map

```text
airfare/       DGCA basket, airport registry, source contracts
backend/       FastAPI service and API endpoints
data/          DGCA files, imported airfare, runtime scraper settings
data_pipeline/ Validation, outlier handling, representative fare preparation
database/      SQLite persistence and PostgreSQL target schema
frontend/      Next.js dashboard, charts, Leaflet map, settings
index_engine/  Basket weights, relatives, index, alerts, revisions
ml/            Forecasting and anomaly detection
scraper/       OTA and airline source adapters
scripts/       Pipeline runner, worker, inspection utilities
tests/         Automated unit tests
```

## Limitations

The checked-in database contains research/backcast observations and an imported OTA run. A production deployment still needs permissioned live-source credentials or APIs, durable job storage, retry/observability infrastructure, database migrations, and compliance review for every source. The worker never bypasses CAPTCHA, robots rules, access controls, or terms of service.
