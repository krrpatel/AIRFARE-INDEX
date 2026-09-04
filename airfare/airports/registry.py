from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AIRPORTS_FILE = PROJECT_ROOT / "data" / "airports" / "IndiaAirports.json"


@dataclass(frozen=True)
class Airport:
    code: str
    city: str
    name: str
    country_code: str
    country_name: str
    latitude: float | None
    longitude: float | None


@lru_cache(maxsize=1)
def load_airports(path: str | Path | None = None) -> dict[str, Airport]:
    source = Path(path) if path else AIRPORTS_FILE
    data = json.loads(source.read_text(encoding="utf-8"))
    airports: dict[str, Airport] = {}
    for row in data:
        code = str(row.get("airportCode", "")).upper().strip()
        if not code:
            continue
        airports[code] = Airport(
            code=code,
            city=str(row.get("airportCity") or ""),
            name=str(row.get("airportName") or ""),
            country_code=str(row.get("countryCode") or ""),
            country_name=str(row.get("countryName") or ""),
            latitude=_num(row.get("latitude")),
            longitude=_num(row.get("longitude")),
        )
    return airports


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_indian_airport(code: str, airports: dict[str, Airport] | None = None) -> bool:
    ref = airports or load_airports()
    airport = ref.get(code.upper().strip())
    return bool(airport and airport.country_code == "IN")


def require_domestic_route(origin: str, destination: str) -> None:
    origin = origin.upper().strip()
    destination = destination.upper().strip()
    if origin == destination:
        raise ValueError("origin and destination cannot be the same")
    airports = load_airports()
    if not is_indian_airport(origin, airports):
        raise ValueError(f"unknown or non-Indian origin airport: {origin}")
    if not is_indian_airport(destination, airports):
        raise ValueError(f"unknown or non-Indian destination airport: {destination}")


def airport_coords() -> dict[str, dict[str, float | str | None]]:
    return {
        code: {
            "code": code,
            "name": airport.city or airport.name,
            "airport_name": airport.name,
            "lat": airport.latitude,
            "lon": airport.longitude,
        }
        for code, airport in load_airports().items()
    }
