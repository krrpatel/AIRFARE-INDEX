"""FastAPI backend for the file-backed Airfare Price Index dashboard."""
import logging
import os
import asyncio
import threading
import time
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from airfare.airports.registry import airport_coords, require_domestic_route
from airfare.dgca.basket import load_basket_metadata, load_route_basket
from airfare.sources.compareflights.offline_adapter import CompareFlightsOfflineAdapter
from airfare.sources.clean_adapter import CleanDataValidationError, CleanSourceAdapter
from airfare.sources.file_read_model import FileAirfareReadModel
from airfare.sources.run_adapter import SOURCE_LABELS, SourceRunAdapter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("airfare_api")

DATA_MODE = os.getenv("MODE", "research")  # 'research' | 'live'
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
CLEAN_DATA_ADAPTER = CleanSourceAdapter(SOURCE_RUN_ADAPTER)
FILE_MODEL = FileAirfareReadModel(SOURCE_RUN_ADAPTER, CLEAN_DATA_ADAPTER)
SOURCE_DATA_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
ADAPTOR_JOBS: dict[str, dict] = {}
ADAPTOR_LOCK = threading.RLock()
FORECAST_CACHE: dict[tuple[str, str], dict] = {}


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
    row = FILE_MODEL.current_index()
    if row is None:
        raise HTTPException(status_code=404, detail="No adapted source data is available yet. Run the source adaptor from Settings.")
    row["disclaimer"] = DISCLAIMER
    return row


@app.get("/api/index/history")
def index_history(
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
):
    rows = FILE_MODEL.index_history(start=start, end=end)
    return {"count": len(rows), "disclaimer": DISCLAIMER, "series": rows}


@app.get("/api/routes")
def list_routes():
    return FILE_MODEL.routes()


@app.get("/api/routes/analytics")
def route_analytics(
    start: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    end: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    limit: int = Query(500, ge=1, le=5000, description="Max (route, airline) rows to return"),
    offset: int = Query(0, ge=0),
):
    return {
        "routes": FILE_MODEL.route_analytics(start=start, end=end, limit=limit, offset=offset),
        "date_range": FILE_MODEL.date_range(),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/analytics")
def analytics(
    start: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    end: str | None = Query(None, description="YYYY-MM-DD, filters by booking_date"),
    limit: int = Query(500, ge=1, le=5000, description="Max route_ranking rows to return"),
    offset: int = Query(0, ge=0),
):
    return {**FILE_MODEL.analytics(start=start, end=end, limit=limit, offset=offset), "disclaimer": DISCLAIMER}


@app.get("/api/holiday-analytics")
def holiday_analytics():
    """Compare travel-date fares falling on the cached holiday calendar."""
    return FILE_MODEL.holiday_analytics()


@app.get("/api/routes/{origin}/{destination}")
def route_detail(origin: str, destination: str):
    try:
        require_domestic_route(origin, destination)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cache_key = (origin.upper(), destination.upper(), "file-model")
    if cache_key in ROUTE_DETAIL_CACHE:
        return ROUTE_DETAIL_CACHE[cache_key]
    detail = FILE_MODEL.route_detail(origin, destination)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Route {origin}-{destination} has no adapted observations.")
    detail["disclaimer"] = DISCLAIMER
    ROUTE_DETAIL_CACHE[cache_key] = detail
    return detail


@app.get("/api/data-quality")
def data_quality():
    return FILE_MODEL.data_quality()


@app.get("/api/source-health")
def source_health():
    sources = []
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
    for name, label, url in [("compareflights", "CompareFlights OTA", "https://compareflights.co.in"), ("ixigo", "Ixigo OTA", "https://www.ixigo.com"), ("ixigo_holiday", "Holiday calendar", "https://www.ixigo.com/growth/api/v1/holidayCalendar")]:
        if name not in known:
            if name == "ixigo_holiday":
                holiday_file = SOURCE_RUN_ADAPTER.root / "ixigo" / "holidays.json"
                status = "ONLINE" if holiday_file.exists() else "CONFIGURED"
                last_success = datetime.fromtimestamp(holiday_file.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds") if holiday_file.exists() else None
            else:
                dates = FILE_MODEL.dates(name)
                status = "ONLINE" if dates else "CONFIGURED"
                last_success = dates[-1] if dates else None
            sources.append({"name": name, "label": label, "status": status, "source_url": url, "last_success": last_success, "data_mode": "date-partitioned files"})
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
    raw_signature = max((path.stat().st_mtime for path in files), default=0.0)
    clean_path = CLEAN_DATA_ADAPTER.path(source, run_date)
    return max(raw_signature, clean_path.stat().st_mtime if clean_path.exists() else 0.0)


@app.get("/api/source-data/dates")
def source_data_dates(source: str | None = Query(None)):
    names = [source.lower()] if source else list(SOURCE_LABELS)
    unknown = [name for name in names if name not in SOURCE_LABELS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {unknown[0]}")
    output = []
    for name in names:
        dates = []
        all_dates = sorted(set(SOURCE_RUN_ADAPTER.run_dates(name)) | set(CLEAN_DATA_ADAPTER.clean_dates(name)))
        for run_date in reversed(all_dates):
            manifest = SOURCE_RUN_ADAPTER.manifest(name, run_date)
            run_dir = SOURCE_RUN_ADAPTER.root / name / run_date
            collection = run_dir / "collection.json"
            clean_path = CLEAN_DATA_ADAPTER.path(name, run_date)
            dates.append({"run_date": run_date, "label": run_date, "file": str(collection.relative_to(SOURCE_RUN_ADAPTER.root.parent.parent)) if collection.exists() else None, "clean_file": str(clean_path.relative_to(SOURCE_RUN_ADAPTER.root.parent.parent)) if clean_path.exists() else None, "file_count": manifest.get("file_count", 0), "format": "clean-json" if clean_path.exists() else manifest.get("format", "route-files"), "data_mode": "clean" if clean_path.exists() else "raw-fallback"})
        output.append({"source": name, "label": SOURCE_LABELS[name], "dates": dates})
    return {"sources": output}


@app.get("/api/source-data")
def source_data(source: str = Query(...), run_date: str = Query(...), route: str | None = Query(None), offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=200000)):
    source = source.lower()
    if source not in SOURCE_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {source}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date) or run_date not in set(SOURCE_RUN_ADAPTER.run_dates(source)) | set(CLEAN_DATA_ADAPTER.clean_dates(source)):
        raise HTTPException(status_code=404, detail=f"No {source} run found for {run_date}.")
    key = (source, run_date)
    signature = _source_run_signature(source, run_date)
    cached = SOURCE_DATA_CACHE.get(key)
    if not cached or cached[0] != signature:
        try:
            rows = FILE_MODEL.rows(source, run_date)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=500, detail=f"Could not read {source} data: {exc}") from exc
        SOURCE_DATA_CACHE[key] = (signature, rows)
    else:
        rows = cached[1]
    filtered = [row for row in rows if not route or row.get("route", "").upper() == route.upper()]
    return {"source": source, "label": SOURCE_LABELS[source], "run_date": run_date, "route": route, "data_mode": FILE_MODEL.row_source_mode(source, run_date), "total_rows": len(filtered), "offset": offset, "limit": limit, "rows": filtered[offset:offset + limit], "manifest": SOURCE_RUN_ADAPTER.manifest(source, run_date)}


@app.get("/api/source-data/routes")
def source_data_routes(source: str = Query(...), run_date: str = Query(...)):
    source = source.lower()
    if source not in SOURCE_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {source}")
    if run_date not in set(SOURCE_RUN_ADAPTER.run_dates(source)) | set(CLEAN_DATA_ADAPTER.clean_dates(source)):
        raise HTTPException(status_code=404, detail=f"No {source} run found for {run_date}.")
    rows = FILE_MODEL.rows(source, run_date)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("route") or "UNKNOWN"
        item = grouped.setdefault(key, {"route": key, "origin": row.get("origin"), "destination": row.get("destination"), "row_count": 0, "usable_count": 0, "airlines": set(), "lead_windows": set(), "lowest_fare": None, "average_fare": None, "_fares": []})
        item["row_count"] += 1
        if row.get("airline_code"):
            item["airlines"].add(row["airline_code"])
        if row.get("lead_window"):
            item["lead_windows"].add(row["lead_window"])
        try:
            fare = float(row.get("fare"))
        except (TypeError, ValueError):
            fare = None
        if fare is not None and fare > 0:
            item["usable_count"] += 1
            item["_fares"].append(fare)
    output = []
    for item in sorted(grouped.values(), key=lambda value: value["route"]):
        fares = item.pop("_fares")
        item["airlines"] = sorted(item["airlines"])
        item["lead_windows"] = sorted(item["lead_windows"], key=lambda value: int(value[2:]) if str(value).startswith("T+") and str(value)[2:].isdigit() else 999)
        item["lowest_fare"] = round(min(fares), 2) if fares else None
        item["average_fare"] = round(sum(fares) / len(fares), 2) if fares else None
        output.append(item)
    return {"source": source, "run_date": run_date, "data_mode": FILE_MODEL.row_source_mode(source, run_date), "routes": output}


def _adaptor_job_safe(job: dict) -> dict:
    return {key: value for key, value in job.items() if key != "stop_event"}


def _run_clean_adaptor(job_id: str, source: str, run_date: str) -> None:
    try:
        expected = [f"{item.origin}-{item.destination}" for item in _source_routes()]
        with ADAPTOR_LOCK:
            ADAPTOR_JOBS[job_id].update({"status": "RUNNING", "started_at": _now(), "total_routes": len(expected)})
        def progress(done: int, total: int) -> None:
            with ADAPTOR_LOCK:
                job = ADAPTOR_JOBS[job_id]
                job.update({"processed_routes": done, "total_routes": total, "progress_pct": round(done * 100 / total, 1) if total else 0})
        result = CLEAN_DATA_ADAPTER.build(source, run_date, expected, progress)
        FILE_MODEL.invalidate(source, run_date)
        with ADAPTOR_LOCK:
            ADAPTOR_JOBS[job_id].update({"status": "COMPLETED", "finished_at": _now(), "processed_routes": result["route_count"], "rows_written": result["row_count"], "output_file": result["path"], "progress_pct": 100})
    except CleanDataValidationError as exc:
        with ADAPTOR_LOCK:
            ADAPTOR_JOBS[job_id].update({"status": "REJECTED", "finished_at": _now(), "error": str(exc)})
    except Exception as exc:
        logger.exception("Clean adaptor failed")
        with ADAPTOR_LOCK:
            ADAPTOR_JOBS[job_id].update({"status": "ERROR", "finished_at": _now(), "error": str(exc)})


def _adaptor_payload(payload: dict[str, Any]) -> tuple[str, str]:
    source = str(payload.get("source") or "compareflights").lower()
    if source not in SOURCE_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported source: {source}")
    run_date = str(payload.get("run_date") or "")
    available = sorted(set(SOURCE_RUN_ADAPTER.run_dates(source)) | set(CLEAN_DATA_ADAPTER.clean_dates(source)))
    if not run_date:
        if not available:
            raise HTTPException(status_code=404, detail=f"No {source} source run is available.")
        run_date = available[-1]
    if run_date not in available:
        raise HTTPException(status_code=404, detail=f"No {source} source run found for {run_date}.")
    return source, run_date


@app.post("/api/adaptor/validate")
def adaptor_validate(payload: dict[str, Any] = Body(default={} )):
    source, run_date = _adaptor_payload(payload)
    expected = [f"{item.origin}-{item.destination}" for item in _source_routes()]
    return CLEAN_DATA_ADAPTER.validate(source, run_date, expected)


@app.post("/api/adaptor/run")
def adaptor_run(payload: dict[str, Any] = Body(default={} )):
    source, run_date = _adaptor_payload(payload)
    expected = [f"{item.origin}-{item.destination}" for item in _source_routes()]
    validation = CLEAN_DATA_ADAPTER.validate(source, run_date, expected)
    if not validation["valid"]:
        raise HTTPException(status_code=409, detail=validation)
    if not bool(payload.get("confirmed", False)):
        return {"requires_confirmation": True, "validation": validation}
    with ADAPTOR_LOCK:
        job_id = uuid.uuid4().hex
        ADAPTOR_JOBS[job_id] = {"job_id": job_id, "source": source, "run_date": run_date, "status": "QUEUED", "created_at": _now(), "processed_routes": 0, "total_routes": validation["expected_route_count"], "rows_written": 0, "progress_pct": 0}
    threading.Thread(target=_run_clean_adaptor, args=(job_id, source, run_date), daemon=True).start()
    return {"job_id": job_id, "status": "QUEUED", "validation": validation}


@app.get("/api/adaptor/status/{job_id}")
def adaptor_status(job_id: str):
    with ADAPTOR_LOCK:
        job = ADAPTOR_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Adaptor job not found or expired.")
        return _adaptor_job_safe(job)


@app.get("/api/scrape-overview")
def scrape_overview():
    weekly = FILE_MODEL.weekly()
    latest_run = FILE_MODEL.dates()[-1] if FILE_MODEL.dates() else None
    weekly["source"] = "CompareFlights OTA import"
    weekly["latest_import_run"] = latest_run
    weekly["latest_database_date"] = weekly["date_range"]["end"]
    weekly["note"] = "Weekly view uses the latest seven available indexed observation dates; it does not interpolate missing scraper runs."
    return weekly


@app.get("/api/anomalies")
def anomalies(limit: int = Query(50, le=500)):
    quality = FILE_MODEL.data_quality()
    return {"count": min(limit, quality["anomaly_count"]), "disclaimer": DISCLAIMER, "anomalies": [], "note": "File-backed view retains source rows and reports quality counts; no database anomaly table is used."}


@app.get("/api/index/top-movers")
def top_movers(limit: int = Query(5, le=27)):
    result = FILE_MODEL.top_movers(limit=limit)
    result["disclaimer"] = DISCLAIMER
    return result


@app.get("/api/airlines")
def airlines():
    return {"disclaimer": DISCLAIMER + " Airline index is a simplified avg-fare-ratio, "
                                        "not the full geometric-mean methodology used nationally.",
            "airlines": FILE_MODEL.airlines()}


@app.get("/api/airlines/{airline_code}")
def airline_history(airline_code: str):
    rows = FILE_MODEL.airline_history(airline_code.upper())
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for airline {airline_code}.")
    return {"airline_code": airline_code.upper(), "history": rows, "disclaimer": DISCLAIMER}


@app.get("/api/airlines/{airline_code}/routes")
def airline_route_history(airline_code: str, top_n: int = Query(5, ge=1, le=20)):
    result = FILE_MODEL.airline_route_history(airline_code.upper(), top_n)
    if not result["routes"]:
        raise HTTPException(status_code=404, detail=f"No route observations for airline {airline_code}.")
    result["disclaimer"] = DISCLAIMER + " Route lines show average observed payable fare by booking date."
    return result


@app.get("/api/airports")
def airports():
    return {"airports": list(airport_coords().values())}


@app.get("/api/alerts")
def alerts(limit: int = Query(50, le=500)):
    return {"count": 0, "alerts": [], "note": "No persisted alert table is used. Review source health and route movement for current file-backed signals."}


@app.get("/api/index/revisions")
def index_revisions(limit: int = Query(50, le=500)):
    return {"revisions": [], "note": "Revision history is unavailable in temporary file-only mode."}


@app.get("/api/map")
def route_map():
    """Route basket with airport coordinates and latest index value, for
    the India route map dashboard page."""
    coords = airport_coords()
    routes = FILE_MODEL.routes()
    out = []
    for r in routes:
        detail = FILE_MODEL.route_detail(r["origin"], r["destination"])
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
    grouped: dict[int, list[float]] = defaultdict(list)
    route_coverage: dict[int, set[str]] = defaultdict(set)
    airline_coverage: dict[int, set[str]] = defaultdict(set)
    rows = FILE_MODEL.rows("compareflights", run_date)
    for row in rows:
        fare = row.get("fare")
        days = row.get("lead_days")
        if fare is None or days is None:
            continue
        grouped[int(days)].append(float(fare))
        route_coverage[int(days)].add(row.get("route", "UNKNOWN"))
        if row.get("airline_code"):
            airline_coverage[int(days)].add(row["airline_code"])

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
        "source": "CompareFlights OTA file data",
        "count": sum(row["observation_count"] for row in series),
        "series": series,
        "note": "Computed from date-partitioned source files; no missing lead-time values are fabricated.",
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
            {"source_field": "itinerary.stops / segments length", "standard_field": "number_of_stops", "transformation": "zero for non-stop; connecting segment count otherwise"},
            {"source_field": "offers[*].checkin_baggage_kg/cabin_baggage_kg", "standard_field": "baggage", "transformation": "preserved nullable"},
            {"source_field": "base fare/taxes/fees", "standard_field": "fare components", "transformation": "NULL because current source output does not expose components separately"},
        ],
    }


@app.get("/api/forecast/{origin}/{destination}")
def forecast(origin: str, destination: str):
    """
    Trains a quick forecast on the fly from stored valid observations for
    this route, cached for FORECAST_CACHE_TTL_MINUTES to avoid retraining
    on every request.
    """
    import pandas as pd
    from ml.forecasting import build_features, time_aware_split, evaluate_model, FEATURES
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor

    origin, destination = origin.upper(), destination.upper()
    ttl_minutes = int(os.getenv("FORECAST_CACHE_TTL_MINUTES", "60"))

    cache_key = (origin, destination)
    cached = FORECAST_CACHE.get(cache_key)
    if cached:
        computed = datetime.fromisoformat(cached["computed_at"])
        if computed.tzinfo is None:
            computed = computed.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - computed
        if age < timedelta(minutes=ttl_minutes):
            result = dict(cached["payload"])
            result["cached"] = True
            result["cache_age_seconds"] = max(0, int(age.total_seconds()))
            return result

    rows = FILE_MODEL.fare_history(origin, destination)
    if len(rows) < 50:
        raise HTTPException(status_code=404, detail=f"Not enough file observations for {origin}-{destination} to forecast; at least 50 valid fares are required.")

    try:
        df = pd.DataFrame(rows)
        df["days_to_departure"] = pd.to_numeric(df["days_to_departure"], errors="coerce").fillna(0)
        df["stops"] = pd.to_numeric(df["stops"], errors="coerce").fillna(0)
        df = build_features(df)
        train, val = time_aware_split(df)
        if len(train) < 2 or len(val) < 2:
            raise ValueError("not enough time-separated rows")

        X_train, y_train = train[FEATURES], train["total_fare"]
        X_val, y_val = val[FEATURES], val["total_fare"]

        lr_metrics = evaluate_model(LinearRegression(), X_train, y_train, X_val, y_val)
        rf_model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
        rf_metrics = evaluate_model(rf_model, X_train, y_train, X_val, y_val)

        best_name = "random_forest" if rf_metrics["mae"] < lr_metrics["mae"] else "linear_regression"
        best_model = rf_model if best_name == "random_forest" else LinearRegression().fit(X_train, y_train)

        residual_std = float((y_val - best_model.predict(X_val)).std())
        future_rows = pd.DataFrame([{"days_to_departure": d, "stops": 0, "dow": 4, "month": 9, "is_holiday_window": 0} for d in [3, 7, 14, 30]])
        preds = best_model.predict(future_rows[FEATURES])
    except Exception as exc:
        logger.exception("Forecast failed for %s-%s", origin, destination)
        raise HTTPException(status_code=422, detail=f"Forecast could not be computed for {origin}-{destination}: {exc}") from exc

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
    FORECAST_CACHE[cache_key] = {"computed_at": datetime.now(timezone.utc).isoformat(), "payload": result}
    return result
