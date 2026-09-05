"""
FastAPI backend for the Airfare Price Index.

Data layer: uses database/sqlite_store.py (stdlib sqlite3, no extra
dependencies) against the file produced by scripts/run_pipeline_demo.py.
This is the LOCAL/DEMO path. Production deployment (Docker + Postgres)
swaps this for backend/app/db/queries.py + models.py -- same response
shapes, different storage -- once that path is validated against a live
Postgres instance (not available in the environment this was built in).
"""
import logging
import os
import asyncio
import threading
import time
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid
from datetime import date
from collections import defaultdict

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import sqlite_store
from airfare.airports.registry import airport_coords, require_domestic_route
from airfare.dgca.basket import load_basket_metadata, load_route_basket
from airfare.sources.compareflights.offline_adapter import CompareFlightsOfflineAdapter
from airfare.sources.run_adapter import SOURCE_LABELS, SourceRunAdapter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("airfare_api")

DATA_MODE = os.getenv("MODE", "research")  # 'research' | 'live'
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "database/airfare_demo.db")
DGCA_TOP_N = int(os.getenv("DGCA_TOP_N", "50"))
DGCA_DIRECTION_MODE = os.getenv("DGCA_DIRECTION_MODE", "bidirectional")
SCRAPE_JOBS: dict[str, dict] = {}
SCRAPE_LOCK = threading.RLock()
ROUTE_DETAIL_CACHE: dict[tuple[str, str, str], dict] = {}
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SCRAPE_STATUS_PATH = os.path.join(PROJECT_ROOT, "data", "runtime", "status.json")
SCRAPER_CONFIG_PATH = os.path.join(PROJECT_ROOT, "data", "runtime", "scraper_config.json")
DEFAULT_SCRAPER_CONFIG = {"daily_route_pairs": 20, "compareflights_time": "06:00", "ixigo_time": "06:30", "direction_mode": "bidirectional", "ixigo_driver": "chromium", "compareflights_headless": False, "ixigo_headless": False, "scraper_driver_count": 4, "ixigo_lead_days": [1, 7, 15, 30, 45], "ixigo_route_delay_seconds": 3, "ixigo_retry_count": 2, "ixigo_viewport_width": 1920, "ixigo_viewport_height": 1080}
SOURCE_RUN_ADAPTER = SourceRunAdapter()
SOURCE_DATA_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def _write_scrape_status(job: dict) -> None:
    os.makedirs(os.path.dirname(SCRAPE_STATUS_PATH), exist_ok=True)
    jobs = {}
    for job_id, current in SCRAPE_JOBS.items():
        jobs[job_id] = _safe_job(current)
    with open(SCRAPE_STATUS_PATH, "w", encoding="utf-8") as handle:
        json.dump({"updated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"), "jobs": jobs}, handle, indent=2)


def _update_job(job_id: str, **changes) -> None:
    with SCRAPE_LOCK:
        job = SCRAPE_JOBS.get(job_id)
        if job:
            job.update(changes)
            _write_scrape_status(job)


def _driver_budget(source: str) -> int:
    config = _scraper_config()
    total = max(1, int(config.get("scraper_driver_count", 4)))
    with SCRAPE_LOCK:
        active_sources = {job.get("source") for job in SCRAPE_JOBS.values() if job.get("status") in {"QUEUED", "RUNNING", "STOPPING"}}
    if len(active_sources) > 1:
        return max(1, total // 2)
    return total


def _active_job(source: str):
    return next((job for job in SCRAPE_JOBS.values() if job.get("source") == source and job.get("status") in {"QUEUED", "RUNNING", "STOPPING"}), None)


def _now() -> str:
    return __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_job(job: dict) -> dict:
    return {key: value for key, value in job.items() if key != "stop_event"}


def _previous_tasks(source: str, scrape_date: str) -> dict:
    try:
        with open(SCRAPE_STATUS_PATH, encoding="utf-8") as handle:
            jobs = json.load(handle).get("jobs", {})
        candidates = [job for job in jobs.values() if job.get("source") == source and job.get("scrape_date") == scrape_date]
        if not candidates:
            return {}
        latest = max(candidates, key=lambda job: job.get("created_at", ""))
        return {task.get("task"): task for task in latest.get("tasks", []) if task.get("task")}
    except (OSError, ValueError, TypeError):
        return {}


def _scraper_config() -> dict:
    try:
        with open(SCRAPER_CONFIG_PATH, encoding="utf-8") as handle:
            saved = __import__("json").load(handle)
        config = {**DEFAULT_SCRAPER_CONFIG, **saved}
        config.pop("compareflights_driver_count", None)
        config.pop("ixigo_driver_count", None)
        return config
    except (OSError, ValueError):
        return dict(DEFAULT_SCRAPER_CONFIG)


def _source_routes() -> list:
    config = _scraper_config()
    return load_route_basket(top_n=int(config["daily_route_pairs"]), direction_mode=config["direction_mode"])

app = FastAPI(
    title="Airfare Price Index API",
    description=(
        "Working data API. Serves a research Airfare Price Index "
        "— not an official MoSPI/CPI statistic. See /docs and "
        "docs/METHODOLOGY.md in the repository."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3003,http://127.0.0.1:3003").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


DISCLAIMER = "Research methodology. Not an official MoSPI/CPI index. See docs/METHODOLOGY.md."


class HealthResponse(BaseModel):
    status: str
    mode: str


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", mode=DATA_MODE)


@app.get("/api/index/current")
def index_current():
    row = sqlite_store.get_current_index(SQLITE_DB_PATH)
    if row is None:
        raise HTTPException(status_code=404, detail="No index computed yet. Run scripts/run_pipeline_demo.py first.")
    row["disclaimer"] = DISCLAIMER
    return row


@app.get("/api/index/history")
def index_history(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
):
    rows = sqlite_store.get_index_history(SQLITE_DB_PATH, start, end)
    return {"count": len(rows), "disclaimer": DISCLAIMER, "series": rows}


@app.get("/api/routes")
def list_routes():
    rows = sqlite_store.get_routes(SQLITE_DB_PATH)
    if rows:
        return rows
    # Fall back to the DGCA-derived basket if the DB hasn't been populated yet.
    return [
        {"origin": r.origin, "destination": r.destination, "label": r.label, "weight": r.directional_weight}
        for r in load_route_basket(top_n=DGCA_TOP_N, direction_mode=DGCA_DIRECTION_MODE)
    ]


@app.get("/api/routes/analytics")
def route_analytics(
    start: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    end: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    limit: int = Query(500, ge=1, le=5000, description="Max (route, airline) rows to return"),
    offset: int = Query(0, ge=0),
):
    return {
        "routes": sqlite_store.get_route_analytics(SQLITE_DB_PATH, start, end, limit, offset),
        "date_range": sqlite_store.get_observation_date_bounds(SQLITE_DB_PATH),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/analytics")
def analytics(
    start: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    end: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    limit: int = Query(500, ge=1, le=5000, description="Max route_ranking rows to return"),
    offset: int = Query(0, ge=0),
):
    return {
        **sqlite_store.get_analytics(SQLITE_DB_PATH, start, end, limit, offset),
        "date_range": sqlite_store.get_observation_date_bounds(SQLITE_DB_PATH),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/routes/{origin}/{destination}")
def route_detail(origin: str, destination: str):
    try:
        require_domestic_route(origin, destination)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    adapter = CompareFlightsOfflineAdapter()
    cache_key = (origin.upper(), destination.upper(), adapter._run_dir().name)
    if cache_key in ROUTE_DETAIL_CACHE:
        return ROUTE_DETAIL_CACHE[cache_key]
    detail = sqlite_store.get_route_detail(SQLITE_DB_PATH, origin.upper(), destination.upper())
    if detail is None:
        basket_route = next(
            (item for item in load_route_basket(top_n=100, direction_mode="bidirectional")
             if item.origin == origin.upper() and item.destination == destination.upper()),
            None,
        )
        if basket_route is None:
            raise HTTPException(status_code=404, detail=f"Route {origin}-{destination} not found in basket.")
        detail = {"label": basket_route.label, "history": []}
    imported = [
        record for record in adapter.iter_all_records()
        if record.origin == origin.upper() and record.destination == destination.upper()
        and record.availability_status == "AVAILABLE"
    ]
    fares = [record.components.total_payable_fare or record.components.offered_fare for record in imported]
    fares = [fare for fare in fares if fare is not None]
    airline_rows = defaultdict(lambda: {"airline_name": None, "fares": [], "records": 0})
    lead_rows = defaultdict(lambda: defaultdict(list))
    for record in imported:
        key = record.airline_code or "UNKNOWN"
        airline_rows[key]["airline_name"] = record.airline_name or key
        airline_rows[key]["records"] += 1
        fare = record.components.total_payable_fare or record.components.offered_fare
        if fare is not None:
            airline_rows[key]["fares"].append(fare)
            lead_rows[key][record.advance_purchase_days].append(fare)
    detail["route"] = {"origin": origin.upper(), "destination": destination.upper()}
    detail["market_snapshot"] = {
        "source": "CompareFlights imported observations",
        "observation_count": len(imported),
        "fare_count": len(fares),
        "lowest_payable_fare": round(min(fares), 2) if fares else None,
        "average_payable_fare": round(sum(fares) / len(fares), 2) if fares else None,
        "airlines": [
            {
                "airline_code": code,
                "airline_name": row["airline_name"],
                "record_count": row["records"],
                "lowest_fare": round(min(row["fares"]), 2) if row["fares"] else None,
                "average_fare": round(sum(row["fares"]) / len(row["fares"]), 2) if row["fares"] else None,
            }
            for code, row in sorted(airline_rows.items())
        ],
        "lead_time_series": [
            {
                "route": code,
                "label": row["airline_name"],
                "series": [
                    {"date": f"T+{days}", "value": round(sum(fares_for_day) / len(fares_for_day), 2), "observation_count": len(fares_for_day)}
                    for days, fares_for_day in sorted(lead_rows[code].items())
                ],
            }
            for code, row in sorted(airline_rows.items())
            if lead_rows[code]
        ],
        "fare_components": {
            "base_fare": None,
            "taxes": None,
            "airport_charges": None,
            "mandatory_total": "Not separately exposed by the imported source",
        },
        "note": "Values are computed from imported available offers. Base fare, taxes and statutory fee components remain unavailable until the source exposes them separately.",
    }
    detail["offers"] = [
        {
            "airline_code": record.airline_code,
            "airline_name": record.airline_name or record.airline_code,
            "flight_number": record.flight_number,
            "travel_date": record.travel_date.isoformat(),
            "advance_purchase_days": record.advance_purchase_days,
            "departure": record.departure_datetime.isoformat() if record.departure_datetime else None,
            "arrival": record.arrival_datetime.isoformat() if record.arrival_datetime else None,
            "duration_minutes": record.duration_minutes,
            "stops": record.stops,
            "fare_code": record.fare_code,
            "currency": record.currency,
            "payable_fare": record.components.total_payable_fare or record.components.offered_fare,
            "checkin_baggage_kg": record.baggage.get("checkin_baggage_kg"),
            "cabin_baggage_kg": record.baggage.get("cabin_baggage_kg"),
            "source": record.source_name,
        }
        for record in sorted(imported, key=lambda item: item.components.total_payable_fare or item.components.offered_fare or float("inf"))[:100]
    ]
    detail["disclaimer"] = DISCLAIMER
    ROUTE_DETAIL_CACHE[cache_key] = detail
    return detail


@app.get("/api/data-quality")
def data_quality():
    return sqlite_store.get_data_quality_summary(SQLITE_DB_PATH)


@app.get("/api/source-health")
def source_health():
    sources = sqlite_store.get_source_health(SQLITE_DB_PATH)
    known = {item["name"] for item in sources}
    latest_jobs = {}
    with SCRAPE_LOCK:
        for job in SCRAPE_JOBS.values():
            source_key = "ixigo" if job.get("source") == "Ixigo OTA" else "compareflights" if job.get("source") == "CompareFlights OTA" else None
            if source_key and (source_key not in latest_jobs or job.get("created_at", "") > latest_jobs[source_key].get("created_at", "")):
                latest_jobs[source_key] = _safe_job(job)
    try:
        with open(SCRAPE_STATUS_PATH, encoding="utf-8") as handle:
            persisted_jobs = json.load(handle).get("jobs", {})
        for job in persisted_jobs.values():
            source_key = "ixigo" if job.get("source") == "Ixigo OTA" else "compareflights" if job.get("source") == "CompareFlights OTA" else None
            if source_key and (source_key not in latest_jobs or job.get("created_at", "") > latest_jobs[source_key].get("created_at", "")):
                latest_jobs[source_key] = _safe_job(job)
    except (OSError, ValueError, TypeError):
        pass
    for name, label, url in [("compareflights", "CompareFlights OTA", "https://compareflights.co.in"), ("ixigo", "Ixigo OTA", "https://www.ixigo.com"), ("ixigo_holiday", "Ixigo holiday calendar", "https://www.ixigo.com/growth/api/v1/holidayCalendar")]:
        if name not in known:
            sources.append({"name": name, "label": label, "status": "CONFIGURED" if name != "ixigo_holiday" else "ONLINE", "source_url": url, "last_success": None})
        job = latest_jobs.get(name)
        if job:
            entry = next(item for item in sources if item["name"] == name)
            route_statuses = [str(item.get("status")) for item in (job.get("tasks") or job.get("routes", []))]
            success_count = sum(status == "SUCCESS" for status in route_statuses)
            error_count = sum(status == "SOURCE_ERROR" for status in route_statuses)
            source_status = "ONLINE" if success_count else "SOURCE_ERROR" if error_count else job.get("status", "UNKNOWN")
            entry.update({"status": source_status, "job_status": job.get("status", "UNKNOWN"), "job_id": job.get("job_id"), "last_run": job.get("created_at"), "last_success": job.get("finished_at") if success_count else entry.get("last_success"), "completed_routes": job.get("completed_routes", 0), "total_routes": job.get("total_tasks", job.get("total_routes", 0)), "successful_routes": success_count, "source_errors": error_count, "headless": job.get("headless", False), "driver_count": job.get("driver_count", 0)})
    return sources


@app.get("/api/scraper-config")
def scraper_config():
    config = _scraper_config()
    config["directed_route_count"] = int(config["daily_route_pairs"]) * 2 if config["direction_mode"] == "bidirectional" else int(config["daily_route_pairs"])
    return config


@app.put("/api/scraper-config")
def update_scraper_config(payload: dict = Body(...)):
    config = {**_scraper_config(), **payload}
    config.pop("compareflights_driver_count", None)
    config.pop("ixigo_driver_count", None)
    config["daily_route_pairs"] = max(1, min(100, int(config["daily_route_pairs"])))
    if config["direction_mode"] not in {"bidirectional", "unidirectional", "merged"}:
        raise HTTPException(status_code=400, detail="Invalid direction mode")
    if config.get("ixigo_driver") != "chromium":
        raise HTTPException(status_code=400, detail="Ixigo uses the Selenium Chromium driver")
    config["ixigo_route_delay_seconds"] = max(0, min(60, int(config.get("ixigo_route_delay_seconds", 3))))
    config["ixigo_retry_count"] = max(0, min(5, int(config.get("ixigo_retry_count", 2))))
    config["scraper_driver_count"] = max(1, min(16, int(config.get("scraper_driver_count", 4))))
    config["compareflights_headless"] = bool(config.get("compareflights_headless", False))
    config["ixigo_headless"] = bool(config.get("ixigo_headless", False))
    config["ixigo_lead_days"] = sorted({int(value) for value in config.get("ixigo_lead_days", [1, 7, 15, 30, 45]) if int(value) > 0}) or [1]
    config["ixigo_viewport_width"] = max(1280, min(3840, int(config.get("ixigo_viewport_width", 1920))))
    config["ixigo_viewport_height"] = max(720, min(2160, int(config.get("ixigo_viewport_height", 1080))))
    os.makedirs(os.path.dirname(SCRAPER_CONFIG_PATH), exist_ok=True)
    with open(SCRAPER_CONFIG_PATH, "w", encoding="utf-8") as handle:
        __import__("json").dump(config, handle, indent=2)
    return scraper_config()


def _run_ota_job(job_id: str) -> None:
    try:
        adapter = CompareFlightsOfflineAdapter()
        records = list(adapter.iter_all_records())
        route_groups = defaultdict(list)
        for record in records:
            route_groups[f"{record.origin}-{record.destination}"].append(record)
        configured = [f"{item.origin}-{item.destination}" for item in _source_routes()]
        SOURCE_RUN_ADAPTER.write_manifest("compareflights", SCRAPE_JOBS[job_id]["run_date"])
        with SCRAPE_LOCK:
            SCRAPE_JOBS[job_id].update({"total_routes": len(configured), "completed_routes": 0, "record_count": len(records), "available_count": sum(r.availability_status == "AVAILABLE" for r in records), "routes": [{"route": route, "status": "QUEUED", "record_count": len(route_groups.get(route, [])), "available_count": sum(r.availability_status == "AVAILABLE" for r in route_groups.get(route, [])), "started_at": None, "completed_at": None} for route in configured]})
            _write_scrape_status(SCRAPE_JOBS[job_id])
        for route in configured:
            if SCRAPE_JOBS[job_id]["stop_event"].is_set():
                break
            started = _now()
            with SCRAPE_LOCK:
                item = next(row for row in SCRAPE_JOBS[job_id]["routes"] if row["route"] == route)
                item.update({"status": "RUNNING", "started_at": started})
            with SCRAPE_LOCK:
                item.update({
                    "status": "SUCCESS" if item["record_count"] else "NO_DATA",
                    "reason": "Source records available" if item["record_count"] else "No imported records for this route in the latest CompareFlights run",
                    "completed_at": _now(),
                })
                SCRAPE_JOBS[job_id]["completed_routes"] += 1
                _write_scrape_status(SCRAPE_JOBS[job_id])
        with SCRAPE_LOCK:
            SCRAPE_JOBS[job_id].update({"status": "STOPPED" if SCRAPE_JOBS[job_id]["stop_event"].is_set() else "COMPLETED", "finished_at": _now()})
            _write_scrape_status(SCRAPE_JOBS[job_id])
    except Exception as exc:
        with SCRAPE_LOCK:
            SCRAPE_JOBS[job_id].update({"status": "ERROR", "error": str(exc)})
            _write_scrape_status(SCRAPE_JOBS[job_id])


@app.post("/api/scrape/{source_name}")
def run_source_scrape(source_name: str):
    """Run the currently enabled local source adapter on demand.

    The checked-in source is an imported CompareFlights run. Direct airline
    adapters stay disabled until their terms and collection permissions are
    configured.
    """
    if source_name.lower() == "ixigo":
        return run_ixigo_scrape()
    if source_name.lower() != "compareflights":
        raise HTTPException(status_code=409, detail="Direct airline scrapers are coming soon.")
    with SCRAPE_LOCK:
        active = _active_job("CompareFlights OTA")
        if active:
            return {"job_id": active["job_id"], "status": active["status"], "message": "An OTA collection is already running."}
        job_id = uuid.uuid4().hex
        latest_run = CompareFlightsOfflineAdapter()._run_dir().name
        config = _scraper_config()
        SCRAPE_JOBS[job_id] = {"job_id": job_id, "source": "CompareFlights OTA", "run_date": latest_run, "scrape_date": date.today().isoformat(), "status": "QUEUED", "created_at": _now(), "started_at": _now(), "headless": bool(config.get("compareflights_headless", False)), "driver_count": _driver_budget("compareflights"), "total_routes": 0, "completed_routes": 0, "routes": [], "stop_event": threading.Event()}
        _write_scrape_status(SCRAPE_JOBS[job_id])
    threading.Thread(target=_run_ota_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "source": "CompareFlights OTA", "status": "QUEUED", "message": "OTA collection started in the background."}


def _run_ixigo_task(job_id: str, task: dict, config: dict) -> None:
    from datetime import timedelta
    from scraper.ota.ixigo import collect_route, save_route_result
    job = SCRAPE_JOBS[job_id]
    item = task
    if item.get("status") == "SUCCESS":
        return
    if job["stop_event"].is_set():
        item.update({"status": "STOPPED"})
        return
    item.update({"status": "RUNNING", "started_at": _now()})
    max_attempts = max(1, int(config.get("ixigo_retry_count", 2)) + 1)
    result = None
    for attempt in range(item.get("attempts", 0) + 1, max_attempts + 1):
        if job["stop_event"].is_set():
            item.update({"status": "STOPPED"})
            return
        item["attempts"] = attempt
        try:
            result = asyncio.run(collect_route(task["origin"], task["destination"], task["travel_date"], headless=bool(config.get("ixigo_headless", False)), browser_engine="chromium", viewport=(int(config.get("ixigo_viewport_width", 1920)), int(config.get("ixigo_viewport_height", 1080)))))
            result.update({"lead_days": task["lead_days"], "lead_label": task["lead_label"], "travel_date": f"{task['travel_date'][4:8]}-{task['travel_date'][2:4]}-{task['travel_date'][:2]}"})
            save_route_result(result, run_date=job["scrape_date"], suffix=task["lead_label"])
            if result.get("status") == "SUCCESS" or attempt == max_attempts:
                break
        except Exception as exc:
            result = {"status": "SOURCE_ERROR", "reason": str(exc), "observation_count": 0}
        if attempt < max_attempts:
            time.sleep(min(30, 2 ** (attempt - 1) * 5))
    result = result or {"status": "SOURCE_ERROR", "reason": "No result", "observation_count": 0}
    count = int(result.get("observation_count", 0))
    item.update({"status": result.get("status", "SOURCE_ERROR"), "reason": result.get("reason", ""), "record_count": count, "available_count": count, "completed_at": _now()})
    with SCRAPE_LOCK:
        job["completed_tasks"] = sum(row.get("status") in {"SUCCESS", "NO_DATA", "SOURCE_ERROR", "STOPPED"} for row in job["tasks"])
        job["record_count"] = sum(int(row.get("record_count", 0)) for row in job["tasks"])
        job["available_count"] = sum(int(row.get("available_count", 0)) for row in job["tasks"])
        _write_scrape_status(job)


def _run_ixigo_job(job_id: str) -> None:
    try:
        from datetime import date, timedelta
        from scraper.ota.ixigo import consolidate_legacy_run
        config = _scraper_config()
        routes = _source_routes()
        lead_days = sorted({int(value) for value in config.get("ixigo_lead_days", [1, 7, 15, 30, 45]) if int(value) > 0}) or [1]
        scrape_date = SCRAPE_JOBS[job_id]["scrape_date"]
        consolidate_legacy_run(scrape_date)
        tasks = []
        for route in routes:
            for days in lead_days:
                tasks.append({"task": f"{route.origin}-{route.destination}|T+{days}", "route": f"{route.origin}-{route.destination}", "origin": route.origin, "destination": route.destination, "lead_days": days, "lead_label": f"T+{days}", "travel_date": (date.fromisoformat(scrape_date) + timedelta(days=days)).strftime("%d%m%Y"), "status": "QUEUED", "attempts": 0, "record_count": 0, "available_count": 0, "started_at": None, "completed_at": None})
        previous = _previous_tasks("Ixigo OTA", scrape_date)
        for task in tasks:
            prior = previous.get(task["task"])
            if prior and prior.get("status") == "SUCCESS":
                task.update({key: prior.get(key) for key in ("status", "attempts", "record_count", "available_count", "started_at", "completed_at", "reason") if key in prior})
            elif prior and int(prior.get("attempts", 0)) < max(1, int(config.get("ixigo_retry_count", 2)) + 1):
                task.update({"attempts": int(prior.get("attempts", 0)), "reason": "Resuming incomplete task from previous run"})
        with SCRAPE_LOCK:
            completed_tasks = sum(row.get("status") in {"SUCCESS", "NO_DATA", "SOURCE_ERROR", "STOPPED"} for row in tasks)
            record_count = sum(int(row.get("record_count", 0)) for row in tasks)
            available_count = sum(int(row.get("available_count", 0)) for row in tasks)
            completed_routes = len({row["route"] for row in tasks if row.get("status") in {"SUCCESS", "NO_DATA", "SOURCE_ERROR", "STOPPED"}})
            SCRAPE_JOBS[job_id].update({"status": "RUNNING", "lead_days": lead_days, "headless": bool(config.get("ixigo_headless", False)), "driver_count": _driver_budget("ixigo"), "total_routes": len(routes), "total_tasks": len(tasks), "completed_routes": completed_routes, "completed_tasks": completed_tasks, "record_count": record_count, "available_count": available_count, "tasks": tasks, "routes": tasks})
            _write_scrape_status(SCRAPE_JOBS[job_id])
        workers = max(1, int(SCRAPE_JOBS[job_id]["driver_count"]))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ixigo-driver") as pool:
            futures = [pool.submit(_run_ixigo_task, job_id, task, config) for task in tasks]
            for future in as_completed(futures):
                future.result()
        with SCRAPE_LOCK:
            job = SCRAPE_JOBS[job_id]
            job["completed_tasks"] = sum(row.get("status") in {"SUCCESS", "NO_DATA", "SOURCE_ERROR", "STOPPED"} for row in job["tasks"])
            job["record_count"] = sum(int(row.get("record_count", 0)) for row in job["tasks"])
            job["available_count"] = sum(int(row.get("available_count", 0)) for row in job["tasks"])
            job["completed_routes"] = len({row["route"] for row in job["tasks"] if row.get("status") in {"SUCCESS", "NO_DATA", "SOURCE_ERROR", "STOPPED"}})
            job.update({"status": "STOPPED" if job["stop_event"].is_set() else "COMPLETED", "finished_at": _now()})
            _write_scrape_status(job)
    except Exception as exc:
        with SCRAPE_LOCK:
            SCRAPE_JOBS[job_id].update({"status": "ERROR", "error": str(exc), "finished_at": _now()})
            _write_scrape_status(SCRAPE_JOBS[job_id])


@app.post("/api/scrape/ixigo")
def run_ixigo_scrape():
    with SCRAPE_LOCK:
        active = _active_job("Ixigo OTA")
        if active: return {"job_id": active["job_id"], "status": active["status"], "message": "Ixigo collection is already running."}
        job_id = uuid.uuid4().hex
        config = _scraper_config()
        SCRAPE_JOBS[job_id] = {"job_id": job_id, "source": "Ixigo OTA", "scrape_date": date.today().isoformat(), "status": "QUEUED", "created_at": _now(), "started_at": _now(), "headless": bool(config.get("ixigo_headless", False)), "driver_count": 0, "total_routes": 0, "total_tasks": 0, "completed_routes": 0, "completed_tasks": 0, "record_count": 0, "available_count": 0, "tasks": [], "routes": [], "stop_event": threading.Event()}
        _write_scrape_status(SCRAPE_JOBS[job_id])
    threading.Thread(target=_run_ixigo_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "source": "Ixigo OTA", "status": "QUEUED", "message": "Ixigo collection started in the background."}


@app.get("/api/scrape/status/{job_id}")
def scrape_status(job_id: str):
    with SCRAPE_LOCK:
        job = SCRAPE_JOBS.get(job_id)
        if not job:
            try:
                with open(SCRAPE_STATUS_PATH, encoding="utf-8") as handle:
                    job = json.load(handle).get("jobs", {}).get(job_id)
            except (OSError, ValueError):
                job = None
        if not job:
            raise HTTPException(status_code=404, detail="Scrape job not found or expired.")
        return _safe_job(job)


@app.post("/api/scrape/stop/{job_id}")
def stop_scrape(job_id: str):
    with SCRAPE_LOCK:
        job = SCRAPE_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Scrape job not found or expired.")
        job["stop_event"].set()
        if job.get("status") in {"QUEUED", "RUNNING"}:
            job["status"] = "STOPPING"
        for task in job.get("tasks", []):
            if task.get("status") == "QUEUED":
                task.update({"status": "STOPPED", "completed_at": _now(), "reason": "Stopped by operator"})
        _write_scrape_status(job)
        return _safe_job(job)


def _source_run_signature(source: str, run_date: str) -> float:
    run_dir = SOURCE_RUN_ADAPTER.root / source / run_date
    files = list(run_dir.glob("*.json")) if run_dir.exists() else []
    return max((path.stat().st_mtime for path in files), default=0.0)


@app.get("/api/source-data/dates")
def source_data_dates(source: str | None = Query(None)):
    names = [source.lower()] if source else list(SOURCE_LABELS)
    unknown = [name for name in names if name not in SOURCE_LABELS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {unknown[0]}")
    output = []
    for name in names:
        dates = []
        for run_date in reversed(SOURCE_RUN_ADAPTER.run_dates(name)):
            manifest = SOURCE_RUN_ADAPTER.manifest(name, run_date)
            run_dir = SOURCE_RUN_ADAPTER.root / name / run_date
            collection = run_dir / "collection.json"
            dates.append({"run_date": run_date, "label": run_date, "file": str(collection.relative_to(SOURCE_RUN_ADAPTER.root.parent.parent)) if collection.exists() else None, "file_count": manifest.get("file_count", 0), "format": manifest.get("format", "route-files")})
        output.append({"source": name, "label": SOURCE_LABELS[name], "dates": dates})
    return {"sources": output}


@app.get("/api/source-data")
def source_data(source: str = Query(...), run_date: str = Query(...), offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=200000)):
    source = source.lower()
    if source not in SOURCE_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {source}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date) or run_date not in SOURCE_RUN_ADAPTER.run_dates(source):
        raise HTTPException(status_code=404, detail=f"No {source} run found for {run_date}.")
    key = (source, run_date)
    signature = _source_run_signature(source, run_date)
    cached = SOURCE_DATA_CACHE.get(key)
    if not cached or cached[0] != signature:
        try:
            rows = SOURCE_RUN_ADAPTER.rows(source, run_date)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=500, detail=f"Could not read {source} data: {exc}") from exc
        SOURCE_DATA_CACHE[key] = (signature, rows)
    else:
        rows = cached[1]
    return {"source": source, "label": SOURCE_LABELS[source], "run_date": run_date, "total_rows": len(rows), "offset": offset, "limit": limit, "rows": rows[offset:offset + limit], "manifest": SOURCE_RUN_ADAPTER.manifest(source, run_date)}


@app.get("/api/scrape-overview")
def scrape_overview():
    weekly = sqlite_store.get_weekly_fare_overview(SQLITE_DB_PATH)
    latest_run = None
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "raw_airfare", "compareflights")
    if os.path.isdir(root):
        runs = sorted(name for name in os.listdir(root) if os.path.isdir(os.path.join(root, name)))
        latest_run = runs[-1] if runs else None
    weekly["source"] = "CompareFlights OTA import"
    weekly["latest_import_run"] = latest_run
    weekly["latest_database_date"] = weekly["date_range"]["end"]
    weekly["note"] = "Weekly view uses the latest seven available indexed observation dates; it does not interpolate missing scraper runs."
    return weekly


@app.get("/api/anomalies")
def anomalies(limit: int = Query(50, le=500)):
    return {"count": limit, "disclaimer": DISCLAIMER, "anomalies": sqlite_store.get_anomalies(SQLITE_DB_PATH, limit)}


@app.get("/api/index/top-movers")
def top_movers(limit: int = Query(5, le=27)):
    result = sqlite_store.get_top_movers(SQLITE_DB_PATH, limit)
    result["disclaimer"] = DISCLAIMER
    return result


@app.get("/api/airlines")
def airlines():
    return {"disclaimer": DISCLAIMER + " Airline index is a simplified avg-fare-ratio, "
                                        "not the full geometric-mean methodology used nationally.",
            "airlines": sqlite_store.get_airlines_summary(SQLITE_DB_PATH)}


@app.get("/api/airlines/{airline_code}")
def airline_history(airline_code: str):
    rows = sqlite_store.get_airline_history(SQLITE_DB_PATH, airline_code.upper())
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for airline {airline_code}.")
    return {"airline_code": airline_code.upper(), "history": rows, "disclaimer": DISCLAIMER}


@app.get("/api/airlines/{airline_code}/routes")
def airline_route_history(airline_code: str, top_n: int = Query(5, ge=1, le=20)):
    result = sqlite_store.get_airline_route_history(SQLITE_DB_PATH, airline_code.upper(), top_n)
    if not result["routes"]:
        raise HTTPException(status_code=404, detail=f"No route observations for airline {airline_code}.")
    result["disclaimer"] = DISCLAIMER + " Route lines show average observed payable fare by booking date."
    return result


@app.get("/api/airports")
def airports():
    return {"airports": list(airport_coords().values())}


@app.get("/api/alerts")
def alerts(limit: int = Query(50, le=500)):
    return {"count": limit, "alerts": sqlite_store.get_alerts(SQLITE_DB_PATH, limit)}


@app.get("/api/index/revisions")
def index_revisions(limit: int = Query(50, le=500)):
    return {"revisions": sqlite_store.get_revision_history(SQLITE_DB_PATH, limit)}


@app.get("/api/map")
def route_map():
    """Route basket with airport coordinates and latest index value, for
    the India route map dashboard page."""
    coords = airport_coords()
    routes = sqlite_store.get_routes(SQLITE_DB_PATH)
    if not routes:
        routes = [
            {"origin": r.origin, "destination": r.destination, "label": r.label, "weight": r.directional_weight}
            for r in load_route_basket(top_n=DGCA_TOP_N, direction_mode=DGCA_DIRECTION_MODE)
        ]
    out = []
    for r in routes:
        detail = sqlite_store.get_route_detail(SQLITE_DB_PATH, r["origin"], r["destination"])
        latest_index = None
        if detail and detail["history"]:
            latest_index = detail["history"][-1]["index_value"]
        out.append({
            "origin": r["origin"], "destination": r["destination"], "label": r["label"],
            "weight": r["weight"],
            "origin_coords": coords.get(r["origin"]),
            "destination_coords": coords.get(r["destination"]),
            "latest_index_value": latest_index,
        })
    return {"routes": out, "disclaimer": DISCLAIMER}


@app.get("/api/dgca/basket")
def dgca_basket(
    top_n: int = Query(DGCA_TOP_N, ge=1, le=100),
    direction_mode: str = Query(DGCA_DIRECTION_MODE, pattern="^(unidirectional|bidirectional|merged)$"),
):
    rows = load_route_basket(top_n=top_n, direction_mode=direction_mode)
    return {
        "metadata": load_basket_metadata(),
        "top_n": top_n,
        "direction_mode": direction_mode,
        "count": len(rows),
        "routes": [r.__dict__ for r in rows],
    }


@app.get("/api/dgca/status")
def dgca_status():
    raw_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "dgca", "raw")
    current = date.today().replace(day=1)
    previous_month = current.month - 1 or 12
    previous_year = current.year if current.month > 1 else current.year - 1
    expected = f"{previous_year:04d}-{previous_month:02d}.xlsx"
    uploaded = sorted(name for name in os.listdir(raw_dir) if name.endswith(".xlsx")) if os.path.isdir(raw_dir) else []
    return {
        "expected_previous_month": f"{previous_year:04d}-{previous_month:02d}",
        "expected_file": expected,
        "status": "UPLOADED" if expected in uploaded else "NOT_UPLOADED_BY_DGCA",
        "message": "Previous-month DGCA workbook found." if expected in uploaded else "Previous-month DGCA workbook was not uploaded by DGCA; retaining the latest available month and flagging this cycle.",
        "latest_available_file": uploaded[-1] if uploaded else None,
        "uploaded_files": uploaded,
    }


@app.get("/api/lead-time")
def lead_time(run_date: str | None = Query(None)):
    adapter = CompareFlightsOfflineAdapter(run_date=run_date)
    records = [r for r in adapter.iter_all_records() if r.availability_status == "AVAILABLE"]
    grouped: dict[int, list[float]] = defaultdict(list)
    route_coverage: dict[int, set[str]] = defaultdict(set)
    airline_coverage: dict[int, set[str]] = defaultdict(set)
    for record in records:
        fare = record.components.total_payable_fare or record.components.offered_fare
        if fare is None:
            continue
        grouped[record.advance_purchase_days].append(fare)
        route_coverage[record.advance_purchase_days].add(f"{record.origin}-{record.destination}")
        if record.airline_code:
            airline_coverage[record.advance_purchase_days].add(record.airline_code)

    series = []
    for days in sorted(grouped):
        fares = sorted(grouped[days])
        n = len(fares)
        median = fares[n // 2] if n % 2 else (fares[n // 2 - 1] + fares[n // 2]) / 2
        series.append({
            "advance_purchase_days": days,
            "observation_count": n,
            "average_fare": round(sum(fares) / n, 2),
            "median_fare": round(median, 2),
            "route_coverage": len(route_coverage[days]),
            "airline_coverage": len(airline_coverage[days]),
        })
    return {
        "run_date": run_date,
        "source": "compareflights_offline",
        "count": sum(row["observation_count"] for row in series),
        "series": series,
        "note": "Computed from imported observed CompareFlights output; no missing lead-time values are fabricated.",
    }


@app.get("/api/source-field-mapping")
def source_field_mapping():
    return {
        "source": "compareflights",
        "parser_version": "compareflights-normalized-json-v1",
        "mapping": [
            {"source_field": "route file origin/destination", "standard_field": "origin/destination", "transformation": "upper-case IATA; searched route preserved"},
            {"source_field": "departure_date", "standard_field": "travel_date", "transformation": "ISO date"},
            {"source_field": "days_before_departure", "standard_field": "advance_purchase_days", "transformation": "integer; computed by scraper from travel date - search date"},
            {"source_field": "segments[0].airline_code", "standard_field": "airline_code", "transformation": "primary segment airline"},
            {"source_field": "segments[*].flight_number", "standard_field": "flight_number", "transformation": "joined with | for connecting itineraries"},
            {"source_field": "offers[*].price", "standard_field": "total_payable_fare/offered_fare", "transformation": "numeric INR when present"},
            {"source_field": "offers[*].fare_code", "standard_field": "fare_code", "transformation": "preserved as source fare code"},
            {"source_field": "offers[*].checkin_baggage_kg/cabin_baggage_kg", "standard_field": "baggage", "transformation": "preserved nullable"},
            {"source_field": "base fare/taxes/fees", "standard_field": "fare components", "transformation": "NULL because current source output does not expose components separately"},
        ],
    }


@app.get("/api/forecast/{origin}/{destination}")
def forecast(origin: str, destination: str):
    """
    Trains a quick forecast on the fly from stored valid observations for
    this route, cached for FORECAST_CACHE_TTL_MINUTES to avoid retraining
    on every request (see database/sqlite_store.py forecast_cache table).
    """
    import pandas as pd
    from datetime import datetime, timedelta
    from ml.forecasting import build_features, time_aware_split, evaluate_model, FEATURES
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor

    origin, destination = origin.upper(), destination.upper()
    ttl_minutes = int(os.getenv("FORECAST_CACHE_TTL_MINUTES", "60"))

    cached = sqlite_store.get_cached_forecast(SQLITE_DB_PATH, origin, destination)
    if cached:
        age = datetime.utcnow() - datetime.fromisoformat(cached["computed_at"])
        if age < timedelta(minutes=ttl_minutes):
            result = cached["payload"]
            result["cached"] = True
            result["cache_age_seconds"] = int(age.total_seconds())
            return result

    rows = sqlite_store.get_route_fare_history(SQLITE_DB_PATH, origin, destination)
    if len(rows) < 50:
        raise HTTPException(status_code=404, detail="Not enough observations for this route to forecast.")

    df = pd.DataFrame(rows)
    df["days_to_departure"] = (pd.to_datetime(df["travel_date"]) - pd.to_datetime(df["booking_date"])).dt.days
    df["stops"] = 0  # not tracked in the sqlite demo schema; see LIMITATIONS.md
    df = build_features(df)
    train, val = time_aware_split(df)

    X_train, y_train = train[FEATURES], train["total_fare"]
    X_val, y_val = val[FEATURES], val["total_fare"]

    lr_metrics = evaluate_model(LinearRegression(), X_train, y_train, X_val, y_val)
    rf_model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
    rf_metrics = evaluate_model(rf_model, X_train, y_train, X_val, y_val)

    best_name = "random_forest" if rf_metrics["mae"] < lr_metrics["mae"] else "linear_regression"
    best_model = rf_model if best_name == "random_forest" else LinearRegression().fit(X_train, y_train)

    residual_std = float((y_val - best_model.predict(X_val)).std())
    future_rows = pd.DataFrame([
        {"days_to_departure": d, "stops": 0, "dow": 4, "month": 9, "is_holiday_window": 0}
        for d in [3, 7, 14, 30]
    ])
    preds = best_model.predict(future_rows[FEATURES])

    result = {
        "route": f"{origin}-{destination}",
        "model_comparison": {"linear_regression": lr_metrics, "random_forest": rf_metrics},
        "chosen_model": best_name,
        "forecast": [
            {
                "days_to_departure": int(d),
                "predicted_fare": round(float(p), 2),
                "lower_bound": round(float(p - 1.28 * residual_std), 2),
                "upper_bound": round(float(p + 1.28 * residual_std), 2),
            }
            for d, p in zip([3, 7, 14, 30], preds)
        ],
        "disclaimer": DISCLAIMER + " Forecast is a supplementary ML signal, not part of the index itself.",
        "cached": False,
    }
    sqlite_store.set_cached_forecast(SQLITE_DB_PATH, origin, destination, result)
    return result
