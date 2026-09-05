"""
Worker entrypoint: runs the scheduled daily airfare collection and monthly
DGCA basket refresh using APScheduler.

The dashboard is file-backed. A cycle validates and publishes a clean source
file when the configured route basket is complete; it never writes SQLite.
"""
import logging
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("worker")

MODE = os.getenv("MODE", "file")
SCRAPE_HOUR = int(os.getenv("SCRAPE_HOUR", "6"))
SCRAPE_MINUTE = int(os.getenv("SCRAPE_MINUTE", "0"))
COMPAREFLIGHTS_SCRAPE_HOUR = int(os.getenv("COMPAREFLIGHTS_SCRAPE_HOUR", str(SCRAPE_HOUR)))
COMPAREFLIGHTS_SCRAPE_MINUTE = int(os.getenv("COMPAREFLIGHTS_SCRAPE_MINUTE", str(SCRAPE_MINUTE)))
IXIGO_SCRAPE_HOUR = int(os.getenv("IXIGO_SCRAPE_HOUR", "6"))
IXIGO_SCRAPE_MINUTE = int(os.getenv("IXIGO_SCRAPE_MINUTE", "30"))
MAX_RETRIES = int(os.getenv("SCRAPE_MAX_RETRIES", "3"))
BACKOFF_BASE = int(os.getenv("SCRAPE_BACKOFF_BASE_SECONDS", "5"))
DGCA_CHECK_HOUR = int(os.getenv("DGCA_CHECK_HOUR", "9"))
DGCA_CHECK_MINUTE = int(os.getenv("DGCA_CHECK_MINUTE", "30"))
DGCA_TOP_N = int(os.getenv("DGCA_TOP_N", "50"))
DGCA_DIRECTION_MODE = os.getenv("DGCA_DIRECTION_MODE", "bidirectional")
DAILY_ROUTE_PAIRS = int(os.getenv("DAILY_ROUTE_PAIRS", "20"))
DAILY_DIRECTION_MODE = os.getenv("DGCA_DIRECTION_MODE", "bidirectional")
IXIGO_DRIVER = os.getenv("IXIGO_DRIVER", "chromium")
IXIGO_HEADLESS = os.getenv("IXIGO_HEADLESS", "0").lower() in {"1", "true", "yes"}
SCRAPER_DRIVER_COUNT = int(os.getenv("SCRAPER_DRIVER_COUNT", "4"))
IXIGO_LEAD_DAYS = [1, 7, 15, 30, 45]
RUNTIME_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "runtime", "scraper_config.json")

try:
    with open(RUNTIME_CONFIG_PATH, encoding="utf-8") as config_file:
        _runtime = json.load(config_file)
    DAILY_ROUTE_PAIRS = int(_runtime.get("daily_route_pairs", DAILY_ROUTE_PAIRS))
    DAILY_DIRECTION_MODE = _runtime.get("direction_mode", DAILY_DIRECTION_MODE)
    IXIGO_DRIVER = _runtime.get("ixigo_driver", IXIGO_DRIVER)
    IXIGO_HEADLESS = bool(_runtime.get("ixigo_headless", IXIGO_HEADLESS))
    SCRAPER_DRIVER_COUNT = max(1, min(16, int(_runtime.get("scraper_driver_count", SCRAPER_DRIVER_COUNT))))
    IXIGO_LEAD_DAYS = sorted({int(value) for value in _runtime.get("ixigo_lead_days", IXIGO_LEAD_DAYS) if int(value) > 0}) or [1]
    COMPAREFLIGHTS_SCRAPE_HOUR, COMPAREFLIGHTS_SCRAPE_MINUTE = (int(value) for value in _runtime.get("compareflights_time", f"{COMPAREFLIGHTS_SCRAPE_HOUR:02d}:{COMPAREFLIGHTS_SCRAPE_MINUTE:02d}").split(":", 1))
    IXIGO_SCRAPE_HOUR, IXIGO_SCRAPE_MINUTE = (int(value) for value in _runtime.get("ixigo_time", f"{IXIGO_SCRAPE_HOUR:02d}:{IXIGO_SCRAPE_MINUTE:02d}").split(":", 1))
except (OSError, ValueError, TypeError):
    pass


def scheduled_routes():
    from airfare.dgca.basket import load_route_basket
    return load_route_basket(top_n=DAILY_ROUTE_PAIRS, direction_mode=DAILY_DIRECTION_MODE)


def run_collection_cycle():
    """Validate the latest OTA run and publish a clean file when complete."""
    logger.info("Starting collection cycle (mode=%s)", MODE)
    logger.info("CompareFlights route scope: top %d pairs -> %d directed routes.", DAILY_ROUTE_PAIRS, len(scheduled_routes()))
    from airfare.sources.clean_adapter import CleanDataValidationError, CleanSourceAdapter
    from airfare.sources.run_adapter import SourceRunAdapter
    raw = SourceRunAdapter()
    clean = CleanSourceAdapter(raw)
    dates = raw.run_dates("compareflights")
    if not dates:
        logger.warning("No CompareFlights date-partitioned run is available.")
        return
    run_date = dates[-1]
    expected = [f"{route.origin}-{route.destination}" for route in scheduled_routes()]
    validation = clean.validate("compareflights", run_date, expected)
    if not validation["valid"]:
        logger.warning("Clean adaptation rejected for %s: %s", run_date, validation["message"])
        return
    try:
        result = clean.build("compareflights", run_date, expected)
        logger.info("Clean adaptation completed: %s", result["path"])
    except CleanDataValidationError as exc:
        logger.warning("Clean adaptation rejected: %s", exc)


def run_ixigo_collection_cycle():
    """Run Ixigo's date/route/lead-window queue independently."""
    routes = scheduled_routes()
    logger.info("Starting Ixigo collection: top %d pairs -> %d directed routes x %s.", DAILY_ROUTE_PAIRS, len(routes), IXIGO_LEAD_DAYS)
    if MODE != "live":
        logger.info("MODE=%s: Ixigo queue validated; live browser collection is disabled in non-live mode.", MODE)
        return
    from scraper.ota.ixigo import collect_route, consolidate_legacy_run, save_route_result
    from datetime import timedelta
    import asyncio
    run_date = date.today().isoformat()
    consolidate_legacy_run(run_date)
    output_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw_airfare", "ixigo", run_date)

    def window_completed(route, lead_days):
        path = os.path.join(output_root, f"{route.origin}-{route.destination}.json")
        try:
            with open(path, encoding="utf-8") as result_file:
                payload = json.load(result_file)
            return payload.get("lead_windows", {}).get(f"T+{lead_days}", {}).get("status") == "SUCCESS"
        except (OSError, ValueError, TypeError):
            return False

    tasks = [(route, days) for route in routes for days in IXIGO_LEAD_DAYS if not window_completed(route, days)]
    logger.info("Ixigo resume check: %d windows already complete; %d remain.", len(routes) * len(IXIGO_LEAD_DAYS) - len(tasks), len(tasks))

    def collect_task(task):
        route, lead_days = task
        result = None
        travel_date = (date.today() + timedelta(days=lead_days)).strftime("%d%m%Y")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = asyncio.run(collect_route(route.origin, route.destination, travel_date, headless=IXIGO_HEADLESS, browser_engine=IXIGO_DRIVER))
                result.update({"lead_days": lead_days, "lead_label": f"T+{lead_days}"})
                save_route_result(result, run_date=run_date, suffix=f"T+{lead_days}")
                if result.get("status") == "SUCCESS" or attempt == MAX_RETRIES:
                    return route, lead_days, result
            except Exception as exc:
                result = {"route": f"{route.origin}-{route.destination}", "status": "SOURCE_ERROR", "reason": str(exc), "observation_count": 0}
                if attempt == MAX_RETRIES:
                    return route, lead_days, result
            time.sleep(min(30, BACKOFF_BASE * (2 ** (attempt - 1))))
        return route, lead_days, result or {"route": f"{route.origin}-{route.destination}", "status": "SOURCE_ERROR", "observation_count": 0}

    with ThreadPoolExecutor(max_workers=SCRAPER_DRIVER_COUNT, thread_name_prefix="ixigo-driver") as pool:
        futures = [pool.submit(collect_task, task) for task in tasks]
        for future in as_completed(futures):
            route, lead_days, result = future.result()
            logger.info("Ixigo %s-%s %s: %s observations=%s", route.origin, route.destination, f"T+{lead_days}", result["status"], result.get("observation_count", 0))


def run_dgca_route_basket_check():
    """Daily DGCA check.

    DGCA can publish the previous month's file a few days late. This job runs
    every day at a fixed local time and only updates the latest basket when the
    full latest 12-complete-month window is available. Existing fare rows keep
    their route/basket context; new snapshots are versioned under data/dgca/output.
    """
    logger.info("Checking DGCA route basket (top_n=%s, direction_mode=%s).", DGCA_TOP_N, DGCA_DIRECTION_MODE)
    try:
        from airfare.dgca.pipeline import run as run_dgca
        result = run_dgca(top_n=DGCA_TOP_N, direction_mode=DGCA_DIRECTION_MODE)
        logger.info("DGCA check result: %s window=%s..%s output=%s",
                    result.reason, result.window_start, result.window_end, result.output_path)
    except FileNotFoundError as exc:
        logger.warning("DGCA latest complete-month file is not available yet: %s", exc)
    except Exception:
        logger.exception("DGCA route basket check failed.")


if __name__ == "__main__":
    from apscheduler.schedulers.blocking import BlockingScheduler  # deferred: not needed to test run_collection_cycle directly

    scheduler = BlockingScheduler()
    scheduler.add_job(run_collection_cycle, "cron", hour=COMPAREFLIGHTS_SCRAPE_HOUR, minute=COMPAREFLIGHTS_SCRAPE_MINUTE, id="compareflights_daily")
    scheduler.add_job(run_ixigo_collection_cycle, "cron", hour=IXIGO_SCRAPE_HOUR, minute=IXIGO_SCRAPE_MINUTE, id="ixigo_daily")
    scheduler.add_job(run_dgca_route_basket_check, "cron", day=1, hour=DGCA_CHECK_HOUR, minute=DGCA_CHECK_MINUTE)
    logger.info("Worker started. CompareFlights scheduled at %02d:%02d; Ixigo at %02d:%02d local time, mode=%s, top pairs=%d.", COMPAREFLIGHTS_SCRAPE_HOUR, COMPAREFLIGHTS_SCRAPE_MINUTE, IXIGO_SCRAPE_HOUR, IXIGO_SCRAPE_MINUTE, MODE, DAILY_ROUTE_PAIRS)
    logger.info("DGCA monthly refresh scheduled on day 1 at %02d:%02d local time.", DGCA_CHECK_HOUR, DGCA_CHECK_MINUTE)
    run_dgca_route_basket_check()
    run_collection_cycle()  # run once immediately on startup
    run_ixigo_collection_cycle()  # validate/run the second source queue too
    scheduler.start()
