"""Offline-testable, bounded research source adapters."""

from trader.research.sources.adapters import (
    CotSource,
    EconomicCalendarSource,
    FredSource,
    RSSSource,
    URLFetchSource,
    WebSearchSource,
)
from trader.research.sources.base import (
    HttpResearchSource,
    InvalidSourceResponse,
    Reliability,
    ResearchRepository,
    SourceError,
)

__all__ = [
    "CotSource",
    "EconomicCalendarSource",
    "FredSource",
    "HttpResearchSource",
    "InvalidSourceResponse",
    "RSSSource",
    "Reliability",
    "ResearchRepository",
    "SourceError",
    "URLFetchSource",
    "WebSearchSource",
]
