"""Permission-aware Ixigo airfare collector.

This adapter captures the public Ixigo flight-search stream with Selenium Chromium,
normalizes the response into JSON, and never attempts CAPTCHA, IP, cookie, or
robots.txt bypasses. Configure routes through ``IXIGO_ROUTES`` or the CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import os
import shutil
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "data" / "raw_airfare" / "ixigo"
# Reuse the original collector's persistent profile when present so the
# background job uses the same approved session state as the manual run.
LEGACY_PROFILE = BASE_DIR.parent / "ixigo_browser_profile"
PROFILE_ROOT = Path(os.getenv("IXIGO_PROFILE_DIR", str(LEGACY_PROFILE if LEGACY_PROFILE.exists() else BASE_DIR / "data" / "runtime" / "ixigo_browser_profiles")))
STREAM_HINT = "/flights/v2/search/stream"
CLASSES = {"e": "Economy", "w": "Premium Economy", "b": "Business"}
AIRLINE_NAMES = {"6E": "IndiGo", "AI": "Air India", "IX": "Air India Express", "QP": "Akasa Air", "SG": "SpiceJet", "UK": "Vistara", "G8": "Go First"}
OUTPUT_LOCK = threading.RLock()


def parse_stream(text: str) -> list[Any]:
    text = text.strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    frames = list(re.finditer(r'(?<!")\bdata:\s*(?=[\{\[])', text))
    if frames:
        parsed = []
        for frame in frames:
            try:
                value, _ = decoder.raw_decode(text, frame.end())
                parsed.append(value)
            except json.JSONDecodeError:
                continue
        if parsed:
            return parsed
    objects = []
    cursor = 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(text):
            break
        try:
            value, end = decoder.raw_decode(text, cursor)
        except json.JSONDecodeError:
            break
        objects.append(value)
        cursor = end
    return objects or [json.loads(text)] if text else []


def _number(value: Any) -> float | None:
    try:
        cleaned = re.sub(r"[^0-9.]", "", str(value).replace(",", ""))
        return float(cleaned) if cleaned else None
    except (TypeError, ValueError):
        return None


def _iso(value: str) -> str:
    if re.fullmatch(r"\d{8}", value):
        return f"{value[4:]}-{value[2:4]}-{value[:2]}"
    return value[:10]


def extract_observations(frames: list[Any], origin: str, destination: str, travel_date: str, cabin: str) -> list[dict[str, Any]]:
    def journey_lists(value: Any):
        """Accept the known stream envelope and harmless API wrapper variants."""
        if isinstance(value, dict):
            if isinstance(value.get("flightJourneys"), list):
                yield value["flightJourneys"]
            data = value.get("data")
            if data is not value:
                yield from journey_lists(data)
            for key in ("result", "payload", "response"):
                if key in value:
                    yield from journey_lists(value[key])

    output = []
    for frame in frames:
        for journeys in journey_lists(frame):
            for journey in journeys:
                for flight_fare in journey.get("flightFare") or []:
                    keys = str(flight_fare.get("flightKeys") or "")
                    segments = []
                    for part in keys.split("*"):
                        pieces = part.split("-")
                        if len(pieces) >= 4:
                            flight_number = pieces[2]
                            carrier_match = re.match(r"([A-Za-z0-9]{2})", flight_number)
                            carrier_code = carrier_match.group(1).upper() if carrier_match else None
                            segments.append({"origin": pieces[0], "destination": pieces[1], "flight_number": flight_number, "airline_code": carrier_code, "airline_name": AIRLINE_NAMES.get(carrier_code, carrier_code), "travel_date": _iso(pieces[3])})
                    if not segments or segments[0]["origin"] != origin or segments[-1]["destination"] != destination:
                        continue
                    for fare in flight_fare.get("fares") or []:
                        details = fare.get("fareDetails") or {}
                        amount = next((_number(details.get(key)) for key in ("displayFare", "totalFare", "fare", "amount") if _number(details.get(key)) is not None), None)
                        if amount is None:
                            continue
                        metadata = fare.get("fareMetadata") or []
                        provider = metadata[0].get("providerId") if metadata and isinstance(metadata[0], dict) else None
                        carrier_code = segments[0].get("airline_code")
                        output.append({"source": "ixigo", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "origin": origin, "destination": destination, "travel_date": _iso(travel_date), "advance_purchase_days": None, "cabin_class": CLASSES.get(cabin, cabin), "airline_code": carrier_code, "airline_name": AIRLINE_NAMES.get(carrier_code, carrier_code), "flight_number": "|".join(segment["flight_number"] for segment in segments), "stops": len(segments) - 1, "number_of_stops": len(segments) - 1, "departure": None, "arrival": None, "duration_minutes": None, "fare": {"amount": amount, "currency": "INR"}, "provider_id": provider, "fare_token_present": bool(details.get("fareToken")), "segments": segments})
    unique = {}
    for row in output:
        key = (row["origin"], row["destination"], row["travel_date"], row["flight_number"], row["fare"]["amount"], row["cabin_class"])
        unique.setdefault(key, row)
    return list(unique.values())


def search_url(origin: str, destination: str, travel_date: str, cabin: str) -> str:
    return f"https://www.ixigo.com/search/result/flight?from={origin}&to={destination}&date={travel_date}&adults=1&children=0&infants=0&class={cabin}&source=Search+Form"


def _collect_route_selenium(origin: str, destination: str, travel_date: str, cabin: str, viewport: tuple[int, int], headless: bool) -> dict[str, Any]:
    """Capture Ixigo's own stream with Selenium/CDP, matching CompareFlights."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError("Selenium is required for Ixigo collection; install requirements-scrapers.txt.") from exc
    options = Options()
    candidates = [
        os.getenv("CHROME_BINARY"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Chromium\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Chromium\Application\chrome.exe"),
        str(BASE_DIR / "browser" / "chrome.exe"),
        str(BASE_DIR / "browser" / "chrome-win" / "chrome.exe"),
        str(BASE_DIR / "chromium" / "chrome.exe"),
        str(BASE_DIR / "chromium" / "chrome-win" / "chrome.exe"),
    ]
    browser_binary = next((path for path in candidates if path and Path(path).exists()), None)
    if browser_binary:
        options.binary_location = browser_binary
    if headless:
        options.add_argument("--headless=new")
    options.add_argument(f"--window-size={viewport[0]},{viewport[1]}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-features=Translate,MediaRouter")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    driver.execute_cdp_cmd("Network.enable", {})
    responses = []
    bodies = []
    stream_requests = {}
    completed_requests = set()
    try:
        try:
            driver.get(search_url(origin, destination, travel_date, cabin))
        except Exception as exc:
            responses.append({"status": "navigation_error", "error": str(exc)})
        deadline = time.monotonic() + 30
        last_count = 0
        settle_deadline = time.monotonic() + 8
        while time.monotonic() < deadline or time.monotonic() < settle_deadline:
            for entry in driver.get_log("performance"):
                try:
                    message = json.loads(entry["message"])["message"]
                    params = message.get("params", {})
                    if message.get("method") == "Network.responseReceived":
                        response = params.get("response", {})
                        url = response.get("url", "")
                        if "ixigo.com" not in url.lower():
                            continue
                        status_code = response.get("status")
                        if STREAM_HINT in url.lower():
                            request_id = params.get("requestId")
                            stream_requests[request_id] = {"status": status_code, "url": url, "resource_type": params.get("type")}
                            responses.append(stream_requests[request_id])
                    elif message.get("method") == "Network.loadingFinished":
                        request_id = params.get("requestId")
                        if request_id in stream_requests and request_id not in completed_requests:
                            completed_requests.add(request_id)
                            if stream_requests[request_id]["status"] == 200:
                                try:
                                    body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}).get("body", "")
                                    if body:
                                        bodies.append(body)
                                except Exception:
                                    pass
                except Exception:
                    continue
            if len(responses) != last_count:
                last_count = len(responses)
                settle_deadline = time.monotonic() + 8
            if responses and time.monotonic() >= settle_deadline:
                break
            time.sleep(0.4)
        for request_id, response in stream_requests.items():
            if response["status"] == 200 and request_id not in completed_requests:
                try:
                    body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}).get("body", "")
                    if body:
                        bodies.append(body)
                except Exception:
                    pass
        observations = extract_observations([frame for body in bodies for frame in parse_stream(body)], origin, destination, travel_date, cabin)
        status = "SUCCESS" if observations else ("SOURCE_ERROR" if not bodies else "NO_DATA")
        reason = "Fare observations captured" if observations else (f"Ixigo returned no successful stream response; responses={responses[-8:]}" if not bodies else "Successful stream responses contained no matching fare journeys")
        return {"route": f"{origin}-{destination}", "status": status, "reason": reason, "observations": observations, "observation_count": len(observations), "captured_streams": len(bodies), "response_count": len(responses), "source_url": search_url(origin, destination, travel_date, cabin)}
    finally:
        driver.quit()


async def collect_route(origin: str, destination: str, travel_date: str, cabin: str = "e", headless: bool = False, browser_engine: str = "chromium", viewport: tuple[int, int] = (1920, 1080)) -> dict[str, Any]:
    if browser_engine != "chromium":
        raise ValueError("Ixigo now uses the CompareFlights Selenium Chromium driver; select chromium.")
    return await asyncio.to_thread(_collect_route_selenium, origin, destination, travel_date, cabin, viewport, headless)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def save_route_result(result: dict[str, Any], run_date: str | None = None, suffix: str | None = None) -> Path:
    """Upsert one lead-time result into one route file for the run date."""
    folder = OUTPUT_DIR / (run_date or date.today().isoformat())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result['route']}.json"
    window = suffix or result.get("lead_label") or (f"T+{result['lead_days']}" if result.get("lead_days") is not None else "latest")
    with OUTPUT_LOCK:
        payload: dict[str, Any] = {"source": "ixigo", "route": result["route"], "run_date": run_date or date.today().isoformat(), "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "lead_windows": {}}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload.update({key: existing[key] for key in ("source", "route", "run_date") if key in existing})
                    payload["lead_windows"] = existing.get("lead_windows") if isinstance(existing.get("lead_windows"), dict) else {}
            except (OSError, ValueError):
                pass
        payload["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        payload["lead_windows"][window] = result
        _atomic_json_write(path, payload)
        _write_run_manifest(folder, payload["run_date"])
    return path


def _write_run_manifest(folder: Path, run_date: str) -> Path:
    route_files = sorted(path.name for path in folder.glob("*.json") if path.name != "collection.json")
    manifest = {"source": "ixigo", "run_date": run_date, "format": "route-files-with-lead-windows", "route_files": route_files, "file_count": len(route_files), "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    path = folder / "collection.json"
    _atomic_json_write(path, manifest)
    return path


def consolidate_legacy_run(run_date: str) -> int:
    """Merge older per-window files into one route file for this date."""
    folder = OUTPUT_DIR / run_date
    merged = 0
    with OUTPUT_LOCK:
        for legacy_path in (sorted(folder.glob("*-T+*.json")) if folder.exists() else []):
            match = re.match(r"^(?P<route>[A-Z]{3}-[A-Z]{3})-(?P<label>T\+\d+)\.json$", legacy_path.name)
            if not match:
                continue
            try:
                result = json.loads(legacy_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            result["route"] = result.get("route") or match.group("route")
            route_path = folder / f"{result['route']}.json"
            payload: dict[str, Any] = {"source": "ixigo", "route": result["route"], "run_date": run_date, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "lead_windows": {}}
            if route_path.exists():
                try:
                    existing = json.loads(route_path.read_text(encoding="utf-8"))
                    if isinstance(existing, dict):
                        payload["lead_windows"] = existing.get("lead_windows") if isinstance(existing.get("lead_windows"), dict) else {}
                except (OSError, ValueError):
                    pass
            payload["lead_windows"][match.group("label")] = result
            _atomic_json_write(route_path, payload)
            legacy_path.unlink()
            merged += 1
        if folder.exists():
            _write_run_manifest(folder, run_date)
    return merged


async def main_async(args: argparse.Namespace) -> None:
    result = await collect_route(args.origin, args.destination, args.date, args.cabin, args.headless, args.driver)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = save_route_result(result)
    print(json.dumps({"output": str(path), "status": result["status"], "observation_count": result["observation_count"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect one permissioned Ixigo route search.")
    parser.add_argument("origin"); parser.add_argument("destination"); parser.add_argument("--date", default=date.today().strftime("%d%m%Y")); parser.add_argument("--cabin", choices=CLASSES, default="e"); parser.add_argument("--driver", choices=("chromium",), default="chromium"); parser.add_argument("--headless", action="store_true", help="Run without a visible browser window.")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
