# Backend Mechanism

## Service

`backend/app/main.py` is a FastAPI service backed locally by `database/sqlite_store.py`. It serves index, route, airline, map, source, DGCA, quality, forecast, analytics, and scraper-job endpoints.

## Important Endpoint Groups

- `/api/index/*`: current value, history, revisions, and movers.
- `/api/routes`, `/api/routes/analytics`, `/api/routes/{origin}/{destination}`: basket, route-airline summaries, offers, and lead-time data.
- `/api/airlines`, `/api/airlines/{code}`, `/api/airlines/{code}/routes`: airline summaries and Top N histories.
- `/api/analytics`: fare distribution, route ranking, and seasonality.
- `/api/scraper-config`: shared daily route count, direction, and source times.
- `/api/scrape/compareflights`, `/api/scrape/ixigo`: start background collection jobs.
- `/api/scrape/status/{job_id}`: route-by-route progress and timestamps.
- `/api/source-health`, `/api/dgca/status`: monitoring and publication-cycle state.

## Background Jobs

On-demand scraper requests return immediately with a job id. A daemon thread processes the route queue while the browser can navigate elsewhere. The status payload contains source, status, total routes, completed routes, observations, and each route's queued/running/completed time. The long-lived worker uses independent APScheduler jobs for CompareFlights and Ixigo.

`/api/source-health` keeps source identities separate. Each configured source includes its URL, current source status, job status, job id, last run, last successful run, completed/total routes, successful routes, and source-error count. A local backcast is labeled `LOCAL_BACKCAST` and is never presented as a live scraper.

## Configuration

`data/runtime/scraper_config.json` is the shared local configuration. The API validates route count and direction mode before writing it. Environment variables remain available for deployment defaults. CORS is configured through `CORS_ORIGINS`.

## Run and Inspect

```powershell
$env:PYTHONPATH = "."
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/scraper-config
curl http://127.0.0.1:8000/api/routes/analytics
```

The API intentionally reports unavailable fields when a source does not expose them and keeps the research disclaimer in index responses.
