"""
IndiGo adapter — SKELETON, not a working scraper.

Per docs/METHODOLOGY.md and the project's legal/ethical requirement: before
this adapter is implemented for real, the Data/Scraping lead must check and
record:
  1. robots.txt at the site root
  2. Terms of Service language on automated access
  3. Whether an official partner/affiliate API exists instead

Until that review is done and recorded in the `sources` table
(robots_checked_at, robots_allowed), this adapter must raise rather than
silently return fabricated data — a stub that "looks like" it works is
worse than an honest NotImplementedError, per the project's own coding
rules.
"""
from datetime import date

from scraper.base import AdapterResult, SearchRequest, SourceAdapter, StandardFareRecord


class IndiGoAdapter(SourceAdapter):
    name = "indigo"
    source_type = "airline"
    parser_version = "not-implemented"

    def collect(self, request: SearchRequest) -> AdapterResult:
        raise NotImplementedError(
            "IndiGoAdapter is a documented skeleton. Real implementation is "
            "blocked on robots.txt/ToS review and a permitted collection path."
        )

    def fetch(self, origin: str, destination: str, travel_date: date) -> list[StandardFareRecord]:
        raise NotImplementedError(
            "IndiGoAdapter is a documented skeleton. Real implementation is "
            "blocked on the Data/Scraping lead's robots.txt/ToS review for "
            "indigo.com (Phase 6). Use scraper.mock_adapter.MockAdapter for "
            "all demo/dev/testing purposes until that review is recorded."
        )
