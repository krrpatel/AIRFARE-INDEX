from __future__ import annotations

import argparse
import sys
from collections import Counter

sys.path.insert(0, ".")

from airfare.sources.compareflights.offline_adapter import CompareFlightsOfflineAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect imported CompareFlights normalized records.")
    parser.add_argument("--run-date", default="2026-08-26")
    args = parser.parse_args()
    adapter = CompareFlightsOfflineAdapter(run_date=args.run_date)
    records = adapter.iter_all_records()
    by_status = Counter(record.availability_status for record in records)
    by_route = Counter((record.origin, record.destination) for record in records)
    print(f"records={len(records)}")
    print(f"status={dict(by_status)}")
    print(f"routes={dict(by_route)}")
    print("components: base_fare/taxes are NULL unless a source exposes them; CompareFlights currently provides offer totals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

