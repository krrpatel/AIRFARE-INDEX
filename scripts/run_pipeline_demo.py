"""
Full end-to-end run: generates mock data, runs it through the real
validation/outlier pipeline, computes the national + route indices for
every day, runs anomaly detection, and persists everything into a real
SQLite database that backend/app/main.py can serve from directly.

This is the thing to run to prove (and to demo) that the system actually
works end-to-end without requiring Docker/Postgres/network access:

    PYTHONPATH=. python scripts/run_pipeline_demo.py --start 2026-01-01 --end 2026-03-31

Then inspect the resulting database/airfare_demo.db, or point
DATABASE_URL=sqlite:///database/airfare_demo.db at the FastAPI app.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, ".")

from data_pipeline.pipeline import (
    apply_outlier_flags, compute_daily_route_bucket_prices,
    compute_national_index_for_day, route_bucket_prices_to_dict, validate_dataframe,
)
from database.sqlite_store import (
    get_conn, init_db, insert_alert, insert_fare_observations, record_revision,
    upsert_airline_index_value, upsert_index_value, upsert_route, upsert_route_index_value,
)
from index_engine.alerts import check_anomaly_rate, check_national_index_move, check_route_index_move
from index_engine.index import pct_change
from index_engine.price_relative import route_price_relative
from index_engine.weights import compute_route_weights
from ml.anomaly_detection import detect_anomalies
from scripts.generate_mock_data import generate


def run(start: date, end: date, seed: int, db_path: str) -> None:
    print(f"[1/8] Generating deterministic mock data {start} -> {end} (seed={seed})...")
    df = generate(start, end, seed)
    df["booking_date"] = pd.to_datetime(df["booking_ts"]).dt.date.astype(str)
    print(f"       {len(df):,} raw observations generated.")

    print("[2/8] Running tier-1 validation + tier-2 outlier flagging...")
    df = apply_outlier_flags(validate_dataframe(df))
    print(f"       quality_status counts: {df['quality_status'].value_counts().to_dict()}")

    print("[3/8] Running anomaly detection (Isolation Forest, per-route)...")
    anomaly_frames = []
    for (o, d_), group in df.groupby(["origin", "destination"]):
        anomaly_frames.append(detect_anomalies(group))
    df = pd.concat(anomaly_frames).sort_index()
    flagged = int(df["anomaly_flag"].sum())
    print(f"       {flagged:,} observations flagged anomalous (of {len(df):,}).")

    print("[4/8] Initializing SQLite database and route/weight tables...")
    init_db(db_path)
    weights = compute_route_weights()
    with get_conn(db_path) as conn:
        route_id_map = {}
        for rw in weights:
            rid = upsert_route(conn, rw.route.origin, rw.route.destination, rw.route.label, rw.weight)
            route_id_map[(rw.route.origin, rw.route.destination)] = rid

    print("[5/8] Computing national + route index for every day and persisting...")
    base_date = df["booking_date"].min()
    base_grouped = compute_daily_route_bucket_prices(df, base_date)
    base_prices = route_bucket_prices_to_dict(base_grouped)
    weight_dict = {(w.route.origin, w.route.destination): w.weight for w in weights}

    dates = sorted(df["booking_date"].unique())
    national_series: dict[str, float] = {}

    with get_conn(db_path) as conn:
        # persist raw observations once (kept lean: only needed columns)
        obs_rows = [
            (route_id_map[(r.origin, r.destination)], r.source, r.airline_code,
             r.travel_date, r.booking_date, r.departure_bucket, float(r.total_fare),
             r.quality_status, r.data_mode)
            for r in df.itertuples(index=False)
            if (r.origin, r.destination) in route_id_map
        ]
        insert_fare_observations(conn, obs_rows)

        for anomaly_row in df[df["anomaly_flag"]].itertuples(index=False):
            key = (anomaly_row.origin, anomaly_row.destination)
            if key in route_id_map:
                conn.execute(
                    "INSERT INTO anomalies (route_id, booking_date, method, description) VALUES (?,?,?,?)",
                    (route_id_map[key], anomaly_row.booking_date, "iqr_or_isoforest",
                     f"total_fare={anomaly_row.total_fare}")
                )

        for d in dates:
            today_grouped = compute_daily_route_bucket_prices(df, d)
            today_prices = route_bucket_prices_to_dict(today_grouped)
            obs_counts = (
                df[(df["booking_date"] == d) & (df["quality_status"] == "valid")]
                .groupby(["origin", "destination"]).size().to_dict()
            )

            route_results = []
            for route_key, weight in weight_dict.items():
                relative = route_price_relative(today_prices.get(route_key, {}), base_prices.get(route_key, {}))
                route_id = route_id_map[route_key]
                avg_fare = None
                if route_key in today_prices and today_prices[route_key]:
                    avg_fare = sum(today_prices[route_key].values()) / len(today_prices[route_key])
                upsert_route_index_value(
                    conn, route_id, d,
                    (relative * 100.0) if relative is not None else None,
                    avg_fare, obs_counts.get(route_key, 0),
                )
                from index_engine.index import RouteDayResult
                route_results.append(RouteDayResult(
                    route_id=route_id, price_relative=relative, weight=weight,
                    observation_count=obs_counts.get(route_key, 0),
                ))

            try:
                from index_engine.index import compute_national_index
                result = compute_national_index(route_results)
                national_series[d] = result.index_value
                mom = pct_change(result.index_value, national_series.get(_shift_date(d, -30)))
                wow = pct_change(result.index_value, national_series.get(_shift_date(d, -7)))
                upsert_index_value(conn, d, result.index_value, "simulated_backcast",
                                    mom, wow, result.routes_included, result.routes_excluded)

                # --- alerts: national move, route moves, anomaly rate ---
                prev_val = national_series.get(_shift_date(d, -1))
                nat_alert = check_national_index_move(result.index_value, prev_val)
                if nat_alert:
                    insert_alert(conn, nat_alert.alert_type, d, nat_alert.message,
                                 nat_alert.threshold_pct, nat_alert.actual_pct, severity=nat_alert.severity)

                day_total = len(df[df["booking_date"] == d])
                day_anom = int(df[(df["booking_date"] == d) & (df["anomaly_flag"])].shape[0])
                rate_alert = check_anomaly_rate(day_anom, day_total)
                if rate_alert:
                    insert_alert(conn, rate_alert.alert_type, d, rate_alert.message,
                                 rate_alert.threshold_pct, rate_alert.actual_pct, severity=rate_alert.severity)
            except ValueError:
                pass  # no usable data that day -- correctly skipped, not faked

        # per-route move alerts, using the just-persisted route_index_values
        prev_route_values: dict[tuple[str, str], float] = {}
        for d in dates:
            for route_key, route_id in route_id_map.items():
                row = conn.execute(
                    "SELECT index_value FROM route_index_values WHERE route_id=? AND index_date=?",
                    (route_id, d),
                ).fetchone()
                current_val = row["index_value"] if row else None
                prev_val = prev_route_values.get(route_key)
                if current_val is not None:
                    route_alert = check_route_index_move(route_key, current_val, prev_val)
                    if route_alert:
                        insert_alert(conn, route_alert.alert_type, d, route_alert.message,
                                     route_alert.threshold_pct, route_alert.actual_pct,
                                     route_id=route_id, severity=route_alert.severity)
                    prev_route_values[route_key] = current_val

        conn.execute("INSERT OR REPLACE INTO sources (name, status, last_success) VALUES (?,?,?)",
                     ("mock", "ONLINE", dates[-1]))

    print(f"       {len(national_series)} days of national index computed and stored.")

    print("[6/8] Computing per-airline index...")
    airline_dfs = []
    for code, group in df[df["quality_status"] == "valid"].groupby("airline_code"):
        base_avg = group[group["booking_date"] == base_date]["total_fare"].mean()
        with get_conn(db_path) as conn:
            for d in dates:
                day_group = group[group["booking_date"] == d]
                if len(day_group) == 0:
                    continue
                avg_fare = day_group["total_fare"].mean()
                idx = (avg_fare / base_avg * 100.0) if base_avg and base_avg > 0 else None
                upsert_airline_index_value(conn, code, d, round(avg_fare, 2),
                                            round(idx, 4) if idx else None, len(day_group))
        airline_dfs.append(code)
    print(f"       Computed index for {len(airline_dfs)} airlines.")

    print("[7/8] Simulating one late-arriving revision (demonstrates revision policy)...")
    with get_conn(db_path) as conn:
        mid_date = dates[len(dates) // 2]
        row = conn.execute("SELECT index_value FROM index_values WHERE index_date=?", (mid_date,)).fetchone()
        if row:
            old_val = row["index_value"]
            new_val = round(old_val * 1.004, 4)  # simulated small correction from late data
            conn.execute("UPDATE index_values SET index_value=? WHERE index_date=?", (new_val, mid_date))
            record_revision(conn, mid_date, old_val, new_val,
                             "Simulated late-arriving observation triggered recomputation (demo only).")
    print(f"       Revision recorded for {mid_date}.")

    print(f"[8/8] Done. Database written to: {db_path}")
    print()
    last_date = dates[-1]
    print(f"Latest national index ({last_date}): {national_series.get(last_date)}")


def _shift_date(d: str, days: int) -> str:
    return (pd.Timestamp(d) + pd.Timedelta(days=days)).date().isoformat()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2026, 1, 1))
    ap.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2026, 3, 31))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--db", type=str, default="database/airfare_demo.db")
    args = ap.parse_args()
    run(args.start, args.end, args.seed, args.db)
