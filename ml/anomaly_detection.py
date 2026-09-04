"""
Anomaly detection layer — corroborates data_pipeline/outliers.py's IQR flag
with a multivariate Isolation Forest signal, per docs/METHODOLOGY.md §8.
Used for the dashboard's Anomalies page, not for silent deletion.
"""
from __future__ import annotations

import pandas as pd

from data_pipeline.outliers import flag_isolation_forest_outliers


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Adds `is_isoforest_outlier` and a combined `anomaly_flag` (IQR OR
    IsolationForest) without removing any row."""
    out = df.copy()
    out["is_isoforest_outlier"] = flag_isolation_forest_outliers(
        out, feature_cols=["total_fare", "days_to_departure", "stops"]
    )
    out["anomaly_flag"] = out.get("is_iqr_outlier", False) | out["is_isoforest_outlier"]
    return out
