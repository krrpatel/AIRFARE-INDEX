"""
Alert evaluation. Pure functions (no I/O) so they're independently
testable, per the same design principle as index_engine/index.py.
Persistence of the alerts these produce happens in the caller
(scripts/run_pipeline_demo.py), via database/sqlite_store.insert_alert.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_NATIONAL_MOVE_THRESHOLD_PCT = 5.0   # national index day-over-day
DEFAULT_ROUTE_MOVE_THRESHOLD_PCT = 15.0     # a single route, day-over-day
DEFAULT_ANOMALY_RATE_THRESHOLD_PCT = 8.0    # share of a day's observations flagged anomalous


@dataclass(frozen=True)
class Alert:
    alert_type: str
    message: str
    threshold_pct: float
    actual_pct: float
    severity: str = "warning"
    route_key: tuple[str, str] | None = None


def check_national_index_move(current: float, previous: float | None,
                               threshold_pct: float = DEFAULT_NATIONAL_MOVE_THRESHOLD_PCT) -> Alert | None:
    if previous is None or previous == 0:
        return None
    pct = 100.0 * (current - previous) / previous
    if abs(pct) >= threshold_pct:
        direction = "increased" if pct > 0 else "decreased"
        return Alert(
            alert_type="national_index_move",
            message=f"National index {direction} {abs(pct):.2f}% day-over-day (threshold {threshold_pct}%).",
            threshold_pct=threshold_pct, actual_pct=round(pct, 3),
            severity="critical" if abs(pct) >= threshold_pct * 2 else "warning",
        )
    return None


def check_route_index_move(route_key: tuple[str, str], current: float, previous: float | None,
                            threshold_pct: float = DEFAULT_ROUTE_MOVE_THRESHOLD_PCT) -> Alert | None:
    if previous is None or previous == 0:
        return None
    pct = 100.0 * (current - previous) / previous
    if abs(pct) >= threshold_pct:
        direction = "increased" if pct > 0 else "decreased"
        return Alert(
            alert_type="route_index_move",
            message=f"{route_key[0]}-{route_key[1]} index {direction} {abs(pct):.2f}% day-over-day "
                    f"(threshold {threshold_pct}%).",
            threshold_pct=threshold_pct, actual_pct=round(pct, 3), route_key=route_key,
            severity="critical" if abs(pct) >= threshold_pct * 2 else "warning",
        )
    return None


def check_anomaly_rate(anomaly_count: int, total_count: int,
                        threshold_pct: float = DEFAULT_ANOMALY_RATE_THRESHOLD_PCT) -> Alert | None:
    if total_count == 0:
        return None
    rate = 100.0 * anomaly_count / total_count
    if rate >= threshold_pct:
        return Alert(
            alert_type="anomaly_rate",
            message=f"Anomaly rate {rate:.2f}% of observations today (threshold {threshold_pct}%) -- "
                    f"possible source or scraping issue.",
            threshold_pct=threshold_pct, actual_pct=round(rate, 3), severity="warning",
        )
    return None
