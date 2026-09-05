from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from airfare.airports.registry import require_domestic_route
from airfare.sources.base import AdapterResult, FareComponents, SearchRequest, SourceAdapter, StandardFareRecord

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "raw_airfare" / "compareflights"
PARSER_VERSION = "compareflights-normalized-json-v1"


class CompareFlightsOfflineAdapter(SourceAdapter):
    name = "compareflights"
    source_type = "ota"
    parser_version = PARSER_VERSION

    def __init__(self, root: str | Path | None = None, run_date: str | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.run_date = run_date

    def collect(self, request: SearchRequest) -> AdapterResult:
        require_domestic_route(request.origin, request.destination)
        route_file = self._route_file(request.origin, request.destination)
        if not route_file.exists():
            return AdapterResult(
                request=request,
                source_name=self.name,
                collected_at=datetime.now(timezone.utc),
                status="NOT_FOUND",
                records=[],
                error=f"No saved CompareFlights file found for {request.origin}-{request.destination}",
            )

        payload = json.loads(route_file.read_text(encoding="utf-8"))
        records = list(records_from_route_payload(payload, route_file))
        matching = [
            record for record in records
            if record.travel_date == request.travel_date
            and record.cabin_class.lower() == request.cabin_class.lower()
        ]
        return AdapterResult(
            request=request,
            source_name=self.name,
            collected_at=_parse_dt(payload.get("searched_at")) or datetime.now(timezone.utc),
            status="AVAILABLE" if matching else "NOT_FOUND",
            records=matching,
            raw_payload=payload,
            metadata={"source_file": str(route_file), "parser_version": self.parser_version},
        )

    def iter_all_records(self) -> list[StandardFareRecord]:
        records: list[StandardFareRecord] = []
        for path in self._run_dir().glob("*.json"):
            if path.name in {"summary.json", "completed_searches.json", "collection.json"}:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.extend(records_from_route_payload(payload, path))
        return records

    def _run_dir(self) -> Path:
        if self.run_date:
            return self.root / self.run_date
        dated = sorted([p for p in self.root.iterdir() if p.is_dir()]) if self.root.exists() else []
        if not dated:
            return self.root
        return dated[-1]

    def _route_file(self, origin: str, destination: str) -> Path:
        return self._run_dir() / f"{origin.upper()}-{destination.upper()}.json"


def records_from_route_payload(payload: dict[str, Any], source_file: Path) -> list[StandardFareRecord]:
    date_searched = payload.get("date_searched")
    observed_at = _parse_dt(payload.get("searched_at")) or _date_to_dt(date_searched)
    out: list[StandardFareRecord] = []
    route_origin = str(payload.get("origin") or "").upper()
    route_dest = str(payload.get("destination") or "").upper()
    require_domestic_route(route_origin, route_dest)

    for offset_key, offset in (payload.get("flights") or {}).items():
        departure_date = _parse_date(offset.get("departure_date"))
        if not departure_date:
            continue
        itineraries = offset.get("itineraries") or []
        if not itineraries:
            out.append(StandardFareRecord(
                source_name="compareflights",
                observed_at=observed_at,
                origin=route_origin,
                destination=route_dest,
                travel_date=departure_date,
                advance_purchase_days=int(offset.get("days_before_departure") or 0),
                availability_status="NOT_FOUND",
                source_file=str(source_file),
                parser_version=PARSER_VERSION,
                source_payload={"offset": offset_key, "error": offset.get("error")},
            ))
            continue

        for itinerary in itineraries:
            segments = itinerary.get("segments") or []
            first_segment = segments[0] if segments else {}
            offers = itinerary.get("offers") or []
            pricing = itinerary.get("pricing") or {}
            for offer in offers or [{}]:
                price = _num(offer.get("price")) or _num(pricing.get("lowest_price"))
                dep_dt = _parse_dt(first_segment.get("departure_datetime"))
                arr_dt = _parse_dt((segments[-1] if segments else {}).get("arrival_datetime"))
                record = StandardFareRecord(
                    source_name="compareflights",
                    observed_at=observed_at,
                    origin=route_origin,
                    destination=route_dest,
                    travel_date=departure_date,
                    advance_purchase_days=int(itinerary.get("days_before_departure") or offset.get("days_before_departure") or 0),
                    availability_status="AVAILABLE" if price is not None else "UNKNOWN",
                    airline_code=first_segment.get("airline_code"),
                    airline_name=first_segment.get("airline_name"),
                    flight_number="|".join(s.get("flight_number") or "" for s in segments).strip("|") or None,
                    departure_datetime=dep_dt,
                    arrival_datetime=arr_dt,
                    duration_minutes=_int(itinerary.get("total_duration_minutes")),
                    stops=_int(itinerary.get("stops")),
                    cabin_class=str(itinerary.get("cabin") or "Economy"),
                    fare_code=offer.get("fare_code"),
                    currency=str(offer.get("currency") or pricing.get("currency") or "INR"),
                    components=FareComponents(
                        offered_fare=price,
                        total_payable_fare=price,
                        source_native={"pricing": pricing, "offer": offer},
                    ),
                    baggage={
                        "checkin_baggage_kg": offer.get("checkin_baggage_kg"),
                        "cabin_baggage_kg": offer.get("cabin_baggage_kg"),
                    },
                    source_payload={
                        "offset": offset_key,
                        "reported_origin": itinerary.get("origin"),
                        "reported_destination": itinerary.get("destination"),
                        "itinerary_key": itinerary.get("itinerary_key"),
                        "segments": segments,
                        "layovers": itinerary.get("layovers"),
                    },
                    source_file=str(source_file),
                    parser_version=PARSER_VERSION,
                )
                require_domestic_route(record.origin, record.destination)
                out.append(record)
    return out


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _date_to_dt(value: Any) -> datetime:
    parsed = _parse_date(value)
    return datetime.combine(parsed or date.today(), time.min, tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
