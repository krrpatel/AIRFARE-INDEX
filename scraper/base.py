"""Legacy import path for source adapters.

New code should import from `airfare.sources.base`. This module remains so
older scraper code continues to run while the project migrates to the common
adapter package.
"""
from airfare.sources.base import AdapterResult, FareComponents, SearchRequest, SourceAdapter, StandardFareRecord

__all__ = [
    "AdapterResult",
    "FareComponents",
    "SearchRequest",
    "SourceAdapter",
    "StandardFareRecord",
]
