"""Registry mapping source name -> scraper/ingestor factory.

Lets the scheduler (Phase 6) resolve a `sources` row to a runnable scraper
without importing each module by hand. Add one line per new source.
"""
from __future__ import annotations

from collections.abc import Callable

from scrapers.base import BaseScraper
from scrapers.desidime import DesidimeScraper
from scrapers.inrdeals import InrdealsIngestor

# name -> zero-arg factory (override with configured args where needed)
SCRAPER_REGISTRY: dict[str, Callable[[], BaseScraper]] = {
    DesidimeScraper.source_name: lambda: DesidimeScraper(max_pages=1),
    InrdealsIngestor.source_name: lambda: InrdealsIngestor(),
}


def get_scraper(source_name: str) -> BaseScraper:
    try:
        return SCRAPER_REGISTRY[source_name]()
    except KeyError as exc:
        raise KeyError(
            f"No scraper registered for source {source_name!r}. "
            f"Known: {sorted(SCRAPER_REGISTRY)}"
        ) from exc
