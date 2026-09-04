from __future__ import annotations

import argparse
import calendar
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "dgca"
RAW = DATA_ROOT / "raw"
PROCESSED = DATA_ROOT / "processed"
OUTPUT = DATA_ROOT / "output"
SNAPSHOTS = OUTPUT / "snapshots"
AIRPORTS_FILE = PROJECT_ROOT / "data" / "airports" / "IndiaAirports.json"
BASE = "https://public-prd-dgca.s3.ap-south-1.amazonaws.com/InventoryList/dataReports/aviationDataStatistics/airTransport/domestic/monthly/"
HEADERS = {"User-Agent": "Airfare-Price-Index-DGCA-route-pipeline/1.0"}
TIMEOUT = 60

log = logging.getLogger("airfare.dgca")


@dataclass(frozen=True)
class DgcaRunResult:
    generated: bool
    reason: str
    window_start: str | None
    window_end: str | None
    route_count: int
    output_path: Path | None


def last_complete_months(n: int = 12, today: date | None = None) -> list[date]:
    today = today or date.today()
    y, m = today.year, today.month - 1
    if m == 0:
        y, m = y - 1, 12
    months = []
    for _ in range(n):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(months))


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def filename_variants(d: date) -> list[str]:
    month = calendar.month_name[d.month].upper()
    year = d.year
    return [
        f"DOM CITYPAIR DATA, {month} {year}.xlsx",
        f"DOM CITYPAIR DATA,  {month} {year}.xlsx",
        f"DOM CITYPAIR DATA, {month}  {year}.xlsx",
        f"DOM CITYPAIR DATA,  {month}  {year}.xlsx",
    ]


def download_month(d: date, session: requests.Session, force: bool = False) -> tuple[Path, str]:
    RAW.mkdir(parents=True, exist_ok=True)
    key = month_key(d)
    out = RAW / f"{key}.xlsx"
    if force and out.exists():
        out.unlink()
    if out.exists() and out.stat().st_size > 1000:
        return out, "cached"

    last_status = None
    for filename in filename_variants(d):
        url = BASE + requests.utils.quote(filename, safe="")
        try:
            response = session.get(url, timeout=TIMEOUT)
            last_status = response.status_code
            if response.status_code == 200 and response.content[:2] == b"PK":
                out.write_bytes(response.content)
                return out, url
        except requests.RequestException as exc:
            log.warning("[%s] request failed: %s", key, exc)
        time.sleep(0.5)
    raise FileNotFoundError(f"DGCA file for {key} is not currently available; last HTTP status={last_status}")


def clean_city(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = re.sub(r"\s+", " ", str(value).strip())
    if text.lower().startswith("mumbai (") and text.endswith(")"):
        text = "Mumbai"
    return text


def city_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_city(value).casefold())


def load_airport_lookup() -> dict[str, dict[str, Any]]:
    data = json.loads(AIRPORTS_FILE.read_text(encoding="utf-8"))
    lookup: dict[str, dict[str, Any]] = {}
    for airport in data:
        if airport.get("countryCode") != "IN":
            continue
        lookup[city_key(airport.get("airportCity"))] = airport
        lookup[city_key(airport.get("airportCode"))] = airport
    return lookup


def read_dgca(path: Path, month: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=0, header=2, engine="openpyxl")
    raw = raw.iloc[:, :9].copy()
    expected = [
        "S.No.", "CITY 1", "CITY 2", "PASSENGERS TO CITY 2",
        "PASSENGERS FROM CITY 2", "FREIGHT TO CITY 2", "FREIGHT FROM CITY 2",
        "MAIL TO CITY 2", "MAIL FROM CITY 2",
    ]
    raw.columns = expected
    raw = raw[raw["CITY 1"].notna() & raw["CITY 2"].notna()].copy()
    for col in expected[3:]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").fillna(0)
    raw["CITY 1"] = raw["CITY 1"].map(clean_city)
    raw["CITY 2"] = raw["CITY 2"].map(clean_city)
    raw["month"] = month
    raw["source_file"] = path.name
    raw["pax_combined"] = raw["PASSENGERS TO CITY 2"] + raw["PASSENGERS FROM CITY 2"]
    return raw


def build_routes(frames: list[pd.DataFrame], airport_lookup: dict[str, dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    all_rows = pd.concat(frames, ignore_index=True)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in all_rows.iterrows():
        c1, c2 = row["CITY 1"], row["CITY 2"]
        k1, k2 = city_key(c1), city_key(c2)
        key, city_a, city_b = ((k1, k2), c1, c2) if k1 <= k2 else ((k2, k1), c2, c1)
        rec = grouped.setdefault(key, {
            "city_1": city_a,
            "city_2": city_b,
            "monthly_pax": {},
            "total_pax_12_months": 0.0,
        })
        rec["monthly_pax"][row["month"]] = rec["monthly_pax"].get(row["month"], 0.0) + float(row["pax_combined"])
        rec["total_pax_12_months"] += float(row["pax_combined"])

    eligible = []
    for rec in grouped.values():
        source = airport_lookup.get(city_key(rec["city_1"]))
        destination = airport_lookup.get(city_key(rec["city_2"]))
        if not source or not destination or source["airportCode"] == destination["airportCode"]:
            continue
        rec["source"] = {"city": source["airportCity"], "code": source["airportCode"]}
        rec["destination"] = {"city": destination["airportCity"], "code": destination["airportCode"]}
        rec["route"] = f"{source['airportCode']}-{destination['airportCode']}"
        eligible.append(rec)

    eligible.sort(key=lambda row: row["total_pax_12_months"], reverse=True)
    selected = eligible[:top_n]
    selected_total = sum(row["total_pax_12_months"] for row in selected)

    routes = []
    for rank, rec in enumerate(selected, start=1):
        weight = rec["total_pax_12_months"] / selected_total if selected_total else 0.0
        routes.append({
            "rank": rank,
            "route": rec["route"],
            "source": rec["source"],
            "destination": rec["destination"],
            "total_pax_12_months": int(round(rec["total_pax_12_months"])),
            "average_monthly_pax": round(rec["total_pax_12_months"] / len(frames), 1),
            "pax_weight": weight,
            "pax_weight_pct": round(weight * 100, 4),
            "months_available": len(rec["monthly_pax"]),
            "monthly_pax": [
                {"month": month, "pax": int(round(rec["monthly_pax"][month]))}
                for month in sorted(rec["monthly_pax"])
            ],
        })
    return routes


def write_outputs(routes: list[dict[str, Any]], months: list[date], top_n: int, direction_mode: str) -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone().isoformat()
    payload = {
        "generated_at": generated_at,
        "window_start": month_key(months[0]),
        "window_end": month_key(months[-1]),
        "months": [month_key(m) for m in months],
        "top_n": top_n,
        "direction_mode_default": direction_mode,
        "visibility_policy": (
            "Route-basket snapshots are versioned. Existing fare observations keep their original "
            "basket version; a new Top-N/Top-50 affects future collection and newly computed index "
            "versions, not raw historical observations."
        ),
        "weight_definition": (
            "pax_weight = undirected route-pair 12-month passenger traffic / combined passenger "
            "traffic of selected route pairs. For bidirectional collection, each direction receives "
            "half the pair weight while raw airfare observations preserve direction."
        ),
        "routes": routes,
    }
    latest = OUTPUT / "top50.json"
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_rows = [
        {
            "rank": r["rank"],
            "route": r["route"],
            "source_city": r["source"]["city"],
            "source_code": r["source"]["code"],
            "destination_city": r["destination"]["city"],
            "destination_code": r["destination"]["code"],
            "total_pax_12_months": r["total_pax_12_months"],
            "average_monthly_pax": r["average_monthly_pax"],
            "pax_weight": r["pax_weight"],
            "pax_weight_pct": r["pax_weight_pct"],
            "months_available": r["months_available"],
        }
        for r in routes
    ]
    pd.DataFrame(csv_rows).to_csv(OUTPUT / "top50.csv", index=False)

    stamp = generated_at.replace(":", "").replace("-", "").split(".")[0]
    snapshot = SNAPSHOTS / f"top_routes_{month_key(months[0])}_{month_key(months[-1])}_{stamp}.json"
    shutil.copyfile(latest, snapshot)
    shutil.copyfile(latest, OUTPUT / "latest_top_routes.json")
    return latest


def current_output_window(path: Path = OUTPUT / "top50.json") -> str | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("window_end")
    except json.JSONDecodeError:
        return None


def run(
    top_n: int = 50,
    direction_mode: str = "bidirectional",
    force: bool = False,
    today: date | None = None,
    skip_if_current: bool = True,
) -> DgcaRunResult:
    months = last_complete_months(12, today)
    target_end = month_key(months[-1])
    existing_end = current_output_window()
    if skip_if_current and not force and existing_end == target_end:
        return DgcaRunResult(False, f"DGCA basket already current through {target_end}", month_key(months[0]), target_end, 0, OUTPUT / "top50.json")

    session = requests.Session()
    session.headers.update(HEADERS)
    paths: list[Path] = []
    for month in months:
        try:
            path, _source = download_month(month, session, force=force)
        except FileNotFoundError as exc:
            existing = OUTPUT / "top50.json"
            if existing.exists():
                return DgcaRunResult(
                    False,
                    f"{exc}; keeping existing basket through {existing_end or current_output_window(existing)}",
                    None,
                    existing_end or current_output_window(existing),
                    0,
                    existing,
                )
            raise
        paths.append(path)

    frames = [read_dgca(path, month_key(month)) for path, month in zip(paths, months)]
    PROCESSED.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).to_csv(PROCESSED / "combined_12_months.csv", index=False)
    routes = build_routes(frames, load_airport_lookup(), top_n=top_n)
    out = write_outputs(routes, months, top_n=top_n, direction_mode=direction_mode)
    return DgcaRunResult(True, "DGCA basket generated", month_key(months[0]), target_end, len(routes), out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DGCA route basket for the Airfare Price Index.")
    parser.add_argument("--top-n", type=int, default=50, help="Number of DGCA-ranked route pairs to keep.")
    parser.add_argument("--direction-mode", choices=["unidirectional", "bidirectional", "merged"], default="bidirectional")
    parser.add_argument("--force", action="store_true", help="Redownload/recompute even if the latest complete month is already processed.")
    parser.add_argument("--no-skip-current", action="store_true", help="Recompute even when the current latest complete month is already available.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    result = run(args.top_n, args.direction_mode, args.force, skip_if_current=not args.no_skip_current)
    log.info("%s; window=%s..%s routes=%s output=%s", result.reason, result.window_start, result.window_end, result.route_count, result.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
