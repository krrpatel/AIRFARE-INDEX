"""
Deterministic mock airfare observation generator.

Simulates realistic fare behavior so `MODE=mock` gives a demo that is
statistically indistinguishable in *shape* from live data:
  - base fare that scales with a route "distance tier"
  - booking-curve effect: price rises as days_to_departure shrinks
  - day-of-week effect (Fri/Sun pricier)
  - holiday multiplier
  - per-airline pricing offset (some airlines run cheaper/pricier)
  - random noise + occasional promotions + occasional injected anomalies
    (so the anomaly-detection demo has something real to find)

Deterministic: same --seed always produces the same dataset, so index
values computed from it are reproducible run-to-run (docs/METHODOLOGY.md
requires this).

Usage:
    python scripts/generate_mock_data.py --start 2026-01-01 --end 2026-08-24 \
        --seed 42 --out data/mock_fares.csv
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import date, datetime, timedelta

import pandas as pd

from index_engine.basket import PROTOTYPE_BASKET
from index_engine.methodology import days_to_departure_bucket
from airfare.airports.registry import load_airports

AIRLINES = [
    ("6E", "IndiGo", 1.00),
    ("AI", "Air India", 1.08),
    ("QP", "Akasa Air", 0.96),
    ("SG", "SpiceJet", 0.93),
    ("IX", "Air India Express", 0.90),
]

# Rough relative "distance tier" -> base fare anchor (INR), ASSUMPTION for demo realism only
_DISTANCE_TIER = {
    ("DEL", "BOM"): 4800, ("BOM", "DEL"): 4800,
    ("DEL", "BLR"): 5200, ("BLR", "DEL"): 5200,
    ("DEL", "HYD"): 4600, ("DEL", "CCU"): 4200, ("DEL", "MAA"): 5400,
    ("BOM", "BLR"): 3600, ("BOM", "HYD"): 3400, ("BOM", "MAA"): 4800, ("BOM", "CCU"): 5600,
    ("AMD", "DEL"): 3900, ("AMD", "BOM"): 2600,
    ("BLR", "HYD"): 3200, ("BLR", "MAA"): 2600, ("BLR", "CCU"): 5800,
    ("PNQ", "DEL"): 4400, ("PNQ", "BLR"): 3400,
    ("JAI", "DEL"): 2600, ("JAI", "BOM"): 3800,
    ("GOI", "BOM"): 2400, ("GOI", "DEL"): 4600,
    ("COK", "BOM"): 4600, ("COK", "DEL"): 5800,
    ("LKO", "DEL"): 3200, ("PAT", "DEL"): 3600, ("GAU", "CCU"): 3400,
}


def route_base_anchor(origin: str, destination: str) -> int:
    if (origin, destination) in _DISTANCE_TIER:
        return _DISTANCE_TIER[(origin, destination)]
    airports = load_airports()
    a, b = airports.get(origin), airports.get(destination)
    if not a or not b or a.latitude is None or a.longitude is None or b.latitude is None or b.longitude is None:
        return 4200
    km = _haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
    return int(max(2200, min(7500, 1800 + km * 2.25)))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# A handful of 2026 Indian holidays affecting travel demand (ASSUMPTION: illustrative set)
HOLIDAYS_2026 = {
    date(2026, 1, 14): "Makar Sankranti", date(2026, 1, 26): "Republic Day",
    date(2026, 3, 4): "Holi", date(2026, 3, 21): "Eid al-Fitr (approx.)",
    date(2026, 8, 15): "Independence Day", date(2026, 10, 2): "Gandhi Jayanti",
    date(2026, 10, 20): "Diwali (approx.)", date(2026, 11, 24): "Guru Nanak Jayanti",
    date(2026, 12, 25): "Christmas",
}


def booking_curve_multiplier(days_to_departure: int) -> float:
    """Prices rise sharply in the final week, plateau further out."""
    if days_to_departure <= 1:
        return 2.3
    if days_to_departure <= 3:
        return 1.8
    if days_to_departure <= 7:
        return 1.4
    if days_to_departure <= 14:
        return 1.15
    if days_to_departure <= 30:
        return 1.0
    return 0.88


def holiday_multiplier(travel_dt: date) -> float:
    for h in HOLIDAYS_2026:
        if abs((travel_dt - h).days) <= 2:
            return 1.35
    return 1.0


def weekday_multiplier(travel_dt: date) -> float:
    # Mon=0..Sun=6; Fri/Sun travel is pricier
    return 1.12 if travel_dt.weekday() in (4, 6) else 1.0


def generate(start: date, end: date, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []

    day_cursor = start
    while day_cursor <= end:
        for route in PROTOTYPE_BASKET:
            base_anchor = route_base_anchor(route.origin, route.destination)
            for days_out in (1, 3, 5, 10, 20, 45, 75):
                travel_dt = day_cursor + timedelta(days=days_out)
                bucket = days_to_departure_bucket(days_out)

                for code, name, airline_factor in AIRLINES:
                    # skip some airline/route combos randomly for realism (not every airline flies every route)
                    if rng.random() < 0.15:
                        continue

                    mult = (
                        booking_curve_multiplier(days_out)
                        * holiday_multiplier(travel_dt)
                        * weekday_multiplier(travel_dt)
                        * airline_factor
                    )
                    noise = rng.gauss(1.0, 0.06)
                    fare = base_anchor * mult * noise

                    # occasional promotion (rare, legitimate low fare)
                    if rng.random() < 0.02:
                        fare *= rng.uniform(0.45, 0.65)

                    # occasional injected anomaly (for anomaly-detection demo)
                    is_injected_anomaly = rng.random() < 0.005
                    if is_injected_anomaly:
                        fare *= rng.choice([0.15, 4.5])  # absurdly low or high

                    base_fare = round(fare * 0.85, 2)
                    taxes = round(fare * 0.15, 2)
                    total_fare = round(base_fare + taxes, 2)

                    rows.append({
                        "source": rng.choice(["airline_direct", "makemytrip", "cleartrip", "ixigo"]),
                        "airline_code": code,
                        "airline_name": name,
                        "origin": route.origin,
                        "destination": route.destination,
                        "travel_date": travel_dt.isoformat(),
                        "booking_ts": datetime.combine(day_cursor, datetime.min.time()).isoformat(),
                        "days_to_departure": days_out,
                        "departure_bucket": bucket,
                        "base_fare": base_fare,
                        "taxes": taxes,
                        "total_fare": total_fare,
                        "currency": "INR",
                        "cabin_class": "Economy",
                        "stops": 0 if rng.random() > 0.2 else 1,
                        "data_mode": "simulated_backcast",
                        "_injected_anomaly": is_injected_anomaly,  # ground truth, for evaluating anomaly detection
                    })
        day_cursor += timedelta(days=1)

    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2026, 1, 1))
    ap.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2026, 8, 24))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=str, default="data/mock_fares.csv")
    args = ap.parse_args()

    df = generate(args.start, args.end, args.seed)
    df.to_csv(args.out, index=False)
    print(f"Generated {len(df):,} mock observations -> {args.out}")
    print(f"Injected anomalies: {df['_injected_anomaly'].sum():,} "
          f"({100 * df['_injected_anomaly'].mean():.2f}%)")
