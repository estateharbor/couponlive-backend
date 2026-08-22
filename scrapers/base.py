"""BaseScraper interface (Phase 2 fills in concrete scrapers).

Contract: `scrape()` returns a list of `RawCoupon`. Nothing here touches the
database — normalization, dedup, and persistence happen downstream so a
scraper break is isolated to one source and easy to test in isolation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from models.enums import IngestionMethod
from models.schemas import RawCoupon


class BaseScraper(ABC):
    """Every source (scraper or affiliate-API ingestor) implements this."""

    #: Human-readable source name, must match `sources.name`.
    source_name: str = ""
    #: How this source ingests. Affiliate APIs are preferred where available.
    ingestion_method: IngestionMethod = IngestionMethod.scrape_requests

    @abstractmethod
    def scrape(self) -> list[RawCoupon]:
        """Fetch and parse the source, returning structured raw coupons.

        Implementations must be side-effect-free w.r.t. the DB and must raise
        on hard failure (so the scheduler records a failed run) rather than
        silently returning []. An *empty* list means "ran fine, found nothing"
        and is itself an alert signal (see Phase 6).
        """
        raise NotImplementedError
