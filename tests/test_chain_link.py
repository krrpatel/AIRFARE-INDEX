"""Tests for index_engine/chain_link.py"""
import pytest

from index_engine.chain_link import chain_link


class TestChainLink:
    def test_splice_point_continuity(self):
        backcast = [("2026-01-01", 100.0), ("2026-08-24", 96.5654)]
        live = [("2026-08-24", 100.0), ("2026-08-25", 102.0)]
        linked = chain_link(backcast, live)

        # backcast points unchanged
        assert linked[0].index_value == 100.0
        assert linked[1].index_value == 96.5654
        assert linked[1].data_mode == "simulated_backcast"

        # live point rescaled: +2% move applied to the backcast's ending value
        assert linked[2].date == "2026-08-25"
        assert linked[2].data_mode == "live"
        assert linked[2].index_value == pytest.approx(96.5654 * 1.02, rel=1e-6)

    def test_live_relative_movement_preserved(self):
        backcast = [("2026-01-01", 100.0), ("2026-06-01", 110.0)]
        live = [("2026-06-01", 200.0), ("2026-06-02", 220.0)]  # live's own scale: +10%
        linked = chain_link(backcast, live)
        live_point = [p for p in linked if p.date == "2026-06-02"][0]
        # +10% move on live's own scale should translate to +10% on the linked scale too
        assert live_point.index_value == pytest.approx(110.0 * 1.10, rel=1e-6)

    def test_raises_on_empty_backcast(self):
        with pytest.raises(ValueError):
            chain_link([], [("2026-01-01", 100.0)])

    def test_raises_on_empty_live(self):
        with pytest.raises(ValueError):
            chain_link([("2026-01-01", 100.0)], [])

    def test_raises_when_splice_date_not_in_backcast(self):
        backcast = [("2026-01-01", 100.0)]
        live = [("2026-12-31", 100.0)]  # no overlap
        with pytest.raises(ValueError):
            chain_link(backcast, live)

    def test_raises_on_zero_live_splice_value(self):
        backcast = [("2026-01-01", 100.0)]
        live = [("2026-01-01", 0.0)]
        with pytest.raises(ValueError):
            chain_link(backcast, live)

    def test_output_length(self):
        backcast = [("2026-01-01", 100.0), ("2026-01-02", 101.0)]
        live = [("2026-01-02", 50.0), ("2026-01-03", 51.0), ("2026-01-04", 52.0)]
        linked = chain_link(backcast, live)
        # 2 backcast points + 2 live points (splice date's live entry is dropped, backcast wins)
        assert len(linked) == 4
