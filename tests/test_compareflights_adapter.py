from airfare.sources.compareflights.offline_adapter import CompareFlightsOfflineAdapter
from airfare.sources.run_adapter import SourceRunAdapter


def _latest_records():
    dates = SourceRunAdapter().run_dates("compareflights")
    assert dates, "A saved CompareFlights source run is required for this test."
    return dates[-1], CompareFlightsOfflineAdapter(run_date=dates[-1]).iter_all_records()


def test_compareflights_import_uses_actual_saved_records():
    _, records = _latest_records()
    assert len(records) > 0
    assert {r.availability_status for r in records} == {"AVAILABLE"}


def test_compareflights_missing_components_remain_null():
    _, records = _latest_records()
    record = records[0]
    assert record.components.total_payable_fare is not None
    assert record.components.base_fare is None
    assert record.components.taxes is None


def test_compareflights_preserves_searched_route_for_alternate_airports():
    _, records = _latest_records()
    assert any(r.source_payload.get("reported_origin") == "NMI" for r in records)
