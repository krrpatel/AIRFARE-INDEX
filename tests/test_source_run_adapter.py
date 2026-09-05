import json
from pathlib import Path

from airfare.sources.run_adapter import SourceRunAdapter


def test_ixigo_legacy_windows_are_normalized(tmp_path: Path):
    run_dir = tmp_path / "ixigo" / "2026-09-05"
    run_dir.mkdir(parents=True)
    payload = {
        "route": "DEL-BOM",
        "status": "SUCCESS",
        "observations": [{"origin": "DEL", "destination": "BOM", "flight_number": "6E1", "fare": {"amount": 4200, "currency": "INR"}}],
    }
    (run_dir / "DEL-BOM-T+45.json").write_text(json.dumps(payload), encoding="utf-8")

    rows = SourceRunAdapter(tmp_path).rows("ixigo", "2026-09-05")

    assert len(rows) == 1
    assert rows[0]["route"] == "DEL-BOM"
    assert rows[0]["lead_window"] == "T+45"
    assert rows[0]["fare"] == 4200


def test_ixigo_merged_route_windows_are_normalized(tmp_path: Path):
    run_dir = tmp_path / "ixigo" / "2026-09-05"
    run_dir.mkdir(parents=True)
    payload = {
        "source": "ixigo",
        "route": "DEL-BOM",
        "run_date": "2026-09-05",
        "lead_windows": {
            "T+1": {"status": "SUCCESS", "observations": [{"origin": "DEL", "destination": "BOM", "fare": {"amount": 3000}}]},
            "T+45": {"status": "SUCCESS", "observations": [{"origin": "DEL", "destination": "BOM", "fare": {"amount": 5000}}]},
        },
    }
    (run_dir / "DEL-BOM.json").write_text(json.dumps(payload), encoding="utf-8")

    rows = SourceRunAdapter(tmp_path).rows("ixigo", "2026-09-05")

    assert [row["lead_window"] for row in rows] == ["T+1", "T+45"]
    assert [row["fare"] for row in rows] == [3000, 5000]
