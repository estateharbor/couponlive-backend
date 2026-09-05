"""Tests for the Cuelinks Offers-API ingestor (mocked HTTP).

SAMPLE mirrors the REAL Cuelinks response shape (confirmed live 2026-09):
merchant in `campaign`, code in `coupon_code`, the tracked link in
`affiliate_url` (NOT the plain `url`), an HTML `description`, and a `status`.
If Cuelinks changes these, the assertions break loudly — our early warning.
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
        {"id": 128504, "campaign": "Airalo WW", "title": "Claim Your 15% Discount",
         "description": "<li>Receive 15% off your first eSIM!</li>", "coupon_code": "SEPTEMBER15",
         "type": "discount", "status": "live", "url": "https://www.airalo.com/",
         "affiliate_url": "https://linksredirect.com/?pub_id=272129&url=airalo"},
        {"id": 200, "campaign": "Amazon WW", "title": "Up to 60% Off Fashion",
         "description": "", "coupon_code": "", "type": "deal", "status": "live",
         "url": "https://www.amazon.in/", "affiliate_url": "https://linksredirect.com/?pub_id=1&url=amazon"},
        {"id": 300, "campaign": "OldStore", "title": "Expired 10% Off", "coupon_code": "OLD10",
         "type": "discount", "status": "expired", "affiliate_url": "https://linksredirect.com/?x"},
    ]
}


def _scraper(pages=None):
    return CuelinksFeedScraper(api_key="k", session=_FakeSession(pages or {1: SAMPLE}))


def test_maps_codes_and_deals_and_skips_expired():
    active = _scraper().scrape()
    by_ref = {r.external_ref: r for r in active}
    assert set(by_ref) == {"128504", "200"}       # "300" is expired -> skipped
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in active)

    airalo = by_ref["128504"]
    assert airalo.code == "SEPTEMBER15" and airalo.merchant_name == "Airalo WW"
    assert airalo.discount_type is DiscountType.percentage and airalo.discount_value == 15

    deal = by_ref["200"]
    assert deal.code is None                       # empty coupon_code -> a deal


def test_affiliate_url_preferred_for_commission():
    airalo = {r.external_ref: r for r in _scraper().scrape()}["128504"]
    # MUST be the tracked linksredirect URL, never the plain merchant homepage.
    assert airalo.source_url.startswith("https://linksredirect.com/")
    assert "airalo.com" not in airalo.source_url


def test_html_description_stripped():
    s = _scraper()
    item = {"id": "9", "campaign": "Foo", "coupon_code": "X", "type": "discount",
            "title": "", "description": "<li>Flat &#8377;300 OFF</li><li>terms apply</li>"}
    rc = s._map_offer(item, datetime.now(timezone.utc))
    assert "<li>" not in (rc.description or "")
    assert rc.discount_type is DiscountType.fixed and rc.discount_value == 300  # ₹ entity decoded


def test_auth_header_and_key_sent():
    sess = _FakeSession({1: SAMPLE})
    CuelinksFeedScraper(api_key="secret", session=sess).scrape()
    _url, headers, params = sess.calls[0]
    assert headers["Authorization"] == "Token secret"
    assert params["api_key"] == "secret"


def test_pagination_dedupes_and_stops():
    row = {"id": "c1", "campaign": "Amazon", "title": "10% Off", "coupon_code": "A", "status": "live"}
    s = CuelinksFeedScraper(api_key="k", session=_FakeSession({1: {"offers": [row]},
                                                               2: {"offers": [row]}}))
    assert len(s.scrape()) == 1                     # dedup by external_ref; repeat page stops loop


def test_missing_key_raises():
    with pytest.raises(MissingCredentials):
        CuelinksFeedScraper(api_key="", session=_FakeSession({1: SAMPLE})).scrape()
