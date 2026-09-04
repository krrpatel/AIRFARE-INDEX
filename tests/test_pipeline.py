"""
Tests for data_pipeline/pipeline.py -- the DB-free end-to-end path.
Uses a small synthetic dataset (not the full mock generator) so tests run
fast and assert on hand-computable expected values.
"""
import pandas as pd
import pytest

from data_pipeline.pipeline import (
    apply_outlier_flags,
    compute_daily_route_bucket_prices,
    compute_national_index_for_day,
    route_bucket_prices_to_dict,
    validate_dataframe,
)


def _make_df():
    """
    Each (day, bucket) cell gets >= MIN_WEEKLY_OBSERVATIONS_FOR_ROUTE rows so
    routes clear the real inclusion threshold -- this mirrors how many
    observations a live scrape interval would realistically produce, rather
    than testing against an artificially thin sample the methodology would
    correctly exclude.
    """
    rows = []
    # base day 2026-01-01: DEL-BOM, two buckets, clean prices, 5 obs each
    for _ in range(5):
        rows.append(dict(origin="DEL", destination="BOM", travel_date="2026-01-10",
                          booking_ts="2026-01-01T00:00:00", departure_bucket="B4",
                          base_fare=4000, taxes=800, total_fare=4800, currency="INR"))
        rows.append(dict(origin="DEL", destination="BOM", travel_date="2026-01-05",
                          booking_ts="2026-01-01T00:00:00", departure_bucket="B2",
                          base_fare=4200, taxes=800, total_fare=5000, currency="INR"))
    # later day 2026-01-15: same route, price up 10% in both buckets, 5 obs each
    for _ in range(5):
        rows.append(dict(origin="DEL", destination="BOM", travel_date="2026-02-15",
                          booking_ts="2026-01-15T00:00:00", departure_bucket="B4",
                          base_fare=4400, taxes=880, total_fare=5280, currency="INR"))
        rows.append(dict(origin="DEL", destination="BOM", travel_date="2026-01-20",
                          booking_ts="2026-01-15T00:00:00", departure_bucket="B2",
                          base_fare=4620, taxes=880, total_fare=5500, currency="INR"))
    # a clearly invalid row: negative fare -- must be rejected, never enter index
    rows.append(dict(origin="DEL", destination="BOM", travel_date="2026-01-10",
                      booking_ts="2026-01-01T00:00:00", departure_bucket="B4",
                      base_fare=-100, taxes=0, total_fare=-100, currency="INR"))

    df = pd.DataFrame(rows)
    df["booking_date"] = pd.to_datetime(df["booking_ts"]).dt.date.astype(str)
    return df


class TestPipeline:
    def test_negative_fare_rejected(self):
        df = validate_dataframe(_make_df())
        rejected = df[df["quality_status"] == "rejected"]
        assert len(rejected) == 1
        assert "negative_fare" in rejected.iloc[0]["reject_reasons"]

    def test_valid_rows_pass_validation(self):
        df = validate_dataframe(_make_df())
        assert (df["quality_status"] == "valid").sum() == 20

    def test_bucket_representative_price_median(self):
        df = validate_dataframe(_make_df())
        grouped = compute_daily_route_bucket_prices(df, "2026-01-01")
        prices = route_bucket_prices_to_dict(grouped)
        assert prices[("DEL", "BOM")]["B4"] == 4800
        assert prices[("DEL", "BOM")]["B2"] == 5000

    def test_national_index_reflects_price_increase(self):
        df = apply_outlier_flags(validate_dataframe(_make_df()))
        base_grouped = compute_daily_route_bucket_prices(df, "2026-01-01")
        base_prices = route_bucket_prices_to_dict(base_grouped)

        weights = {("DEL", "BOM"): 1.0}
        idx_base, inc_base, exc_base = compute_national_index_for_day(
            df, "2026-01-01", base_prices, weights
        )
        idx_later, inc_later, exc_later = compute_national_index_for_day(
            df, "2026-01-15", base_prices, weights
        )
        assert idx_base == pytest.approx(100.0)
        # B4: 4800->5280 (+10%), B2: 5000->5500 (+10%) => geometric mean +10% => 110.0
        assert idx_later == pytest.approx(110.0, rel=1e-6)

    def test_reproducible_across_repeated_calls(self):
        df = apply_outlier_flags(validate_dataframe(_make_df()))
        base_grouped = compute_daily_route_bucket_prices(df, "2026-01-01")
        base_prices = route_bucket_prices_to_dict(base_grouped)
        weights = {("DEL", "BOM"): 1.0}

        r1 = compute_national_index_for_day(df, "2026-01-15", base_prices, weights)
        r2 = compute_national_index_for_day(df, "2026-01-15", base_prices, weights)
        assert r1 == r2
