"""Live CompareFlights airfare collector.

CompareFlights is a Travelpayouts white-label search page.  The page signs a
search, starts it, and polls a public results endpoint; this adapter follows
that same request flow and stores the returned fare observations.  It does
not attempt CAPTCHA, proxy, cookie, or anti-bot bypasses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "data" / "raw_airfare" / "compareflights"
SOURCE_URL = "https://compareflights.co.in/search/?origin={origin}&destination={destination}"
MARKER = os.getenv("COMPAREFLIGHTS_MARKER", "281573")
SEARCH_HOST = os.getenv("COMPAREFLIGHTS_HOST", "book.compareflights.co.in")
SIGN_URL = "https://api.apistp.com/whitelabels/web/flights/v1/search/sign"
START_URL = "https://tickets-api.apistp.com/search/wl/start"
AIRLINE_NAMES = {
    "6E": "IndiGo",
    "AI": "Air India",
    "IX": "Air India Express",
    "QP": "Akasa Air",
    "SG": "SpiceJet",
    "UK": "Vistara",
    "G8": "Go First",
}


class CompareFlightsCollectionError(RuntimeError):
    """Raised when the live source cannot start or return a search."""


def _request_json(url: str, payload: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Affiliate-Marker": MARKER,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; AirfarePriceIndex/1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8", "replace")), {
                str(key).lower(): str(value) for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise CompareFlightsCollectionError(f"CompareFlights endpoint returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CompareFlightsCollectionError(f"CompareFlights request failed: {exc}") from exc


def _search_payload(origin: str, destination: str, travel_date: str) -> dict[str, Any]:
    return {
        "citizenship": "RU",
        "client_features": {"badges": True},
        "currency_code": "INR",
        "host": SEARCH_HOST,
        "languages": {"EN": 1},
        "marker": f"{MARKER}.coin_search",
        "market_code": "IN",
        "search_params": {
            "directions": [{
                "origin": origin,
                "destination": destination,
                "date": travel_date,
                "is_destination_airport": False,
                "is_origin_airport": False,
            }],
            "passengers": {"adults": 1, "children": 0, "infants": 0},
            "trip_class": "Y",
        },
    }


def _start_search(origin: str, destination: str, travel_date: str) -> dict[str, Any]:
    payload = _search_payload(origin, destination, travel_date)
    signed, _ = _request_json(SIGN_URL, payload)
    signature = signed.get("signature") if isinstance(signed, dict) else None
    if not signature:
        raise CompareFlightsCollectionError("CompareFlights did not return a search signature")
    payload["signature"] = signature
    started, _ = _request_json(START_URL, payload)
    if not isinstance(started, dict) or not started.get("search_id") or not started.get("results_url"):
        raise CompareFlightsCollectionError("CompareFlights did not return a searchable result session")
    return started


def _poll_results(search: dict[str, Any], max_polls: int = 20) -> tuple[dict[str, Any] | None, int]:
    search_id = search["search_id"]
    results_url = search["results_url"]
    interval = max(1.0, min(5.0, float(search.get("polling_interval_ms") or 1000) / 1000))
    latest: dict[str, Any] | None = None
    poll_count = 0
    for poll_count in range(1, max_polls + 1):
        payload = {
            "search_id": search_id,
            "limit": 200,
            "order": "best",
            "filters": {},
            "search_by_airport": False,
            "required_tickets": [],
        }
        chunks, headers = _request_json(f"https://{results_url}/search/wl/results", payload)
        if isinstance(chunks, list):
            result_chunk = next((chunk for chunk in chunks if isinstance(chunk, dict) and chunk.get("chunk_id") == "results"), None)
            if result_chunk:
                latest = result_chunk
        if headers.get("x-stop-marker") or (latest and latest.get("tickets")):
            # A populated result chunk is usable immediately.  The source also
            # sends X-Stop-Marker once its asynchronous providers have settled.
            if headers.get("x-stop-marker") or poll_count >= 2:
                break
        time.sleep(interval)
    return latest, poll_count


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(re.sub(r"[^0-9.]", "", str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _iso_local(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T", 1)
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text) else None


def _airline_name(code: str | None, airlines: dict[str, Any]) -> str | None:
    if not code:
        return None
    details = airlines.get(code) if isinstance(airlines, dict) else None
    name = (((details or {}).get("name") or {}).get("en") or {}).get("default")
    return name or AIRLINE_NAMES.get(code, code)


def extract_observations(
    chunk: dict[str, Any],
    origin: str,
    destination: str,
    travel_date: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    """Convert the source ticket graph into the dashboard observation shape."""
    legs = chunk.get("flight_legs") or []
    airlines = chunk.get("airlines") or {}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ticket in chunk.get("tickets") or []:
        references: list[int] = []
        for segment in ticket.get("segments") or []:
            references.extend(int(value) for value in segment.get("flights") or [] if str(value).isdigit())
        selected_legs = [legs[index] for index in references if 0 <= index < len(legs)]
        if not selected_legs:
            continue
        first = selected_legs[0]
        last = selected_legs[-1]
        carrier = (first.get("operating_carrier_designator") or {})
        airline_code = str(carrier.get("carrier") or carrier.get("airline_id") or "").upper() or None
        segments = []
        for leg in selected_legs:
            leg_carrier = leg.get("operating_carrier_designator") or {}
            segments.append({
                "origin": leg.get("origin"),
                "destination": leg.get("destination"),
                "flight_number": f"{leg_carrier.get('carrier') or ''}{leg_carrier.get('number') or ''}" or None,
                "airline_code": leg_carrier.get("carrier") or leg_carrier.get("airline_id"),
                "departure": _iso_local(leg.get("local_departure_date_time")),
                "arrival": _iso_local(leg.get("local_arrival_date_time")),
            })
        if segments[0].get("origin") != origin or segments[-1].get("destination") not in {destination, None}:
            # The provider can include nearby metro airports.  Keep those
            # results because they are part of the source's selected city pair.
            pass
        departure = _iso_local(first.get("local_departure_date_time"))
        arrival = _iso_local(last.get("local_arrival_date_time"))
        duration = None
        try:
            duration = max(0, round((int(last["arrival_unix_timestamp"]) - int(first["departure_unix_timestamp"])) / 60))
        except (KeyError, TypeError, ValueError):
            pass
        stops = max(0, len(selected_legs) - 1)
        for proposal in ticket.get("proposals") or []:
            price = _number((proposal.get("price") or {}).get("value"))
            if price is None or price <= 0:
                continue
            terms = proposal.get("flight_terms") or {}
            term = terms.get("0") if isinstance(terms, dict) else None
            term = term if isinstance(term, dict) else {}
            baggage = term.get("baggage") or {}
            row = {
                "source": "compareflights",
                "observed_at": observed_at,
                "origin": origin,
                "destination": destination,
                "travel_date": travel_date,
                "advance_purchase_days": None,
                "cabin_class": "Economy",
                "airline_code": airline_code,
                "airline_name": _airline_name(airline_code, airlines),
                "flight_number": "|".join(str(item.get("flight_number")) for item in segments if item.get("flight_number")) or None,
                "stops": stops,
                "number_of_stops": stops,
                "fare": {"amount": price, "currency": (proposal.get("price") or {}).get("currency_code", "INR")},
                "fare_code": term.get("fare_code"),
                "checkin_baggage_kg": baggage.get("weight"),
                "cabin_baggage_kg": (term.get("handbags") or {}).get("weight"),
                "departure": departure,
                "arrival": arrival,
                "duration_minutes": duration,
                "availability_status": "AVAILABLE",
                "agent_id": proposal.get("agent_id"),
                "segments": segments,
            }
            key = (row["flight_number"], row["fare"]["amount"], row["agent_id"], departure)
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def collect_route(
    origin: str,
    destination: str,
    travel_date: str,
    lead_days: int | None = None,
    headless: bool = False,
    browser_engine: str = "chromium",
    viewport: tuple[int, int] = (1920, 1080),
) -> dict[str, Any]:
    """Run one live CompareFlights search.

    ``headless`` and ``viewport`` remain part of the common scraper contract;
    this source's white-label API is the network request made by its browser
    widget, so no local browser window is required for this adapter.
    """
    if browser_engine != "chromium":
        raise ValueError("CompareFlights uses the Chromium-compatible source API")
    origin, destination = origin.upper(), destination.upper()
    started = _start_search(origin, destination, travel_date)
    chunk, polls = _poll_results(started)
    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    observations = extract_observations(chunk or {}, origin, destination, travel_date, observed_at)
    for observation in observations:
        # The lead window is known by the task even though the provider does
        # not repeat it on every proposal.
        observation["advance_purchase_days"] = lead_days
    status = "SUCCESS" if observations else "NO_DATA"
    return {
        "source": "compareflights",
        "route": f"{origin}-{destination}",
        "status": status,
        "reason": "Fare observations captured" if observations else "The live source returned no priced itineraries",
        "observations": observations,
        "observation_count": len(observations),
        "captured_search_id": started.get("search_id"),
        "poll_count": polls,
        "source_url": SOURCE_URL.format(origin=origin, destination=destination),
        "results_url": started.get("results_url"),
        "lead_days": lead_days,
        "lead_label": f"T+{lead_days}" if lead_days is not None else None,
        "headless": bool(headless),
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def save_route_result(result: dict[str, Any], run_date: str | None = None, suffix: str | None = None) -> Path:
    """Upsert a lead-time result into one date/route file."""
    run_date = run_date or date.today().isoformat()
    folder = OUTPUT_DIR / run_date
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result['route']}.json"
    window = suffix or result.get("lead_label") or "latest"
    payload: dict[str, Any] = {
        "source": "compareflights",
        "route": result["route"],
        "run_date": run_date,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "lead_windows": {},
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload["lead_windows"] = existing.get("lead_windows") if isinstance(existing.get("lead_windows"), dict) else {}
        except (OSError, ValueError):
            pass
    payload["lead_windows"][window] = result
    _atomic_write(path, payload)
    _write_manifest(folder, run_date)
    return path


def _write_manifest(folder: Path, run_date: str) -> Path:
    route_files = sorted(path.name for path in folder.glob("*.json") if path.name != "collection.json")
    path = folder / "collection.json"
    _atomic_write(path, {"source": "compareflights", "run_date": run_date, "format": "route-files-with-lead-windows", "route_files": route_files, "file_count": len(route_files), "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect one live CompareFlights route search")
    parser.add_argument("origin")
    parser.add_argument("destination")
    parser.add_argument("--date", default=(date.today()).strftime("%Y-%m-%d"))
    parser.add_argument("--lead-days", type=int, default=None)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    result = collect_route(args.origin, args.destination, args.date, args.lead_days, args.headless)
    path = save_route_result(result)
    print(json.dumps({"output": str(path), "status": result["status"], "observation_count": result["observation_count"]}))


if __name__ == "__main__":
    main()
