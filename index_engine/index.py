"""
National Airfare Price Index computation. See docs/METHODOLOGY.md §4, §7, §10.

This module is deliberately pure (no DB/IO) so it is trivially unit-testable
and deterministic: same inputs -> same outputs, always. The caller (a
pipeline/service layer, not included here) is responsible for pulling data
from Postgres and passing it in, then persisting the result.
"""
from __future__ import annotations

from dataclasses import dataclass

from index_engine.methodology import MIN_WEEKLY_OBSERVATIONS_FOR_ROUTE


@dataclass(frozen=True)
class RouteDayResult:
    route_id: int
    price_relative: float | None   # None => no data this day
    weight: float                  # base-period fixed weight
    observation_count: int
    is_imputed: bool = False


@dataclass(frozen=True)
class NationalIndexResult:
    index_value: float
    routes_included: int
    routes_imputed: int
    routes_excluded: int


def compute_national_index(route_results: list[RouteDayResult]) -> NationalIndexResult:
    """
    National Index = 100 * sum(weight_i * price_relative_i) / sum(included weights)

    Routes with insufficient observations (see MIN_WEEKLY_OBSERVATIONS_FOR_ROUTE)
    or no price_relative at all are excluded and their weight is
    proportionally redistributed among the remaining routes, per
    METHODOLOGY.md §7 -- this is done here by simply renormalizing over the
    included subset, which is mathematically equivalent to proportional
    redistribution.
    """
    included = [
        r for r in route_results
        if r.price_relative is not None
        and (r.observation_count >= MIN_WEEKLY_OBSERVATIONS_FOR_ROUTE or r.is_imputed)
    ]
    excluded_count = len(route_results) - len(included)
    imputed_count = sum(1 for r in included if r.is_imputed)

    if not included:
        raise ValueError(
            "No routes have usable data for this day -- cannot compute an "
            "index value. This must surface as a data-quality alert, not a "
            "silently wrong number."
        )

    total_weight = sum(r.weight for r in included)
    if total_weight <= 0:
        raise ValueError("Total weight of included routes is zero or negative.")

    weighted_sum = sum(r.weight * r.price_relative for r in included)
    index_value = 100.0 * weighted_sum / total_weight

    return NationalIndexResult(
        index_value=round(index_value, 4),
        routes_included=len(included),
        routes_imputed=imputed_count,
        routes_excluded=excluded_count,
    )


def pct_change(current: float | None, previous: float | None) -> float | None:
    """Simple percentage change, guarding against None/zero previous values."""
    if current is None or previous is None or previous == 0:
        return None
    return round(100.0 * (current - previous) / previous, 3)
