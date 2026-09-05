"""Permission-aware Ixigo airfare collector.

This adapter captures the public Ixigo flight-search stream with Playwright,
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
from datetime import date
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
                            segments.append({"origin": pieces[0], "destination": pieces[1], "flight_number": pieces[2], "travel_date": _iso(pieces[3])})
                    if not segments or segments[0]["origin"] != origin or segments[-1]["destination"] != destination:
                        continue
                    for fare in flight_fare.get("fares") or []:
                        details = fare.get("fareDetails") or {}
                        amount = next((_number(details.get(key)) for key in ("displayFare", "totalFare", "fare", "amount") if _number(details.get(key)) is not None), None)
                        if amount is None:
                            continue
                        metadata = fare.get("fareMetadata") or []
                        provider = metadata[0].get("providerId") if metadata and isinstance(metadata[0], dict) else None
                        output.append({"source": "ixigo", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "origin": origin, "destination": destination, "travel_date": _iso(travel_date), "advance_purchase_days": None, "cabin_class": CLASSES.get(cabin, cabin), "airline_code": segments[0].get("airline_code"), "flight_number": "|".join(segment["flight_number"] for segment in segments), "stops": len(segments) - 1, "fare": {"amount": amount, "currency": "INR"}, "provider_id": provider, "fare_token_present": bool(details.get("fareToken")), "segments": segments})
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


def save_route_result(result: dict[str, Any], run_date: str | None = None, suffix: str | None = None) -> Path:
    folder = OUTPUT_DIR / (run_date or date.today().isoformat())
    folder.mkdir(parents=True, exist_ok=True)
    filename = result["route"] if not suffix else f"{result['route']}-{suffix}"
    path = folder / f"{filename}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


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
