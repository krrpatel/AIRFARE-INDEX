"""Small file-backed read model for the dashboard.

The scraper and clean adapter own writes.  This module owns fast, read-only
aggregations over clean JSON artifacts, with a raw-file fallback while a run
is waiting to be adapted.  It intentionally has no database dependency.
"""
from __future__ import annotations

import math
import statistics
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from airfare.airports.registry import load_airports
from airfare.dgca.basket import load_route_basket
from airfare.sources.clean_adapter import CleanSourceAdapter
from airfare.sources.run_adapter import SOURCE_LABELS, SourceRunAdapter


def _fare(row: dict[str, Any]) -> float | None:
    try:
        value = float(row.get("fare"))
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _round(value: Any, digits: int = 2) -> float | None:
    return round(float(value), digits) if value is not None else None


class FileAirfareReadModel:
    def __init__(self, raw: SourceRunAdapter | None = None, clean: CleanSourceAdapter | None = None):
        self.raw = raw or SourceRunAdapter()
        self.clean = clean or CleanSourceAdapter(self.raw)
        self._cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]], str]] = {}
        self._lock = threading.RLock()

    def dates(self, source: str = "compareflights") -> list[str]:
        return sorted(set(self.raw.run_dates(source)) | set(self.clean.clean_dates(source)))

    def rows(self, source: str = "compareflights", run_date: str | None = None) -> list[dict[str, Any]]:
        dates = [run_date] if run_date else self.dates(source)
        result: list[dict[str, Any]] = []
        for current_date in dates:
            if not current_date:
                continue
            cache_key = (source, current_date)
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached and cached[0] == self._signature(source, current_date):
                    result.extend(cached[1])
                    continue
                payload = self.clean.read(source, current_date)
                if payload:
                    current = [self._normalize(row, source, current_date) for row in payload.get("rows", [])]
                    mode = "clean"
                else:
                    current = [self._normalize(row, source, current_date) for row in self.raw.rows(source, current_date)]
                    mode = "raw-fallback"
                signature = self._signature(source, current_date)
                self._cache[cache_key] = (signature, current, mode)
            result.extend(current)
        return result

    def row_source_mode(self, source: str, run_date: str) -> str:
        payload = self.clean.read(source, run_date)
        return "clean" if payload else "raw-fallback"

    def invalidate(self, source: str | None = None, run_date: str | None = None) -> None:
        with self._lock:
            if source is None:
                self._cache.clear()
            else:
                for key in list(self._cache):
                    if key[0] == source and (run_date is None or key[1] == run_date):
                        self._cache.pop(key, None)

    def date_range(self, source: str = "compareflights") -> dict[str, str | None]:
        values = self.dates(source)
        return {"min": values[0] if values else None, "max": values[-1] if values else None}

    def current_index(self, source: str = "compareflights") -> dict[str, Any] | None:
        series = self.index_history(source)
        if not series:
            return None
        current = series[-1]
        previous_week = series[-8] if len(series) > 7 else None
        previous_month = series[-31] if len(series) > 30 else None
        current = dict(current)
        current["wow_change_pct"] = self._change(current["index_value"], previous_week["index_value"] if previous_week else None)
        current["mom_change_pct"] = self._change(current["index_value"], previous_month["index_value"] if previous_month else None)
        current["data_mode"] = "observed_file_data"
        current["source"] = SOURCE_LABELS.get(source, source)
        return current

    def index_history(self, source: str = "compareflights", start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        rows = self.rows(source)
        by_date_route: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in rows:
            booking_date = row.get("run_date")
            fare = _fare(row)
            if not booking_date or fare is None or not self._in_range(booking_date, start, end):
                continue
            by_date_route[(booking_date, row.get("route", "UNKNOWN"))].append(fare)
        routes = sorted({route for _, route in by_date_route})
        base: dict[str, float] = {}
        daily: dict[str, list[float]] = defaultdict(list)
        daily_counts: dict[str, int] = defaultdict(int)
        for (booking_date, route), fares in by_date_route.items():
            average = sum(fares) / len(fares)
            base.setdefault(route, average)
            if base[route] > 0:
                daily[booking_date].append(100 * average / base[route])
                daily_counts[booking_date] += len(fares)
        return [{"index_date": day, "index_value": _round(sum(values) / len(values)), "avg_fare": None, "observation_count": daily_counts[day], "route_count": len(values), "data_mode": "observed_file_data"} for day, values in sorted(daily.items())]

    def routes(self, source: str = "compareflights") -> list[dict[str, Any]]:
        known = {(row.get("origin"), row.get("destination")) for row in self.rows(source) if row.get("origin") and row.get("destination")}
        basket = load_route_basket(top_n=100, direction_mode="bidirectional")
        airports = load_airports()
        output = []
        for item in basket:
            route = (item.origin, item.destination)
            output.append({
                "origin": item.origin, "destination": item.destination,
                "label": item.label, "weight": item.directional_weight,
                "pair_route": item.pair_route, "rank": item.rank,
                "origin_name": airports.get(item.origin).name if airports.get(item.origin) else item.origin,
                "destination_name": airports.get(item.destination).name if airports.get(item.destination) else item.destination,
                "data_available": route in known,
            })
        return output

    def route_analytics(self, source: str = "compareflights", start: str | None = None, end: str | None = None, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        rows = [row for row in self.rows(source) if self._in_range(row.get("run_date"), start, end)]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if _fare(row) is not None:
                grouped[(row.get("route", "UNKNOWN"), row.get("airline_code") or "UNKNOWN")].append(row)
        latest_indices = self._latest_route_indices(source)
        output = []
        for (route, airline), group in grouped.items():
            fares = [_fare(row) for row in group]
            fares = [value for value in fares if value is not None]
            if not fares:
                continue
            origin, destination = route.split("-", 1) if "-" in route else (route, "")
            output.append({
                "origin": origin, "destination": destination, "label": route,
                "weight": None, "airline_code": airline,
                "observation_count": len(fares), "average_fare": _round(sum(fares) / len(fares)),
                "median_fare": _round(_median(fares)), "lowest_fare": _round(min(fares)), "highest_fare": _round(max(fares)),
                "latest_index": latest_indices.get(route),
            })
        output.sort(key=lambda row: (row["average_fare"] if row["average_fare"] is not None else float("inf"), row["label"], row["airline_code"]))
        return output[offset:offset + limit]

    def analytics(self, source: str = "compareflights", start: str | None = None, end: str | None = None, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        rows = [row for row in self.rows(source) if self._in_range(row.get("run_date"), start, end) and _fare(row) is not None]
        route_groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            route_groups[row.get("route", "UNKNOWN")].append(_fare(row))
        ranking = [{"route": route, "label": route, "average_fare": _round(sum(values) / len(values)), "index": self._latest_route_indices(source).get(route)} for route, values in route_groups.items()]
        ranking.sort(key=lambda item: item["average_fare"])
        buckets = [(0, 3000, "< Rs 3k"), (3000, 6000, "Rs 3k-6k"), (6000, 10000, "Rs 6k-10k"), (10000, 15000, "Rs 10k-15k"), (15000, float("inf"), "Rs 15k+")]
        distribution = [{"bucket": label, "count": sum(1 for row in rows if low <= _fare(row) < high)} for low, high, label in buckets]
        month_groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            month_groups[str(row.get("run_date", ""))[:7]].append(_fare(row))
        seasonality = [{"month": month, "average_fare": _round(sum(values) / len(values)), "observation_count": len(values)} for month, values in sorted(month_groups.items())]
        return {"route_ranking": ranking[offset:offset + limit], "fare_distribution": distribution, "seasonality": seasonality, "route_count": len(ranking), "date_range": self.date_range(source), "data_mode": "observed_file_data"}

    def route_detail(self, origin: str, destination: str, source: str = "compareflights") -> dict[str, Any] | None:
        route = f"{origin.upper()}-{destination.upper()}"
        rows = [row for row in self.rows(source) if row.get("route", "").upper() == route and _fare(row) is not None]
        basket = next((item for item in load_route_basket(top_n=100, direction_mode="bidirectional") if item.origin == origin.upper() and item.destination == destination.upper()), None)
        if not rows and basket is None:
            return None
        history = self._route_history(rows)
        airline_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        lead_groups: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in rows:
            airline_groups[row.get("airline_code") or "UNKNOWN"].append(row)
            lead_groups[(row.get("airline_code") or "UNKNOWN", row.get("lead_window") or "Unknown")].append(_fare(row))
        airlines = []
        for code, group in sorted(airline_groups.items()):
            fares = [_fare(row) for row in group if _fare(row) is not None]
            airlines.append({"airline_code": code, "airline_name": next((row.get("airline_name") for row in group if row.get("airline_name")), code), "record_count": len(group), "lowest_fare": _round(min(fares)) if fares else None, "average_fare": _round(sum(fares) / len(fares)) if fares else None})
        lead_series = []
        for code in sorted({key[0] for key in lead_groups}):
            points = []
            for (_, label), fares in sorted(lead_groups.items(), key=lambda item: self._lead_sort(item[0][1])):
                if _ == code:
                    points.append({"date": label, "value": _round(sum(fares) / len(fares)), "observation_count": len(fares)})
            if points:
                lead_series.append({"route": code, "label": next((row["airline_name"] for row in airlines if row["airline_code"] == code), code), "series": points})
        fares = [_fare(row) for row in rows if _fare(row) is not None]
        offers = [{"airline_code": row.get("airline_code"), "airline_name": row.get("airline_name") or row.get("airline_code"), "flight_number": row.get("flight_number"), "travel_date": row.get("travel_date"), "advance_purchase_days": row.get("lead_days"), "departure": row.get("departure"), "arrival": row.get("arrival"), "duration_minutes": row.get("duration_minutes"), "stops": row.get("number_of_stops"), "fare_code": row.get("fare_code"), "currency": row.get("currency", "INR"), "payable_fare": _fare(row), "checkin_baggage_kg": row.get("checkin_baggage_kg"), "cabin_baggage_kg": row.get("cabin_baggage_kg"), "source": SOURCE_LABELS.get(source, source), "run_date": row.get("run_date")} for row in sorted(rows, key=lambda item: _fare(item) or float("inf"))[:100]]
        return {"label": basket.label if basket else route, "route": {"origin": origin.upper(), "destination": destination.upper()}, "history": history, "market_snapshot": {"source": SOURCE_LABELS.get(source, source), "observation_count": len(rows), "fare_count": len(fares), "lowest_payable_fare": _round(min(fares)) if fares else None, "average_payable_fare": _round(sum(fares) / len(fares)) if fares else None, "airlines": airlines, "lead_time_series": lead_series, "fare_components": {"base_fare": None, "taxes": None, "airport_charges": None, "mandatory_total": "Not separately exposed by the imported source"}, "note": "Payable fares are normalized from the source. Base fare, tax, and statutory components remain null when the source does not expose them."}, "offers": offers, "data_mode": "observed_file_data"}

    def airlines(self, source: str = "compareflights") -> list[dict[str, Any]]:
        rows = [row for row in self.rows(source) if _fare(row) is not None and row.get("airline_code")]
        dates = self.dates(source)
        latest_date = dates[-1] if dates else None
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[row["airline_code"]].append(row)
        output = []
        for code, group in groups.items():
            fares = [_fare(row) for row in group]
            latest = [_fare(row) for row in group if row.get("run_date") == latest_date and _fare(row) is not None]
            mean = sum(fares) / len(fares)
            std = statistics.pstdev(fares) if len(fares) > 1 else 0
            output.append({"airline_code": code, "airline_name": next((row.get("airline_name") for row in group if row.get("airline_name")), code), "latest_index_date": latest_date, "latest_avg_fare": _round(sum(latest) / len(latest)) if latest else _round(sum(fares) / len(fares)), "latest_index_value": _round(self._airline_index(group, latest_date)), "route_coverage": len({row.get("route") for row in group}), "fare_volatility_coeff": _round(std / mean, 4) if mean else None})
        return sorted(output, key=lambda item: item["latest_avg_fare"] or float("inf"))

    def airline_history(self, code: str, source: str = "compareflights") -> list[dict[str, Any]]:
        rows = [row for row in self.rows(source) if row.get("airline_code") == code and _fare(row) is not None]
        by_date: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            by_date[row["run_date"]].append(_fare(row))
        values = [{"index_date": day, "avg_fare": _round(sum(fares) / len(fares)), "index_value": None, "observation_count": len(fares)} for day, fares in sorted(by_date.items())]
        base = values[0]["avg_fare"] if values else None
        for item in values:
            item["index_value"] = _round(100 * item["avg_fare"] / base) if base else None
        return values

    def airline_route_history(self, code: str, top_n: int = 5, source: str = "compareflights") -> dict[str, Any]:
        rows = [row for row in self.rows(source) if row.get("airline_code") == code and _fare(row) is not None]
        counts = defaultdict(int)
        for row in rows:
            counts[row.get("route", "UNKNOWN")] += 1
        selected = [route for route, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:top_n]]
        series = []
        for route in selected:
            by_date: dict[str, list[float]] = defaultdict(list)
            for row in rows:
                if row.get("route") == route:
                    by_date[row["run_date"]].append(_fare(row))
            series.append({"route": route, "label": route, "observation_count": counts[route], "series": [{"date": day, "value": _round(sum(fares) / len(fares)), "observation_count": len(fares)} for day, fares in sorted(by_date.items())]})
        return {"airline_code": code, "top_n": top_n, "routes": series}

    def weekly(self, source: str = "compareflights") -> dict[str, Any]:
        dates = self.dates(source)[-7:]
        rows = [row for row in self.rows(source) if row.get("run_date") in dates and _fare(row) is not None]
        by_airline: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        national: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            code = row.get("airline_code") or "UNKNOWN"
            by_airline[code][row["run_date"]].append(_fare(row))
            national[row["run_date"]].append(_fare(row))
        airlines = [{"route": code, "label": code, "series": [{"date": day, "value": _round(sum(values) / len(values)), "observation_count": len(values)} for day, values in sorted(days.items())]} for code, days in sorted(by_airline.items())]
        return {"date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None}, "airlines": airlines, "national": [{"date": day, "value": _round(sum(values) / len(values))} for day, values in sorted(national.items())], "observation_count": len(rows), "data_mode": "observed_file_data"}

    def top_movers(self, source: str = "compareflights", limit: int = 5) -> dict[str, Any]:
        history = self._route_date_indices(source)
        dates = sorted({day for values in history.values() for day in values})
        if not dates:
            return {"index_date": None, "top_increasing": [], "top_decreasing": []}
        latest = dates[-1]
        previous = dates[-2] if len(dates) > 1 else None
        movers = []
        for route, values in history.items():
            if previous not in values:
                continue
            current_value, previous_value = values.get(latest), values.get(previous)
            if previous_value:
                movers.append({"route": route, "origin": route.split("-")[0], "destination": route.split("-")[-1], "current_value": _round(current_value), "prev_value": _round(previous_value), "change_pct": _round(100 * (current_value - previous_value) / previous_value, 3)})
        return {"index_date": latest, "top_increasing": sorted(movers, key=lambda row: row["change_pct"], reverse=True)[:limit], "top_decreasing": sorted(movers, key=lambda row: row["change_pct"])[:limit]}

    def data_quality(self, source: str = "compareflights") -> dict[str, Any]:
        rows = self.rows(source)
        valid = sum(1 for row in rows if _fare(row) is not None and row.get("availability_status") == "AVAILABLE")
        flagged = len(rows) - valid
        return {"total_observations": len(rows), "by_quality_status": {"valid": valid, "suspicious": flagged}, "anomaly_count": flagged, "data_mode": "observed_file_data"}

    def fare_history(self, origin: str, destination: str, source: str = "compareflights") -> list[dict[str, Any]]:
        route = f"{origin.upper()}-{destination.upper()}"
        return [{"travel_date": row.get("travel_date"), "booking_date": row.get("run_date"), "total_fare": _fare(row), "departure_bucket": row.get("lead_window"), "days_to_departure": row.get("lead_days"), "stops": row.get("number_of_stops")} for row in self.rows(source) if row.get("route") == route and _fare(row) is not None]

    def holiday_analytics(self, source: str = "compareflights") -> dict[str, Any]:
        path = self.raw.root / "ixigo" / "holidays.json"
        try:
            payload = __import__("json").loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {"data": []}
        holidays = payload.get("data", []) if isinstance(payload, dict) else []
        dates: set[str] = set()
        for item in holidays:
            try:
                start = date.fromisoformat(str(item.get("startDate")))
                end = date.fromisoformat(str(item.get("endDate") or item.get("startDate")))
                dates.update((start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1))
            except (TypeError, ValueError):
                continue
        rows = [row for row in self.rows(source) if _fare(row) is not None]
        holiday_fares = [_fare(row) for row in rows if row.get("travel_date") in dates]
        normal_fares = [_fare(row) for row in rows if row.get("travel_date") not in dates]
        breakdown = []
        for holiday in holidays:
            start = str(holiday.get("startDate") or "")
            end = str(holiday.get("endDate") or start)
            matched = [_fare(row) for row in rows if row.get("travel_date") and start <= row["travel_date"] <= end]
            breakdown.append({"name": holiday.get("name"), "start_date": start, "end_date": end, "observation_count": len(matched), "average_fare": _round(sum(matched) / len(matched)) if matched else None})
        return {"source": "Holiday calendar", "source_url": "https://www.ixigo.com/growth/api/v1/holidayCalendar", "holidays": holidays, "holiday_breakdown": breakdown, "summary": {"holiday_observations": len(holiday_fares), "normal_observations": len(normal_fares), "holiday_average_fare": _round(sum(holiday_fares) / len(holiday_fares)) if holiday_fares else None, "normal_average_fare": _round(sum(normal_fares) / len(normal_fares)) if normal_fares else None}, "series": [{"date": row["travel_date"], "value": _fare(row), "category": "Holiday" if row.get("travel_date") in dates else "Normal"} for row in rows[:1000]]}

    def _signature(self, source: str, run_date: str) -> float:
        path = self.clean.path(source, run_date)
        if path.exists():
            return path.stat().st_mtime_ns
        run_dir = self.raw.root / source / run_date
        return max((item.stat().st_mtime_ns for item in run_dir.glob("*.json")), default=0)

    @staticmethod
    def _normalize(row: dict[str, Any], source: str, run_date: str) -> dict[str, Any]:
        output = dict(row)
        output["source"] = source
        output["run_date"] = output.get("run_date") or run_date
        output["route"] = str(output.get("route") or f"{output.get('origin', '')}-{output.get('destination', '')}").upper()
        output["origin"] = str(output.get("origin") or output["route"].split("-")[0]).upper()
        output["destination"] = str(output.get("destination") or output["route"].split("-")[-1]).upper()
        output["number_of_stops"] = output.get("number_of_stops", output.get("stops"))
        try:
            output["number_of_stops"] = int(output["number_of_stops"]) if output["number_of_stops"] is not None else None
        except (TypeError, ValueError):
            output["number_of_stops"] = None
        if output.get("lead_days") is None and str(output.get("lead_window", "")).startswith("T+"):
            try:
                output["lead_days"] = int(str(output["lead_window"])[2:])
            except ValueError:
                pass
        output.setdefault("departure", None)
        output.setdefault("arrival", None)
        output.setdefault("duration_minutes", None)
        output.setdefault("advance_purchase_days", output.get("lead_days"))
        return output

    def _latest_route_indices(self, source: str) -> dict[str, float | None]:
        history = self._route_date_indices(source)
        return {route: values[max(values)] for route, values in history.items() if values}

    def _route_date_indices(self, source: str) -> dict[str, dict[str, float]]:
        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in self.rows(source):
            fare = _fare(row)
            if fare is not None:
                grouped[(row.get("route", "UNKNOWN"), row.get("run_date"))].append(fare)
        base: dict[str, float] = {}
        result: dict[str, dict[str, float]] = defaultdict(dict)
        for (route, day), fares in sorted(grouped.items()):
            average = sum(fares) / len(fares)
            base.setdefault(route, average)
            if base[route]:
                result[route][day] = 100 * average / base[route]
        return result

    def _route_history(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if _fare(row) is not None:
                grouped[row.get("run_date")].append(_fare(row))
        if not grouped:
            return []
        base = sum(grouped[min(grouped)]) / len(grouped[min(grouped)])
        return [{"index_date": day, "index_value": _round(100 * (sum(fares) / len(fares)) / base), "avg_fare": _round(sum(fares) / len(fares)), "observation_count": len(fares)} for day, fares in sorted(grouped.items())]

    @staticmethod
    def _airline_index(rows: list[dict[str, Any]], latest_date: str | None) -> float | None:
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if _fare(row) is not None:
                grouped[row.get("run_date")].append(_fare(row))
        if not grouped or not latest_date or latest_date not in grouped:
            return None
        first_day = min(grouped)
        base = sum(grouped[first_day]) / len(grouped[first_day])
        latest = sum(grouped[latest_date]) / len(grouped[latest_date])
        return 100 * latest / base if base else None

    @staticmethod
    def _change(current: float | None, previous: float | None) -> float | None:
        return _round(100 * (current - previous) / previous, 3) if current is not None and previous else None

    @staticmethod
    def _in_range(value: str | None, start: str | None, end: str | None) -> bool:
        return bool(value) and (not start or value >= start) and (not end or value <= end)

    @staticmethod
    def _lead_sort(label: str) -> int:
        try:
            return int(str(label).replace("T+", ""))
        except ValueError:
            return 10**6
