"""Build and validate compact, date-partitioned source artifacts.

Clean files are deliberately plain JSON so they can be reviewed, uploaded to
GitHub, and consumed by the dashboard without a database.  Raw source files
remain the audit trail; this module only writes after route completeness has
been checked.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from airfare.sources.run_adapter import SourceRunAdapter


class CleanDataValidationError(ValueError):
    """Raised when a source run is not complete enough to publish."""


class CleanSourceAdapter:
    schema_version = "airfare-clean-v1"

    def __init__(self, raw_adapter: SourceRunAdapter | None = None, root: str | Path | None = None):
        self.raw = raw_adapter or SourceRunAdapter()
        project_root = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else project_root / "data" / "clean_airfare"

    def path(self, source: str, run_date: str) -> Path:
        parsed = datetime.strptime(run_date, "%Y-%m-%d")
        return self.root / source / f"{parsed.strftime('%d%m%Y')}.json"

    def clean_dates(self, source: str) -> list[str]:
        source_dir = self.root / source
        if not source_dir.exists():
            return []
        dates = []
        for path in source_dir.glob("*.json"):
            match = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", path.stem)
            if not match:
                continue
            dates.append(f"{match.group(3)}-{match.group(2)}-{match.group(1)}")
        return sorted(set(dates))

    def read(self, source: str, run_date: str) -> dict[str, Any] | None:
        path = self.path(source, run_date)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else None

    def validate(self, source: str, run_date: str, expected_routes: list[str]) -> dict[str, Any]:
        try:
            rows = self.raw.rows(source, run_date)
        except (OSError, ValueError, KeyError) as exc:
            return {
                "valid": False, "source": source, "run_date": run_date,
                "expected_route_count": len(expected_routes), "found_route_count": 0,
                "missing_routes": list(expected_routes), "row_count": 0,
                "stop_values": [], "message": f"Source data could not be read: {exc}",
            }
        usable = [row for row in rows if self._usable(row)]
        found = {str(row.get("route", "")).upper() for row in usable if row.get("route")}
        expected = {str(route).upper() for route in expected_routes}
        missing = sorted(expected - found)
        valid = not missing and bool(expected)
        message = "All configured routes have usable observations." if valid else (
            f"Rejected: {len(missing)} configured route(s) are missing usable observations."
        )
        return {
            "valid": valid, "source": source, "run_date": run_date,
            "expected_route_count": len(expected), "found_route_count": len(found & expected),
            "missing_routes": missing, "row_count": len(rows),
            "usable_row_count": len(usable),
            "stop_values": sorted({int(row["number_of_stops"]) for row in usable if row.get("number_of_stops") is not None}),
            "message": message,
        }

    def build(
        self,
        source: str,
        run_date: str,
        expected_routes: list[str],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        validation = self.validate(source, run_date, expected_routes)
        if not validation["valid"]:
            raise CleanDataValidationError(validation["message"])
        rows = [self._normalize(row) for row in self.raw.rows(source, run_date)]
        route_names = sorted({row["route"] for row in rows if row.get("route")})
        routes = []
        for index, route in enumerate(route_names, 1):
            route_rows = [row for row in rows if row.get("route") == route]
            fares = [float(row["fare"]) for row in route_rows if self._usable(row)]
            routes.append({
                "route": route,
                "origin": route_rows[0].get("origin") if route_rows else route[:3],
                "destination": route_rows[0].get("destination") if route_rows else route[-3:],
                "row_count": len(route_rows),
                "usable_count": len(fares),
                "lowest_fare": min(fares) if fares else None,
                "average_fare": sum(fares) / len(fares) if fares else None,
                "lead_windows": sorted({row["lead_window"] for row in route_rows if row.get("lead_window")}),
                "airlines": sorted({row["airline_code"] for row in route_rows if row.get("airline_code")}),
            })
            if on_progress:
                on_progress(index, len(route_names))
        payload = {
            "schema_version": self.schema_version,
            "source": source,
            "run_date": run_date,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "route_count": len(routes),
            "row_count": len(rows),
            "number_of_stops_field": "number_of_stops",
            "validation": validation,
            "routes": routes,
            "rows": rows,
        }
        path = self.path(source, run_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
        return {"path": str(path), "source": source, "run_date": run_date, "route_count": len(routes), "row_count": len(rows), "validation": validation}

    @staticmethod
    def _usable(row: dict[str, Any]) -> bool:
        try:
            fare = float(row.get("fare"))
        except (TypeError, ValueError):
            return False
        return math.isfinite(fare) and fare > 0 and str(row.get("availability_status", "AVAILABLE")) in {"AVAILABLE", "UNKNOWN"}

    @staticmethod
    def _normalize(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        stops = normalized.get("number_of_stops", normalized.get("stops"))
        try:
            stops = int(stops) if stops is not None else None
        except (TypeError, ValueError):
            stops = None
        normalized["number_of_stops"] = stops
        normalized.pop("stops", None)
        return normalized
