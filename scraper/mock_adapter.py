"""
Mock adapter — implements SourceAdapter using the deterministic generator
logic from scripts/generate_mock_data.py, so MODE=mock exercises the exact
same interface real adapters will use. This is what the worker calls when
MODE=mock (see scripts/run_worker.py).
"""
from __future__ import annotations

import random
from datetime import date, datetime, timezone

from scraper.base import AdapterResult, FareComponents, SearchRequest, SourceAdapter, StandardFareRecord
from scripts.generate_mock_data import (
    AIRLINES, booking_curve_multiplier, holiday_multiplier, route_base_anchor, weekday_multiplier,
)


class MockAdapter(SourceAdapter):
    name = "mock"
    source_type = "mock"
    parser_version = "mock-2"

    def collect(self, request: SearchRequest) -> AdapterResult:
        now = datetime.now(timezone.utc)
        records = self.fetch(request.origin, request.destination, request.travel_date)
        return AdapterResult(
            request=request,
            source_name=self.name,
            collected_at=now,
            status="AVAILABLE" if records else "NOT_FOUND",
            records=records,
            metadata={"parser_version": self.parser_version},
        )

    def fetch(self, origin: str, destination: str, travel_date: date) -> list[StandardFareRecord]:
        now = datetime.now(timezone.utc)
        days_out = max((travel_date - now.date()).days, 0)
        base_anchor = route_base_anchor(origin, destination)
        rng = random.Random(hash((origin, destination, travel_date, now.date())))

        records = []
        for code, airline_name, factor in AIRLINES:
            if rng.random() < 0.15:
                continue
            mult = (
                booking_curve_multiplier(days_out)
                * holiday_multiplier(travel_date)
                * weekday_multiplier(travel_date)
                * factor
            )
            fare = base_anchor * mult * rng.gauss(1.0, 0.06)
            base_fare = round(fare * 0.85, 2)
            taxes = round(fare * 0.15, 2)
            records.append(StandardFareRecord(
                source_name=self.name,
                observed_at=now,
                origin=origin,
                destination=destination,
                travel_date=travel_date,
                advance_purchase_days=days_out,
                availability_status="AVAILABLE",
                airline_code=code,
                airline_name=airline_name,
                flight_number=None,
                departure_datetime=None,
                arrival_datetime=None,
                duration_minutes=None,
                stops=0,
                cabin_class="Economy",
                currency="INR",
                components=FareComponents(
                    base_fare=base_fare,
                    taxes=taxes,
                    offered_fare=round(base_fare + taxes, 2),
                    total_payable_fare=round(base_fare + taxes, 2),
                    source_native={"mock_airline_factor": factor},
                ),
            ))
        return records
