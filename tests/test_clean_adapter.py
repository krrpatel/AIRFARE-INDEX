import json
from pathlib import Path

from airfare.sources.clean_adapter import CleanSourceAdapter
from airfare.sources.run_adapter import SourceRunAdapter


def _write_ixigo(tmp_path: Path, route: str = "DEL-BOM") -> None:
    run_dir = tmp_path / "ixigo" / "2026-09-05"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"route": route, "lead_windows": {"T+1": {"status": "SUCCESS", "observations": [{"origin": "DEL", "destination": "BOM", "flight_number": "6E1", "fare": {"amount": 4200}, "stops": 1}]}}}
    (run_dir / f"{route}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_clean_adapter_writes_one_dated_file_with_stops(tmp_path: Path):
    _write_ixigo(tmp_path)
    adapter = CleanSourceAdapter(SourceRunAdapter(tmp_path), tmp_path / "clean_airfare")

    validation = adapter.validate("ixigo", "2026-09-05", ["DEL-BOM"])
    result = adapter.build("ixigo", "2026-09-05", ["DEL-BOM"])

    assert validation["valid"] is True
    assert Path(result["path"]).name == "05092026.json"
    payload = adapter.read("ixigo", "2026-09-05")
    assert payload["schema_version"] == "airfare-clean-v1"
    assert payload["rows"][0]["number_of_stops"] == 1
    assert payload["rows"][0]["departure"] is None
    assert payload["rows"][0]["arrival"] is None
    assert payload["rows"][0]["duration_minutes"] is None


def test_clean_adapter_rejects_missing_routes(tmp_path: Path):
    _write_ixigo(tmp_path)
    adapter = CleanSourceAdapter(SourceRunAdapter(tmp_path), tmp_path / "clean_airfare")

    validation = adapter.validate("ixigo", "2026-09-05", ["DEL-BOM", "BOM-DEL"])

    assert validation["valid"] is False
    assert validation["missing_routes"] == ["BOM-DEL"]


def test_clean_adapter_can_explicitly_publish_partial_data(tmp_path: Path):
    _write_ixigo(tmp_path)
    adapter = CleanSourceAdapter(SourceRunAdapter(tmp_path), tmp_path / "clean_airfare")

    result = adapter.build("ixigo", "2026-09-05", ["DEL-BOM", "BOM-DEL"], allow_missing=True)

    payload = adapter.read("ixigo", "2026-09-05")
    assert result["validation"]["missing_routes"] == ["BOM-DEL"]
    assert payload["publication_status"] == "PARTIAL_MISSING_ROUTES"
    assert payload["allow_missing_routes"] is True


def test_clean_adapter_filters_rows_to_configured_routes(tmp_path: Path):
    _write_ixigo(tmp_path, "DEL-BOM")
    _write_ixigo(tmp_path, "BOM-DEL")
    adapter = CleanSourceAdapter(SourceRunAdapter(tmp_path), tmp_path / "clean_airfare")

    result = adapter.build("ixigo", "2026-09-05", ["DEL-BOM"])

    payload = adapter.read("ixigo", "2026-09-05")
    assert result["route_count"] == 1
    assert payload["configured_routes"] == ["DEL-BOM"]
    assert {row["route"] for row in payload["rows"]} == {"DEL-BOM"}
