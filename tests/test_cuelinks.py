"""Tests for the Cuelinks Offers-API ingestor (mocked HTTP).

Response-shape health check + the code/deal split: a coupon-code offer becomes a
coded RawCoupon; a code-less offer becomes a deal (code=None). If Cuelinks
changes field names these assertions break loudly — our early warning.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.enums import DiscountType, IngestionMethod
from scrapers.cuelinks_feed import CuelinksFeedScraper, MissingCredentials


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """Serves per-page payloads; unknown pages return an empty feed (stop)."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        page = (params or {}).get("page", 1)
        return _FakeResp(self.pages.get(page, {"offers": []}))


SAMPLE = {
    "offers": [
        {"id": "c1", "merchant": "Amazon", "title": "Flat 40% Off Electronics",
         "coupon_code": "AMZ40", "offer_type": "Code", "url": "https://cue/c1"},
        {"id": "c2", "merchant": "Amazon", "title": "Up to 60% Off Fashion",
         "offer_type": "Deal", "url": "https://cue/c2"},                     # no code -> deal
        {"id": "c3", "store": "Flipkart", "description": "Flat &#8377;300 Off first order",
         "code": "FLIP300", "offer_type": "Code", "link": "https://cue/c3"},
    ]
}


def _scraper(pages=None):
    return CuelinksFeedScraper(api_key="k", session=_FakeSession(pages or {1: SAMPLE}))


def test_maps_codes_and_deals():
    active = _scraper().scrape()
    by_ref = {r.external_ref: r for r in active}
    assert set(by_ref) == {"c1", "c2", "c3"}
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in active)

    amazon_code = by_ref["c1"]
    assert amazon_code.code == "AMZ40" and amazon_code.merchant_name == "Amazon"
    assert amazon_code.discount_type is DiscountType.percentage and amazon_code.discount_value == 40
    assert amazon_code.source_url == "https://cue/c1"

    deal = by_ref["c2"]
    assert deal.code is None                      # code-less -> a deal
    assert deal.source_url == "https://cue/c2"

    flip = by_ref["c3"]                            # `store` + `link` + `code` fallbacks
    assert flip.code == "FLIP300" and flip.merchant_name == "Flipkart"


def test_html_entity_rupee_decoded():
    # "&#8377;300" must map to fixed/300, not unknown/8377.
    s = _scraper()
    item = {"id": "x", "merchant": "Ajio", "code": "A300",
            "description": "Get Flat &#8377;300 OFF", "offer_type": "Code"}
    rc = s._map_offer(item, datetime.now(timezone.utc))
    assert rc.discount_type is DiscountType.fixed and rc.discount_value == 300


def test_auth_header_and_key_sent():
    sess = _FakeSession({1: SAMPLE})
    CuelinksFeedScraper(api_key="secret", session=sess).scrape()
    _url, headers, params = sess.calls[0]
    assert headers["Authorization"] == "Token secret"
    assert params["api_key"] == "secret"


def test_pagination_dedupes_and_stops():
    pages = {
        1: {"offers": [{"id": "c1", "merchant": "Amazon", "title": "10% Off", "code": "A"}]},
        2: {"offers": [{"id": "c1", "merchant": "Amazon", "title": "10% Off", "code": "A"}]},  # dup
    }
    s = CuelinksFeedScraper(api_key="k", session=_FakeSession(pages))
    active = s.scrape()
    assert len(active) == 1                        # dedup by external_ref; repeat page stops loop


def test_missing_key_raises():
    with pytest.raises(MissingCredentials):
        CuelinksFeedScraper(api_key="", session=_FakeSession({1: SAMPLE})).scrape()
