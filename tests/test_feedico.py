"""Tests for the Feedico coupon-catalog ingestor (mocked HTTP POST).

Feedico is "discovery only" (codes, no affiliate link) and its records may lack a
stable id — so a coupon dedups on (merchant, code) when there's no id. SAMPLE
mirrors the documented normalized shape (merchant/brandName, code, title,
offerUrl/merchantWebsiteUrl).
"""
from __future__ import annotations

import pytest

from models.enums import DiscountType, IngestionMethod
from scrapers.feedico_feed import FeedicoFeedScraper, MissingCredentials, _offers_from


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json))
        page = (json or {}).get("page", 1)
        return _FakeResp(self.pages.get(page, {"data": []}))


SAMPLE = {
    "data": [
        {"merchant": "Nike", "networkName": "Nike US", "code": "SAVE20",
         "title": "20% off select styles", "offerUrl": "https://track/nike"},
        {"brandName": "Ajio", "code": "AJIO500", "title": "Flat ₹500 Off",
         "merchantWebsiteUrl": "https://www.ajio.com/"},                 # no id, has code
        {"merchant": "Boat", "id": "bt1", "code": "", "title": "Up to 60% Off",
         "offerUrl": "https://track/boat"},                             # code-less + id -> deal
        {"merchant": "NoId", "title": "10% off"},                       # no code, no id -> skip
    ]
}


def _scraper(pages=None):
    return FeedicoFeedScraper(api_key="fdco_k", country="IN",
                              session=_FakeSession(pages or {1: SAMPLE}))


def test_maps_codes_and_deals_skips_unidentifiable():
    active = _scraper().scrape()
    by_merchant = {r.merchant_name: r for r in active}
    assert set(by_merchant) == {"Nike", "Ajio", "Boat"}      # "NoId" skipped
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in active)

    nike = by_merchant["Nike"]
    assert nike.code == "SAVE20" and nike.discount_type is DiscountType.percentage
    assert nike.discount_value == 20 and nike.source_url == "https://track/nike"

    ajio = by_merchant["Ajio"]                               # no id -> external_ref None
    assert ajio.code == "AJIO500" and ajio.external_ref is None
    assert ajio.discount_type is DiscountType.fixed and ajio.discount_value == 500
    assert ajio.source_url == "https://www.ajio.com/"

    boat = by_merchant["Boat"]
    assert boat.code is None and boat.external_ref == "bt1"  # code-less -> a deal


def test_bearer_auth_and_pagination_body():
    sess = _FakeSession({1: SAMPLE})
    FeedicoFeedScraper(api_key="fdco_secret", country="IN", session=sess).scrape()
    _url, headers, body = sess.calls[0]
    assert headers["Authorization"] == "Bearer fdco_secret"
    assert body["page"] == 1 and body["country"] == "IN" and "limit" in body


def test_offers_envelope_fallback():
    # Unfamiliar wrapper key ("items") still yields the list.
    assert len(_offers_from({"items": [{"a": 1}, {"b": 2}]})) == 2
    assert len(_offers_from({"data": [{"a": 1}]})) == 1
    assert len(_offers_from([{"a": 1}])) == 1


def test_missing_key_raises():
    with pytest.raises(MissingCredentials):
        FeedicoFeedScraper(api_key="", session=_FakeSession({1: SAMPLE})).scrape()
