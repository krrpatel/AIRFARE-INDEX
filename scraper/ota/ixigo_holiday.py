"""Fetch Ixigo's public India holiday calendar and cache the source response."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://www.ixigo.com/growth/api/v1/holidayCalendar"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data" / "raw_airfare" / "ixigo" / "holidays.json"


def fetch(output: Path = DEFAULT_OUTPUT) -> dict:
    request = Request(URL, headers={"User-Agent": "AirfarePriceIndexResearch/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "ONLINE", "source": URL, "output": str(output), "entry_count": len(payload.get("data", [])), "payload": payload}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache Ixigo holiday calendar.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result = fetch(parser.parse_args().output)
    print(json.dumps({key: value for key, value in result.items() if key != "payload"}))


if __name__ == "__main__":
    main()
