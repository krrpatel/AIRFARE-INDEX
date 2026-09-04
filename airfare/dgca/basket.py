from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASKET_FILE = PROJECT_ROOT / "data" / "dgca" / "output" / "top50.json"

DirectionMode = str


@dataclass(frozen=True)
class RouteDef:
    origin: str
    destination: str
    label: str
    pair_route: str
    rank: int
    pair_weight: float
    directional_weight: float
    total_pax_12_months: int
    basket_version: str | None = None


def load_route_basket(
    path: str | Path | None = None,
    top_n: int | None = None,
    direction_mode: DirectionMode = "bidirectional",
) -> list[RouteDef]:
    """Load DGCA route pairs and expose directed collection routes.

    direction_mode:
    - bidirectional: return both A->B and B->A for every DGCA pair.
    - unidirectional: return only the DGCA-ranked A->B direction.
    - merged: return one pair-level route. Observations still preserve direction elsewhere.
    """
    source = Path(path) if path else DEFAULT_BASKET_FILE
    payload = json.loads(source.read_text(encoding="utf-8"))
    ranked = sorted(payload.get("routes", []), key=lambda row: row.get("rank", 10**9))
    if top_n is not None:
        ranked = ranked[:top_n]
    selected_pax_total = sum(float(row.get("total_pax_12_months") or 0.0) for row in ranked)

    mode = direction_mode.lower().strip()
    if mode not in {"bidirectional", "unidirectional", "merged"}:
        raise ValueError("direction_mode must be one of: bidirectional, unidirectional, merged")

    out: list[RouteDef] = []
    version = payload.get("generated_at")
    for row in ranked:
        origin = row["source"]["code"].upper()
        dest = row["destination"]["code"].upper()
        total_pax = int(row.get("total_pax_12_months") or 0)
        pair_weight = (float(total_pax) / selected_pax_total) if selected_pax_total else 0.0
        pair_route = row.get("route") or f"{origin}-{dest}"
        rank = int(row.get("rank") or 0)

        if mode == "bidirectional":
            directions = [(origin, dest), (dest, origin)]
            weight = pair_weight / 2.0
        else:
            directions = [(origin, dest)]
            weight = pair_weight

        for a, b in directions:
            out.append(RouteDef(
                origin=a,
                destination=b,
                label=f"{a} - {b}",
                pair_route=pair_route,
                rank=rank,
                pair_weight=pair_weight,
                directional_weight=weight,
                total_pax_12_months=total_pax,
                basket_version=version,
            ))
    return out


def load_basket_metadata(path: str | Path | None = None) -> dict:
    source = Path(path) if path else DEFAULT_BASKET_FILE
    payload = json.loads(source.read_text(encoding="utf-8"))
    return {
        "generated_at": payload.get("generated_at"),
        "window_start": payload.get("window_start"),
        "window_end": payload.get("window_end"),
        "months": payload.get("months", []),
        "weight_definition": payload.get("weight_definition"),
        "route_count": len(payload.get("routes", [])),
    }
