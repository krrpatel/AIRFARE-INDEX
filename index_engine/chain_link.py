"""
Chain-linking: splices a 'live' index series onto a 'simulated_backcast'
series at a given date, per docs/METHODOLOGY.md §6.

The problem this solves: the live series starts computing its own index
from a scale of 100 on day 1 (whatever the live source's first-day average
happens to be). Left alone, that would create a visible, meaningless jump
in the chart at the splice date. Chain-linking rescales the live series so
its value ON the splice date exactly matches the backcast series' value on
that same date -- the standard technique statistical agencies use whenever
a data source changes mid-series.

Pure function, no I/O -- same design as the rest of index_engine/, so it's
independently testable and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkedIndexPoint:
    date: str
    index_value: float
    data_mode: str  # 'simulated_backcast' | 'live'


def chain_link(
    backcast_series: list[tuple[str, float]],
    live_series: list[tuple[str, float]],
) -> list[LinkedIndexPoint]:
    """
    backcast_series: [(date, index_value), ...] sorted ascending, ending at
        (or before) the splice date.
    live_series: [(date, raw_index_value), ...] sorted ascending, where the
        first entry is the splice date and raw_index_value is on the live
        series' own, unlinked scale (its own day-1 = 100 basis).

    Returns a single continuous series: backcast points unchanged, followed
    by live points rescaled by the link factor
        link_factor = backcast_value_at_splice_date / live_value_at_splice_date
    so the live series continues seamlessly from where the backcast left off.

    Raises ValueError if either series is empty, or if the live series'
    first date isn't found in the backcast series (nothing to link against).
    """
    if not backcast_series:
        raise ValueError("backcast_series is empty -- nothing to link from.")
    if not live_series:
        raise ValueError("live_series is empty -- nothing to link.")

    splice_date, live_splice_value = live_series[0]
    backcast_lookup = dict(backcast_series)

    if splice_date not in backcast_lookup:
        raise ValueError(
            f"Splice date {splice_date} not found in backcast_series -- "
            "the live series must start on a date the backcast series covers, "
            "so the two can be linked at a shared point."
        )
    if live_splice_value == 0:
        raise ValueError("live_series' first value is zero -- cannot compute a link factor.")

    backcast_splice_value = backcast_lookup[splice_date]
    link_factor = backcast_splice_value / live_splice_value

    out = [
        LinkedIndexPoint(date=d, index_value=round(v, 4), data_mode="simulated_backcast")
        for d, v in backcast_series
    ]
    out += [
        LinkedIndexPoint(date=d, index_value=round(v * link_factor, 4), data_mode="live")
        for d, v in live_series[1:]  # splice date itself already represented by the backcast point
    ]
    return out
