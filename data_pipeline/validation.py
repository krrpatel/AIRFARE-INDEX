"""
Observation-level validation. See docs/METHODOLOGY.md §8 for the
three-tier treatment (rejected / suspicious / valid) this feeds into.

This module ONLY classifies "clearly invalid" (tier 1) -- statistical
outlier detection (IQR / isolation forest, tier 2) lives in
data_pipeline/outliers.py and runs on data that already passed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from airfare.airports.registry import is_indian_airport, load_airports

VALID_IATA_CODES: set[str] = set(load_airports())


@dataclass
class RawObservation:
    origin: str
    destination: str
    travel_date: date
    booking_ts: datetime
    total_fare: float
    base_fare: float
    taxes: float
    duration_minutes: int | None
    currency: str


@dataclass
class ValidationResult:
    is_valid: bool
    reasons: list[str]


def validate_observation(obs: RawObservation) -> ValidationResult:
    reasons: list[str] = []

    if not is_indian_airport(obs.origin):
        reasons.append(f"invalid_airport:{obs.origin}")
    if not is_indian_airport(obs.destination):
        reasons.append(f"invalid_airport:{obs.destination}")
    if obs.origin == obs.destination:
        reasons.append("origin_equals_destination")

    if obs.total_fare < 0 or obs.base_fare < 0 or obs.taxes < 0:
        reasons.append("negative_fare")

    # total_fare should roughly equal base_fare + taxes (allow small rounding drift)
    if abs((obs.base_fare + obs.taxes) - obs.total_fare) > 5.0:
        reasons.append("fare_components_mismatch")

    if obs.travel_date < obs.booking_ts.date():
        reasons.append("travel_date_before_booking_date")

    if obs.duration_minutes is not None and (obs.duration_minutes < 20 or obs.duration_minutes > 900):
        reasons.append("impossible_duration")

    if obs.currency != "INR":
        reasons.append("currency_mismatch")

    return ValidationResult(is_valid=(len(reasons) == 0), reasons=reasons)
