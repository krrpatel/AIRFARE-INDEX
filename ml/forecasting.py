"""
Fare forecasting. Time-aware split only (no random shuffling — that would
leak future prices into training, per METHODOLOGY.md and the project's own
constraint on avoiding random splits for time series).

Baseline: Linear Regression. Compared against RandomForest. Model choice
is decided by validation MAE, not assumed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, mean_squared_error


FEATURES = ["days_to_departure", "stops", "dow", "month", "is_holiday_window"]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["travel_date"] = pd.to_datetime(out["travel_date"])
    out["dow"] = out["travel_date"].dt.dayofweek
    out["month"] = out["travel_date"].dt.month
    out["is_holiday_window"] = 0  # placeholder; wired to holidays table in Phase 9
    return out


def time_aware_split(df: pd.DataFrame, val_frac: float = 0.2):
    """Split by travel_date order, not randomly — train on the earlier
    portion, validate on the later portion, matching how forecasting will
    actually be used (predict the future from the past)."""
    df_sorted = df.sort_values("travel_date")
    cutoff = int(len(df_sorted) * (1 - val_frac))
    return df_sorted.iloc[:cutoff], df_sorted.iloc[cutoff:]


def evaluate_model(model, X_train, y_train, X_val, y_val) -> dict:
    model.fit(X_train, y_train)
    preds = model.predict(X_val)
    return {
        "mae": mean_absolute_error(y_val, preds),
        "rmse": mean_squared_error(y_val, preds) ** 0.5,
        "mape": mean_absolute_percentage_error(y_val, preds),
    }


def train_and_compare(route_df: pd.DataFrame) -> dict:
    """Fits Linear Regression and Random Forest for one route's history,
    returns metrics for both plus which one wins on validation MAE."""
    df = build_features(route_df)
    train, val = time_aware_split(df)
    X_train, y_train = train[FEATURES], train["total_fare"]
    X_val, y_val = val[FEATURES], val["total_fare"]

    results = {
        "linear_regression": evaluate_model(LinearRegression(), X_train, y_train, X_val, y_val),
        "random_forest": evaluate_model(
            RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42),
            X_train, y_train, X_val, y_val,
        ),
    }
    best = min(results, key=lambda k: results[k]["mae"])
    return {"metrics": results, "best_model": best}


def predict_with_interval(model, X_val: pd.DataFrame, train_residual_std: float, z: float = 1.28):
    """~80% prediction interval using residual std from training (simple,
    transparent — not a full quantile-regression approach, stated as a
    known simplification in LIMITATIONS.md)."""
    point = model.predict(X_val)
    return point - z * train_residual_std, point, point + z * train_residual_std
