"""
Tests for index_engine/. Run with: pytest tests/test_index_engine.py -v
"""
import pytest

from index_engine.index import RouteDayResult, compute_national_index, pct_change
from index_engine.price_relative import route_price_relative, bucket_representative_price, FareObs
from index_engine.weights import compute_route_weights
from index_engine.basket import PROTOTYPE_BASKET


class TestWeights:
    def test_weights_sum_to_one(self):
        weights = compute_route_weights()
        assert abs(sum(w.weight for w in weights) - 1.0) < 1e-9

    def test_every_basket_route_has_a_weight(self):
        weights = compute_route_weights()
        assert len(weights) == len(PROTOTYPE_BASKET)

    def test_no_negative_weights(self):
        assert all(w.weight >= 0 for w in compute_route_weights())


class TestPriceRelative:
    def test_bucket_representative_price_uses_median_and_excludes_suspicious(self):
        obs = [
            FareObs(1, "B1", 5000, "valid"),
            FareObs(1, "B1", 5200, "valid"),
            FareObs(1, "B1", 4800, "valid"),
            FareObs(1, "B1", 99999, "suspicious"),  # must be excluded
        ]
        assert bucket_representative_price(obs) == 5000

    def test_bucket_representative_price_none_when_no_valid_data(self):
        obs = [FareObs(1, "B1", 100, "rejected")]
        assert bucket_representative_price(obs) is None

    def test_route_price_relative_no_change_equals_100pct(self):
        current = {"B1": 5000.0, "B2": 4000.0}
        base = {"B1": 5000.0, "B2": 4000.0}
        rel = route_price_relative(current, base)
        assert rel == pytest.approx(1.0, rel=1e-9)

    def test_route_price_relative_uniform_10pct_increase(self):
        base = {"B1": 5000.0, "B2": 4000.0}
        current = {"B1": 5500.0, "B2": 4400.0}
        rel = route_price_relative(current, base)
        assert rel == pytest.approx(1.10, rel=1e-6)

    def test_route_price_relative_ignores_unmatched_buckets(self):
        base = {"B1": 5000.0}
        current = {"B1": 5500.0, "B2": 999999.0}  # B2 has no base -> must be ignored
        rel = route_price_relative(current, base)
        assert rel == pytest.approx(1.10, rel=1e-6)

    def test_route_price_relative_none_when_no_overlap(self):
        assert route_price_relative({"B3": 100.0}, {"B1": 100.0}) is None


class TestNationalIndex:
    def test_no_change_gives_index_100(self):
        results = [
            RouteDayResult(route_id=1, price_relative=1.0, weight=0.6, observation_count=10),
            RouteDayResult(route_id=2, price_relative=1.0, weight=0.4, observation_count=10),
        ]
        out = compute_national_index(results)
        assert out.index_value == pytest.approx(100.0)
        assert out.routes_included == 2
        assert out.routes_excluded == 0

    def test_weighted_aggregation_is_correct(self):
        # route1: +20%, weight 0.7 | route2: -10%, weight 0.3
        # expected = 100 * (0.7*1.2 + 0.3*0.9) / 1.0 = 100 * (0.84+0.27) = 111.0
        results = [
            RouteDayResult(route_id=1, price_relative=1.2, weight=0.7, observation_count=10),
            RouteDayResult(route_id=2, price_relative=0.9, weight=0.3, observation_count=10),
        ]
        out = compute_national_index(results)
        assert out.index_value == pytest.approx(111.0, rel=1e-6)

    def test_deterministic_reproducibility(self):
        results = [
            RouteDayResult(route_id=1, price_relative=1.05, weight=0.5, observation_count=8),
            RouteDayResult(route_id=2, price_relative=0.97, weight=0.5, observation_count=8),
        ]
        out1 = compute_national_index(results)
        out2 = compute_national_index(results)
        assert out1 == out2

    def test_low_observation_route_excluded_and_weight_redistributed(self):
        results = [
            RouteDayResult(route_id=1, price_relative=1.0, weight=0.5, observation_count=10),
            RouteDayResult(route_id=2, price_relative=2.0, weight=0.5, observation_count=1),  # below MIN threshold
        ]
        out = compute_national_index(results)
        # route2 excluded -> index driven entirely by route1's 1.0 relative -> 100.0
        assert out.index_value == pytest.approx(100.0)
        assert out.routes_included == 1
        assert out.routes_excluded == 1

    def test_raises_when_no_usable_data(self):
        results = [RouteDayResult(route_id=1, price_relative=None, weight=1.0, observation_count=0)]
        with pytest.raises(ValueError):
            compute_national_index(results)


class TestPctChange:
    def test_basic_increase(self):
        assert pct_change(110.0, 100.0) == pytest.approx(10.0)

    def test_basic_decrease(self):
        assert pct_change(90.0, 100.0) == pytest.approx(-10.0)

    def test_none_when_previous_missing(self):
        assert pct_change(100.0, None) is None

    def test_none_when_previous_zero(self):
        assert pct_change(100.0, 0.0) is None
