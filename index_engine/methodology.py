"""
Shared constants encoding the decisions documented in docs/METHODOLOGY.md.
Nothing statistically meaningful should be a magic number anywhere else in
the codebase — it belongs here so the methodology stays auditable in one
place.
"""
from datetime import date

BASE_PERIOD = date(2026, 1, 1)  # Jan 2026 = 100, per docs/METHODOLOGY.md §6

DEPARTURE_BUCKETS: list[tuple[str, int, int]] = [
    # (bucket_code, min_days_inclusive, max_days_inclusive)
    ("B1", 0, 3),
    ("B2", 4, 7),
    ("B3", 8, 14),
    ("B4", 15, 30),
    ("B5", 31, 60),
    ("B6", 61, 10_000),
]

MIN_WEEKLY_OBSERVATIONS_FOR_ROUTE = 5  # below this, route excluded that week (§7)
MAX_IMPUTATION_DAYS = 3                # carry-forward cap before DQ warning (§7)
IQR_OUTLIER_MULTIPLIER = 1.5           # standard Tukey fence (§8)


def days_to_departure_bucket(days_to_departure: int) -> str:
    """Map a days-to-departure integer to its elementary-aggregate bucket."""
    for code, lo, hi in DEPARTURE_BUCKETS:
        if lo <= days_to_departure <= hi:
            return code
    raise ValueError(f"days_to_departure={days_to_departure} matched no bucket")
