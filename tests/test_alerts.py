"""Tests for index_engine/alerts.py"""
import pytest

from index_engine.alerts import check_anomaly_rate, check_national_index_move, check_route_index_move


class TestNationalAlert:
    def test_no_alert_below_threshold(self):
        assert check_national_index_move(101.0, 100.0) is None

    def test_alert_above_threshold(self):
        a = check_national_index_move(106.0, 100.0)
        assert a is not None and a.severity == "warning"

    def test_critical_at_double_threshold(self):
        a = check_national_index_move(112.0, 100.0)
        assert a is not None and a.severity == "critical"

    def test_none_when_no_previous(self):
        assert check_national_index_move(100.0, None) is None

    def test_decrease_also_triggers(self):
        a = check_national_index_move(90.0, 100.0)
        assert a is not None and "decreased" in a.message


class TestRouteAlert:
    def test_no_alert_below_threshold(self):
        assert check_route_index_move(("DEL", "BOM"), 105.0, 100.0) is None

    def test_alert_above_threshold(self):
        a = check_route_index_move(("DEL", "BOM"), 120.0, 100.0)
        assert a is not None
        assert "DEL-BOM" in a.message


class TestAnomalyRateAlert:
    def test_no_alert_below_threshold(self):
        assert check_anomaly_rate(2, 100) is None

    def test_alert_above_threshold(self):
        a = check_anomaly_rate(10, 100)
        assert a is not None

    def test_none_when_no_observations(self):
        assert check_anomaly_rate(0, 0) is None
