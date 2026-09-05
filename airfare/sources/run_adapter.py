"""Read date-partitioned source runs for the dashboard and exports.

The adapter keeps the dashboard independent from a scraper's native file
layout.  Ixigo and live CompareFlights stores one route file containing all
lead-time windows.  Historical CompareFlights route files remain supported. Both become the
same compact row shape at the API boundary.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from airfare.sources.compareflights.offline_adapter import CompareFlightsOfflineAdapter


SOURCE_LABELS = {
    "compareflights": "CompareFlights OTA",
    "ixigo": "Ixigo OTA",
}
_LEGACY_IXIGO_FILE = re.compile(r"^(?P<route>[A-Z]{3}-[A-Z]{3})-T\+(?P<days>\d+)\.json$")
_AIRLINE_NAMES = {"6E": "IndiGo", "AI": "Air India", "IX": "Air India Express", "QP": "Akasa Air", "SG": "SpiceJet", "UK": "Vistara", "G8": "Go First"}


class SourceRunAdapter:
    def __init__(self, root: str | Path | None = None):
        project_root = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else project_root / "data" / "raw_airfare"

    def run_dates(self, source: str) -> list[str]:
        source_dir = self.root / source
        if not source_dir.exists():
            return []
        return sorted(path.name for path in source_dir.iterdir() if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name))

    def manifest(self, source: str, run_date: str) -> dict[str, Any]:
        run_dir = self.root / source / run_date
        manifest_path = run_dir / "collection.json"
        if manifest_path.exists():
            try:
                return json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        files = sorted(path.name for path in run_dir.glob("*.json") if path.name not in {"collection.json", "summary.json", "completed_searches.json"})
        return {"source": source, "run_date": run_date, "format": "route-files", "route_files": files, "file_count": len(files)}

    def write_manifest(self, source: str, run_date: str) -> Path:
        """Persist a small source/date index without duplicating raw route data."""
        run_dir = self.root / source / run_date
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.manifest(source, run_date)
        manifest["format"] = "route-files"
        path = run_dir / "collection.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def rows(self, source: str, run_date: str) -> list[dict[str, Any]]:
        if source == "compareflights":
            return self._compareflights_rows(run_date)
        if source == "ixigo":
            return self._ixigo_rows(run_date)
        raise ValueError(f"Unsupported source: {source}")

    def _compareflights_rows(self, run_date: str) -> list[dict[str, Any]]:
        run_dir = self.root / "compareflights" / run_date
        rows = []
        # Live searches are stored as one route file with all T+n windows.
        # Keep the legacy parser below so older imported runs remain readable.
        for path in sorted(run_dir.glob("*.json")):
            if path.name in {"summary.json", "completed_searches.json", "collection.json"}:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            windows = payload.get("lead_windows") if isinstance(payload, dict) else None
            if isinstance(windows, dict):
                route = str(payload.get("route") or path.stem).upper()
                for label, result in windows.items():
                    if not isinstance(result, dict):
                        continue
                    for observation in result.get("observations") or []:
                        fare = observation.get("fare") if isinstance(observation.get("fare"), dict) else {}
                        lead_days = _lead_days(label, result.get("lead_days"))
                        rows.append({
                            "source": "compareflights", "run_date": run_date,
                            "observed_at": observation.get("observed_at") or result.get("observed_at"),
                            "route": route,
                            "origin": observation.get("origin") or route.split("-")[0],
                            "destination": observation.get("destination") or route.split("-")[-1],
                            "travel_date": observation.get("travel_date") or result.get("travel_date"),
                            "lead_window": label, "lead_days": lead_days,
                            "airline_code": observation.get("airline_code"),
                            "airline_name": observation.get("airline_name") or observation.get("airline_code"),
                            "flight_number": observation.get("flight_number"),
                            "fare": fare.get("amount") if fare else observation.get("fare_amount"),
                            "currency": fare.get("currency") if fare else observation.get("currency", "INR"),
                            "availability_status": observation.get("availability_status", "AVAILABLE"),
                            "fare_code": observation.get("fare_code"),
                            "number_of_stops": observation.get("number_of_stops", observation.get("stops")),
                            "departure": observation.get("departure"), "arrival": observation.get("arrival"),
                            "duration_minutes": observation.get("duration_minutes"),
                            "cabin_class": observation.get("cabin_class", "Economy"),
                            "checkin_baggage_kg": observation.get("checkin_baggage_kg"),
                            "cabin_baggage_kg": observation.get("cabin_baggage_kg"),
                        })
                continue
            legacy = CompareFlightsOfflineAdapter(root=self.root / "compareflights", run_date=run_date)
            for record in legacy.iter_all_records():
                if record.source_file != str(path):
                    continue
                fare = record.components.total_payable_fare or record.components.offered_fare
                rows.append({
                    "source": "compareflights", "run_date": run_date,
                    "observed_at": record.observed_at.isoformat() if record.observed_at else None,
                    "route": f"{record.origin}-{record.destination}", "origin": record.origin, "destination": record.destination,
                    "travel_date": record.travel_date.isoformat(), "lead_window": f"T+{record.advance_purchase_days}",
                    "lead_days": record.advance_purchase_days, "airline_code": record.airline_code,
                    "airline_name": record.airline_name, "flight_number": record.flight_number, "fare": fare,
                    "currency": record.currency, "availability_status": record.availability_status,
                    "fare_code": record.fare_code, "number_of_stops": record.stops,
                    "departure": record.departure_datetime.isoformat() if record.departure_datetime else None,
                    "arrival": record.arrival_datetime.isoformat() if record.arrival_datetime else None,
                    "duration_minutes": record.duration_minutes, "cabin_class": record.cabin_class,
                    "checkin_baggage_kg": record.baggage.get("checkin_baggage_kg"),
                    "cabin_baggage_kg": record.baggage.get("cabin_baggage_kg"),
                })
        return rows

    def _ixigo_rows(self, run_date: str) -> list[dict[str, Any]]:
        run_dir = self.root / "ixigo" / run_date
        rows: list[dict[str, Any]] = []
        for path in sorted(run_dir.glob("*.json")):
            if path.name == "collection.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            windows = payload.get("lead_windows") if isinstance(payload, dict) else None
            if isinstance(windows, dict):
                entries = [(label, value) for label, value in windows.items()]
            else:
                match = _LEGACY_IXIGO_FILE.match(path.name)
                label = f"T+{match.group('days')}" if match else None
                entries = [(label, payload)]
            for label, result in entries:
                if not isinstance(result, dict):
                    continue
                route = result.get("route") or payload.get("route") or (path.stem.split("-T+")[0] if "-T+" in path.stem else path.stem)
                observations = result.get("observations") or []
                for observation in observations:
                    fare = observation.get("fare") if isinstance(observation.get("fare"), dict) else {}
                    flight_number = observation.get("flight_number")
                    airline_code = observation.get("airline_code")
                    if not airline_code and flight_number:
                        match = re.match(r"([A-Za-z0-9]{2})", str(flight_number))
                        airline_code = match.group(1).upper() if match else None
                    rows.append({
                        "source": "ixigo",
                        "run_date": run_date,
                        "observed_at": observation.get("observed_at"),
                        "route": route,
                        "origin": observation.get("origin") or route.split("-")[0],
                        "destination": observation.get("destination") or route.split("-")[-1],
                        "travel_date": observation.get("travel_date"),
                        "lead_window": label,
                        "lead_days": int(label[2:]) if label and label.startswith("T+") and label[2:].isdigit() else observation.get("advance_purchase_days"),
                        "airline_code": airline_code,
                        "airline_name": observation.get("airline_name") or _AIRLINE_NAMES.get(airline_code, airline_code),
                        "flight_number": flight_number,
                        "fare": fare.get("amount") if fare else observation.get("fare_amount"),
                        "currency": fare.get("currency") if fare else observation.get("currency", "INR"),
                        "availability_status": "AVAILABLE",
                        "fare_code": observation.get("fare_code"),
                        "number_of_stops": observation.get("number_of_stops", observation.get("stops")),
                        "departure": observation.get("departure"),
                        "arrival": observation.get("arrival"),
                        "duration_minutes": observation.get("duration_minutes"),
                        "cabin_class": observation.get("cabin_class", "Economy"),
                        "checkin_baggage_kg": observation.get("checkin_baggage_kg"),
                        "cabin_baggage_kg": observation.get("cabin_baggage_kg"),
                    })
        return rows


def _lead_days(label: Any, fallback: Any = None) -> int | None:
    match = re.fullmatch(r"T\+(\d+)", str(label or ""))
    if match:
        return int(match.group(1))
    try:
        return int(fallback) if fallback is not None else None
    except (TypeError, ValueError):
        return None
