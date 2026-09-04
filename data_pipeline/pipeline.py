"""
End-to-end pipeline orchestration, DB-independent (pure pandas/numpy).

This is deliberately written so it can run and be verified without a live
Postgres connection -- the DB loader (backend/app/db/*) is a thin
persistence layer on top of this; the statistical correctness lives here
and is what this module's own test proves.

Flow: raw observations -> validation -> outlier flagging -> per-day
route/bucket representative prices -> route price relatives vs base period
-> national index (via index_engine).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from data_pipeline.outliers import classify_quality_status
from data_pipeline.validation import RawObservation, validate_observation
from index_engine.index import RouteDayResult, compute_national_index, pct_change
from index_engine.methodology import BASE_PERIOD
from index_engine.weights import weights_as_dict


def validate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Applies tier-1 validation row-by-row, adds `quality_status` column
    ('valid' or 'rejected') and `reject_reasons`."""
    statuses = []
    reasons_list = []
    for row in df.itertuples(index=False):
        obs = RawObservation(
            origin=row.origin,
            destination=row.destination,
            travel_date=date.fromisoformat(row.travel_date) if isinstance(row.travel_date, str) else row.travel_date,
            booking_ts=pd.Timestamp(row.booking_ts).to_pydatetime(),
            total_fare=float(row.total_fare),
            base_fare=float(row.base_fare),
            taxes=float(row.taxes),
            duration_minutes=None,
            currency=row.currency,
        )
        result = validate_observation(obs)
        statuses.append("valid" if result.is_valid else "rejected")
        reasons_list.append(",".join(result.reasons))

    out = df.copy()
    out["quality_status"] = statuses
    out["reject_reasons"] = reasons_list
    return out


def apply_outlier_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Runs tier-2 IQR flagging on rows that passed tier-1 validation, and
    demotes IQR-flagged rows from 'valid' to 'suspicious' (never deleted)."""
    valid_mask = df["quality_status"] == "valid"
    valid_df = df[valid_mask].copy()

    if len(valid_df) == 0:
        df["is_iqr_outlier"] = False
        return df

    flagged = classify_quality_status(
        valid_df, group_cols=["origin", "destination", "departure_bucket"]
    )
    df = df.copy()
    df["is_iqr_outlier"] = False
    df.loc[flagged.index, "is_iqr_outlier"] = flagged["is_iqr_outlier"].values
    df.loc[flagged.index[flagged["is_iqr_outlier"]], "quality_status"] = "suspicious"
    return df


def compute_daily_route_bucket_prices(df: pd.DataFrame, on_date: str) -> pd.DataFrame:
    """Median total_fare per (route, departure_bucket) for observations
    booked on `on_date`, using only quality_status == 'valid' rows
    (suspicious rows are excluded from the representative price, per
    METHODOLOGY.md section 8)."""
    day_df = df[(df["booking_date"] == on_date) & (df["quality_status"] == "valid")]
    grouped = (
        day_df.groupby(["origin", "destination", "departure_bucket"])["total_fare"]
        .median()
        .reset_index()
    )
    return grouped


def route_bucket_prices_to_dict(grouped: pd.DataFrame) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = {}
    for row in grouped.itertuples(index=False):
        key = (row.origin, row.destination)
        out.setdefault(key, {})[row.departure_bucket] = row.total_fare
    return out


def compute_national_index_for_day(
    df: pd.DataFrame,
    on_date: str,
    base_prices_by_route: dict[tuple[str, str], dict[str, float]],
    weights: dict[tuple[str, str], float],
) -> tuple[float, int, int]:
    """Full day computation: bucket prices -> route price relatives (geometric
    mean, see index_engine.price_relative) -> national index (index_engine.index).
    Returns (index_value, routes_included, routes_excluded)."""
    from index_engine.price_relative import route_price_relative

    today_grouped = compute_daily_route_bucket_prices(df, on_date)
    today_prices = route_bucket_prices_to_dict(today_grouped)

    obs_counts = (
        df[(df["booking_date"] == on_date) & (df["quality_status"] == "valid")]
        .groupby(["origin", "destination"]).size().to_dict()
    )

    route_results = []
    for route_key, weight in weights.items():
        current_bucket_prices = today_prices.get(route_key, {})
        base_bucket_prices = base_prices_by_route.get(route_key, {})
        relative = route_price_relative(current_bucket_prices, base_bucket_prices)
        count = obs_counts.get(route_key, 0)
        route_results.append(RouteDayResult(
            route_id=hash(route_key),  # placeholder id for this DB-free path
            price_relative=relative,
            weight=weight,
            observation_count=count,
        ))

    result = compute_national_index(route_results)
    return result.index_value, result.routes_included, result.routes_excluded
