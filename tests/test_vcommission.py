"""Tests for the vCommission (Trackier) publisher-API ingestor (mocked HTTP).

Mirrors the real shapes: campaigns -> data.campaigns[{id,title,tracking_link}]
(page-based); coupons -> {coupons:[...],pageToken}; deals -> {deals:[...],pageToken}.
Coupons/deals carry only campaign_id, joined to the campaign's tracking_link.
"""
from __future__ import annotations

import pytest

from models.enums import DiscountType, IngestionMethod
from scrapers.vcommission_feed import VCommissionFeedScraper, MissingCredentials, _clean_tracking


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


CAMPAIGNS = [
    {"id": 101, "title": "Myntra",
     "tracking_link": "https://trk.vc.com/abc?p1={your-transaction-id}&source={sub}"},
    {"id": 102, "title": "Nykaa", "tracking_link": "https://trk.vc.com/xyz"},
]
COUPONS = {
    None: {"coupons": [
        {"id": "c1", "code": "MYN30", "description": "Flat 30% off fashion",
         "status": "active", "campaign_name": "Myntra", "campaign_id": 101},
        {"id": "c2", "code": "OLD", "description": "old", "status": "expired",
         "campaign_name": "Myntra", "campaign_id": 101},
        {"id": "c3", "code": "NYKX", "description": "₹200 off", "status": "active",
         "campaign_name": "Nykaa", "campaign_id": 999},  # campaign not in map
    ], "pageToken": "P2"},
    "P2": {"coupons": [
        {"id": "c4", "code": "EXTRA10", "description": "10% off", "status": "active",
         "campaign_name": "Nykaa", "campaign_id": 102},
    ], "pageToken": None},
}
DEALS = {
    None: {"deals": [
        {"id": "d1", "name": "BBD", "description": "Up to 70% off", "status": "active",
         "campaign_name": "Myntra", "campaign_id": 101},
    ], "pageToken": None},
}


class _FakeSession:
    def __init__(self, campaigns=CAMPAIGNS, coupons=COUPONS, deals=DEALS):
        self.campaigns = campaigns
        self.coupons = coupons
        self.deals = deals
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        if url.endswith("/publisher/campaigns"):
            return _FakeResp({"success": True, "data": {
                "campaigns": self.campaigns, "page": 1, "count": len(self.campaigns)}})
        if url.endswith("/publishers/coupons"):
            return _FakeResp(self.coupons.get((params or {}).get("pageToken"),
                                              {"coupons": [], "pageToken": None}))
        if url.endswith("/publishers/deals"):
            return _FakeResp(self.deals.get((params or {}).get("pageToken"),
                                            {"deals": [], "pageToken": None}))
        return _FakeResp({})


def _scraper():
    return VCommissionFeedScraper(api_url="https://api.vcommission.com/v2", api_key="k",
                                  session=_FakeSession())


def test_clean_tracking_strips_placeholders():
    assert _clean_tracking("https://trk.vc.com/abc?p1={x}&source={y}") == "https://trk.vc.com/abc"
    assert _clean_tracking("https://trk.vc.com/xyz") == "https://trk.vc.com/xyz"


def test_campaign_links_built_and_cleaned():
    links = _scraper()._fetch_campaign_links()
    assert links == {"101": "https://trk.vc.com/abc", "102": "https://trk.vc.com/xyz"}


def test_scrape_maps_codes_and_deals_joins_tracking_and_skips_expired():
    out = _scraper().scrape()
    by_ref = {r.external_ref: r for r in out}
    # c2 (expired) skipped; c1, c3, c4 coupons + d1 deal
    assert set(by_ref) == {"vc_coupon_c1", "vc_coupon_c3", "vc_coupon_c4", "vc_deal_d1"}
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in out)

    c1 = by_ref["vc_coupon_c1"]
    assert c1.code == "MYN30" and c1.merchant_name == "Myntra"
    assert c1.source_url == "https://trk.vc.com/abc"           # joined affiliate link
    assert c1.discount_type is DiscountType.percentage and c1.discount_value == 30

    d1 = by_ref["vc_deal_d1"]
    assert d1.code is None and d1.source_url == "https://trk.vc.com/abc"  # code-less deal


def test_coupon_without_campaign_match_has_no_url():
    c3 = {r.external_ref: r for r in _scraper().scrape()}["vc_coupon_c3"]
    assert c3.code == "NYKX" and c3.source_url is None        # campaign 999 not in map


def test_token_pagination_follows_pagetoken():
    out = _scraper().scrape()
    assert "vc_coupon_c4" in {r.external_ref for r in out}    # 2nd coupon page via pageToken
    c4 = {r.external_ref: r for r in out}["vc_coupon_c4"]
    assert c4.source_url == "https://trk.vc.com/xyz"


def test_missing_key_raises():
    with pytest.raises(MissingCredentials):
        VCommissionFeedScraper(api_key="", session=_FakeSession()).scrape()
