from airfare.sources.compareflights.offline_adapter import CompareFlightsOfflineAdapter


def test_compareflights_import_uses_actual_saved_records():
    records = CompareFlightsOfflineAdapter(run_date="2026-08-26").iter_all_records()
    assert len(records) == 3816
    assert {r.availability_status for r in records} == {"AVAILABLE"}


def test_compareflights_missing_components_remain_null():
    record = CompareFlightsOfflineAdapter(run_date="2026-08-26").iter_all_records()[0]
    assert record.components.total_payable_fare is not None
    assert record.components.base_fare is None
    assert record.components.taxes is None


def test_compareflights_preserves_searched_route_for_alternate_airports():
    records = CompareFlightsOfflineAdapter(run_date="2026-08-26").iter_all_records()
    assert all((r.origin, r.destination) in {("DEL", "BOM"), ("BOM", "DEL"), ("DEL", "BLR"), ("BLR", "DEL")} for r in records)
    assert any(r.source_payload.get("reported_origin") == "NMI" for r in records)

