from airfare.airports.registry import is_indian_airport
from airfare.dgca.basket import load_basket_metadata, load_route_basket


def test_dgca_top50_bidirectional_expands_to_100_routes():
    routes = load_route_basket(top_n=50, direction_mode="bidirectional")
    assert len(routes) == 100
    assert abs(sum(r.directional_weight for r in routes) - 1.0) < 1e-6


def test_dgca_unidirectional_keeps_top_n_pairs():
    routes = load_route_basket(top_n=10, direction_mode="unidirectional")
    assert len(routes) == 10
    assert abs(sum(r.directional_weight for r in routes) - sum(r.pair_weight for r in routes)) < 1e-9


def test_dgca_routes_are_indian_domestic():
    routes = load_route_basket(top_n=50, direction_mode="bidirectional")
    assert all(is_indian_airport(r.origin) and is_indian_airport(r.destination) for r in routes)
    assert all(r.origin != r.destination for r in routes)


def test_dgca_metadata_has_latest_window():
    meta = load_basket_metadata()
    assert meta["window_start"] == "2025-08"
    assert meta["window_end"] == "2026-07"
    assert meta["route_count"] == 50

