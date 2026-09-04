"""
Tier-2 outlier flagging: statistically suspicious, NOT deleted.
See docs/METHODOLOGY.md §8.

Operates per (route, departure_bucket) cell so a normal price difference
between e.g. a 1-hour regional hop and a trunk metro route never gets
mistaken for an anomaly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from index_engine.methodology import IQR_OUTLIER_MULTIPLIER


def flag_iqr_outliers(fares: pd.Series) -> pd.Series:
    """Return a boolean Series: True where fare is outside Tukey fences."""
    if len(fares) < 4:
        return pd.Series([False] * len(fares), index=fares.index)
    q1, q3 = fares.quantile(0.25), fares.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - IQR_OUTLIER_MULTIPLIER * iqr
    upper = q3 + IQR_OUTLIER_MULTIPLIER * iqr
    return (fares < lower) | (fares > upper)


def flag_isolation_forest_outliers(
    df: pd.DataFrame,
    feature_cols: list[str] = ["total_fare", "days_to_departure", "stops"],
    contamination: float = 0.03,
    random_state: int = 42,
) -> pd.Series:
    """
    Multivariate outlier flag as a corroborating signal alongside IQR --
    per METHODOLOGY.md §8, neither signal alone triggers deletion.
    """
    if len(df) < 20:
        return pd.Series([False] * len(df), index=df.index)

    X = df[feature_cols].fillna(df[feature_cols].median())
    model = IsolationForest(contamination=contamination, random_state=random_state)
    preds = model.fit_predict(X)  # -1 = outlier, 1 = inlier
    return pd.Series(preds == -1, index=df.index)


def classify_quality_status(
    fares_by_cell: pd.DataFrame,
    fare_col: str = "total_fare",
    group_cols: list[str] = ["route_id", "departure_bucket"],
) -> pd.DataFrame:
    """
    Adds an `is_iqr_outlier` column computed within each (route, bucket) group.
    Caller combines this with isolation-forest flags and business rules to
    set the final quality_status ('valid' | 'suspicious') -- rejection
    already happened in validation.py before this stage.
    """
    out = fares_by_cell.copy()
    out["is_iqr_outlier"] = out.groupby(group_cols)[fare_col].transform(
        lambda s: flag_iqr_outliers(s)
    )
    return out
