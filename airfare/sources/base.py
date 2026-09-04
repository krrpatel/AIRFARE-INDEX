from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

AvailabilityStatus = Literal["AVAILABLE", "SOLD_OUT", "CANCELLED", "NOT_FOUND", "SOURCE_ERROR", "UNKNOWN"]


@dataclass(frozen=True)
class SearchRequest:
    origin: str
    destination: str
    travel_date: date
    cabin_class: str = "Economy"
    passengers: int = 1


@dataclass
class FareComponents:
    base_fare: float | None = None
    taxes: float | None = None
    airport_charges: float | None = None
    udf: float | None = None
    psf_asf: float | None = None
    fuel_surcharge: float | None = None
    convenience_fee: float | None = None
    service_fee: float | None = None
    other_mandatory_charges: float | None = None
    published_fare: float | None = None
    offered_fare: float | None = None
    total_payable_fare: float | None = None
    source_native: dict[str, Any] = field(default_factory=dict)


@dataclass
class StandardFareRecord:
    source_name: str
    observed_at: datetime
    origin: str
    destination: str
    travel_date: date
    advance_purchase_days: int
    availability_status: AvailabilityStatus
    airline_code: str | None = None
    airline_name: str | None = None
    flight_number: str | None = None
    departure_datetime: datetime | None = None
    arrival_datetime: datetime | None = None
    duration_minutes: int | None = None
    stops: int | None = None
    cabin_class: str = "Economy"
    fare_family: str | None = None
    fare_code: str | None = None
    currency: str = "INR"
    components: FareComponents = field(default_factory=FareComponents)
    baggage: dict[str, Any] = field(default_factory=dict)
    source_payload: dict[str, Any] = field(default_factory=dict)
    source_file: str | None = None
    collection_run_id: str | None = None
    parser_version: str | None = None


@dataclass
class AdapterResult:
    request: SearchRequest
    source_name: str
    collected_at: datetime
    status: AvailabilityStatus
    records: list[StandardFareRecord]
    raw_payload: dict[str, Any] | list[Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    name: str
    source_type: str
    parser_version: str

    @abstractmethod
    def collect(self, request: SearchRequest) -> AdapterResult:
        """Collect or import one source result and return standardized records."""
        ...

