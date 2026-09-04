"""DGCA passenger-traffic weights for the Airfare Price Index."""
from __future__ import annotations

import os
from dataclasses import dataclass

from airfare.dgca.basket import load_route_basket
from index_engine.basket import RouteDef

METHODOLOGY_NOTE = (
    "DGCA-derived route-pair passenger traffic share over the latest available "
    "12 complete months. For bidirectional collection, each direction receives "
    "half of the undirected route-pair weight while raw observations preserve direction."
)


@dataclass(frozen=True)
class RouteWeight:
    route: RouteDef
    weight: float
    pair_weight: float
    methodology_note: str = METHODOLOGY_NOTE


def compute_route_weights(top_n: int | None = None, direction_mode: str | None = None) -> list[RouteWeight]:
    top_n = top_n if top_n is not None else _env_int("DGCA_TOP_N", 50)
    direction_mode = direction_mode or os.getenv("DGCA_DIRECTION_MODE", "bidirectional")
    rows = load_route_basket(top_n=top_n, direction_mode=direction_mode)
    return [
        RouteWeight(
            route=RouteDef(
                origin=row.origin,
                destination=row.destination,
                label=row.label,
                pair_route=row.pair_route,
                rank=row.rank,
                total_pax_12_months=row.total_pax_12_months,
            ),
            weight=row.directional_weight,
            pair_weight=row.pair_weight,
        )
        for row in rows
    ]


def weights_as_dict() -> dict[tuple[str, str], float]:
    return {(rw.route.origin, rw.route.destination): rw.weight for rw in compute_route_weights()}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


if __name__ == "__main__":
    weights = compute_route_weights()
    for rw in sorted(weights, key=lambda x: (x.route.rank or 10**9, x.route.origin, x.route.destination)):
        print(f"{rw.route.origin}-{rw.route.destination:3s} rank={rw.route.rank:02d} weight={rw.weight * 100:7.4f}%")
    print(f"\nSum check: {sum(rw.weight for rw in weights):.9f}")

