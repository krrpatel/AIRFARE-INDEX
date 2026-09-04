"""DGCA-backed route basket for the Airfare Price Index."""
from __future__ import annotations

import os
from dataclasses import dataclass

from airfare.dgca.basket import load_route_basket


@dataclass(frozen=True)
class RouteDef:
    origin: str
    destination: str
    label: str
    pair_route: str | None = None
    rank: int | None = None
    total_pax_12_months: int | None = None


def get_basket(top_n: int | None = None, direction_mode: str | None = None) -> list[RouteDef]:
    top_n = top_n if top_n is not None else _env_int("DGCA_TOP_N", 50)
    direction_mode = direction_mode or os.getenv("DGCA_DIRECTION_MODE", "bidirectional")
    return [
        RouteDef(
            origin=row.origin,
            destination=row.destination,
            label=row.label,
            pair_route=row.pair_route,
            rank=row.rank,
            total_pax_12_months=row.total_pax_12_months,
        )
        for row in load_route_basket(top_n=top_n, direction_mode=direction_mode)
    ]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Backward-compatible constant for older demo code. New code should call
# get_basket() so CLI/env changes are picked up at runtime.
PROTOTYPE_BASKET = get_basket()

