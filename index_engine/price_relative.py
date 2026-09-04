"""
Price relative computation. See docs/METHODOLOGY.md sections 2-3.

Two levels:
  1. Bucket-level representative price = median total_fare of valid
     observations in a (route, bucket, day) cell.
  2. Route-level price relative for a day = geometric mean of that day's
     bucket-level price relatives (relative to each bucket's base-period
     price), weighted implicitly equally per active bucket.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class FareObs:
    route_id: int
    departure_bucket: str
    total_fare: float
    quality_status: str  # 'valid' | 'suspicious' | 'rejected'


def bucket_representative_price(observations: list[FareObs]) -> float | None:
    """Median total_fare across valid (non-rejected) observations in a cell.

    Suspicious observations are included in the raw record but EXCLUDED here
    per docs/METHODOLOGY.md §8 (median is used specifically because it is
    naturally robust, and we additionally drop flagged-suspicious rows).
    """
    fares = [o.total_fare for o in observations if o.quality_status == "valid"]
    if not fares:
        return None
    return statistics.median(fares)


def route_price_relative(
    current_bucket_prices: dict[str, float],
    base_bucket_prices: dict[str, float],
) -> float | None:
    """Geometric mean of per-bucket price relatives for one route on one day.

    Only buckets present in BOTH current and base data contribute — this is
    the standard "matched sample" constraint applied at the bucket level
    rather than the individual-ticket level (see METHODOLOGY.md §1 on why
    ticket-level matching doesn't work for airfare).
    """
    relatives = []
    for bucket, base_price in base_bucket_prices.items():
        current_price = current_bucket_prices.get(bucket)
        if current_price is None or base_price is None or base_price <= 0:
            continue
        relatives.append(current_price / base_price)

    if not relatives:
        return None

    # Geometric mean via log-space to avoid overflow on many buckets.
    log_mean = np.mean(np.log(np.array(relatives)))
    return float(np.exp(log_mean))
