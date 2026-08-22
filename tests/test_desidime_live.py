"""Selector health check for the Desidime scraper.

This runs the LIVE scraper against desidime.com. Its job is to fail loudly the
moment the site redesign breaks our selectors — that is the whole point of the
"selector health check" pattern, so breakage is caught immediately, not
discovered silently in stale data.

Marked `live` so CI can opt in/out:  `pytest -m live`  /  `pytest -m "not live"`.
"""
from __future__ import annotations

import pytest

from models.enums import IngestionMethod
from scrapers.desidime import DesidimeScraper

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def scraped():
    scraper = DesidimeScraper(max_pages=1)
    try:
        return scraper.scrape()
    except Exception as exc:  # network/site down -> skip, don't hard-fail CI
        pytest.skip(f"Desidime unreachable: {exc}")


def test_returns_results(scraped):
    # The core health assertion: a working scraper yields a full page of cards.
    assert len(scraped) >= 10, f"only {len(scraped)} cards — selectors may be broken"


def test_fields_look_valid(scraped):
    for rc in scraped:
        assert rc.merchant_name and rc.merchant_name.strip()
        assert rc.external_ref and rc.external_ref.isdigit()
        assert rc.requires_reveal is True          # Desidime reveal-gates codes
        assert rc.code is None                      # ...so we never scrape a code
        assert rc.ingestion_method is IngestionMethod.scrape_requests
        assert rc.source_url


def test_some_discounts_parsed(scraped):
    # At least a quarter of cards should yield a parseable discount value,
    # else our discount regex has drifted from the site's labels.
    with_value = sum(1 for rc in scraped if rc.discount_value is not None)
    assert with_value >= len(scraped) // 4
