"""Tests for the Involve Asia Offers-API ingestor (mocked HTTP).

Mirrors the documented two-step shape: POST /authenticate -> {data:{token}}, then
POST /offers/all -> {data:{data:[...]}} (offers double-nested under data.data).
Mapping is defensive; these assertions break loudly if the live shape drifts.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.enums import DiscountType, IngestionMethod
from scrapers.involve_asia_feed import InvolveAsiaFeedScraper, MissingCredentials


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


AUTH = {"status": "success", "message": "ok", "data": {"token": "TOKEN123"}}

SAMPLE = {
    "status": "success",
    "data": {
        "page": 1,
        "limit": 100,
        "data": [
            {"offer_id": "501", "offer_name": "Myntra", "title": "Flat 30% Off",
             "description": "<li>30% off fashion</li>", "coupon_code": "MYN30",
             "status": "active", "tracking_link": "https://invol.co/aff?m=myntra",
             "preview_url": "https://www.myntra.com/"},
            {"offer_id": "502", "offer_name": "Klook", "title": "Up to 15% Off tours",
             "coupon_code": "", "status": "active",
             "tracking_link": "https://invol.co/aff?m=klook"},
            {"offer_id": "503", "offer_name": "OldStore", "title": "Expired 10% Off",
             "coupon_code": "OLD10", "status": "paused",
             "tracking_link": "https://invol.co/x"},
        ],
    },
}


class _FakeSession:
    """Serves the auth token, then per-page offer payloads (empty = stop)."""

    def __init__(self, pages, auth=AUTH):
        self.pages = pages
        self.auth = auth
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data, headers))
        if url.endswith("/authenticate"):
            return _FakeResp(self.auth)
        page = (data or {}).get("page", 1)
        return _FakeResp(self.pages.get(page, {"data": {"data": []}}))


def _scraper(pages=None):
    return InvolveAsiaFeedScraper(
        api_key="k", api_secret="s", session=_FakeSession(pages or {1: SAMPLE})
    )


def test_authenticate_extracts_token():
    s = _scraper()
    assert s.authenticate() == "TOKEN123"


def test_maps_codes_and_deals_and_skips_paused():
    active = _scraper().scrape()
    by_ref = {r.external_ref: r for r in active}
    assert set(by_ref) == {"501", "502"}            # "503" is paused -> skipped
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in active)

    myntra = by_ref["501"]
    assert myntra.code == "MYN30" and myntra.merchant_name == "Myntra"
    assert myntra.discount_type is DiscountType.percentage and myntra.discount_value == 30

    assert by_ref["502"].code is None               # empty coupon_code -> a deal


def test_tracking_link_preferred_for_commission():
    myntra = {r.external_ref: r for r in _scraper().scrape()}["501"]
    # MUST be the Involve Asia tracking deeplink, never the plain merchant site.
    assert myntra.source_url == "https://invol.co/aff?m=myntra"
    assert "myntra.com" not in myntra.source_url


def test_html_description_stripped_and_rupee_decoded():
    s = _scraper()
    item = {"offer_id": "9", "offer_name": "Foo", "coupon_code": "X",
            "title": "", "description": "<li>Flat &#8377;300 OFF</li><li>terms</li>"}
    rc = s._map_offer(item, datetime.now(timezone.utc))
    assert "<li>" not in (rc.description or "")
    assert rc.discount_type is DiscountType.fixed and rc.discount_value == 300


def test_sends_bearer_token_on_offers_call():
    sess = _FakeSession({1: SAMPLE})
    InvolveAsiaFeedScraper(api_key="k", api_secret="s", session=sess).scrape()
    # calls[0] = authenticate, calls[1] = offers/all with the bearer token.
    offers_call = next(c for c in sess.calls if c[0].endswith("/offers/all"))
    assert offers_call[2]["Authorization"] == "Bearer TOKEN123"


def test_pagination_dedupes_and_stops():
    row = {"offer_id": "c1", "offer_name": "Myntra", "title": "10% Off",
           "coupon_code": "A", "status": "active"}
    s = InvolveAsiaFeedScraper(
        api_key="k", api_secret="s",
        session=_FakeSession({1: {"data": {"data": [row]}}, 2: {"data": {"data": [row]}}}),
    )
    assert len(s.scrape()) == 1                      # dedup by external_ref; repeat stops loop


def test_missing_credentials_raises():
    with pytest.raises(MissingCredentials):
        InvolveAsiaFeedScraper(api_key="", api_secret="", session=_FakeSession({1: SAMPLE})).scrape()
