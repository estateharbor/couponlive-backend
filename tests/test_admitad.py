"""Tests for the Admitad (Mitgo) coupons-API ingestor (mocked HTTP).

Mirrors the OAuth2 flow (POST /token/ -> access_token) and the coupons shape
({results:[...], _meta:{count}}). promocode -> code; goto_link -> affiliate link;
campaign.name -> merchant.
"""
from __future__ import annotations

import base64

import pytest

from models.enums import DiscountType, IngestionMethod
from scrapers.admitad_feed import AdmitadFeedScraper, AuthError, MissingCredentials


class _FakeResp:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("http error")

    def json(self):
        return self._payload


TOKEN = {"access_token": "TOK123", "token_type": "bearer", "expires_in": 3600}

PAGE0 = {
    "results": [
        {"id": 11, "name": "15% off", "discount": "15%", "promocode": "SAVE15",
         "status": "active", "campaign": {"id": 1, "name": "Myntra"},
         "goto_link": "https://ad.admitad.com/g/abc/", "description": "15% off fashion"},
        {"id": 12, "name": "Big Sale", "discount": "up to 70%", "promocode": None,
         "status": "active", "campaign": {"id": 2, "name": "AJIO"},
         "goto_link": "https://ad.admitad.com/g/def/", "description": "Up to 70% off"},
        {"id": 13, "name": "Dead", "promocode": "OLD", "status": "inactive",
         "campaign": {"id": 1, "name": "Myntra"}, "goto_link": "https://ad.admitad.com/g/x/"},
    ],
    "_meta": {"count": 3, "limit": 200, "offset": 0},
}


class _FakeSession:
    def __init__(self, token=TOKEN, pages=None, websites=None):
        self.token = token
        self.pages = pages or {0: PAGE0}
        self.websites = websites if websites is not None else []
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, headers))
        return _FakeResp(self.token)

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params, headers))
        if "/websites/" in url:
            return _FakeResp(self.websites)   # Admitad answers a bare list here
        return _FakeResp(self.pages.get((params or {}).get("offset", 0),
                                        {"results": [], "_meta": {"count": 3}}))


def _scraper(**kw):
    return AdmitadFeedScraper(client_id="cid", client_secret="csec",
                             session=_FakeSession(**kw))


def test_authenticate_uses_basic_auth_and_returns_token():
    s = _scraper()
    assert s.authenticate() == "TOK123"
    _m, url, data, headers = s.session.calls[0]
    assert url.endswith("/token/")
    assert data["grant_type"] == "client_credentials"
    expected = base64.b64encode(b"cid:csec").decode()
    assert headers["Authorization"] == f"Basic {expected}"


def test_maps_codes_and_deals_skips_inactive_and_joins_goto_link():
    out = _scraper().scrape()
    by_ref = {r.external_ref: r for r in out}
    assert set(by_ref) == {"admitad_11", "admitad_12"}  # id 13 inactive -> skipped
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in out)

    c = by_ref["admitad_11"]
    assert c.code == "SAVE15" and c.merchant_name == "Myntra"
    assert c.source_url == "https://ad.admitad.com/g/abc/"   # affiliate deeplink
    assert c.discount_type is DiscountType.percentage and c.discount_value == 15

    deal = by_ref["admitad_12"]
    assert deal.code is None and deal.merchant_name == "AJIO"  # no promocode -> deal


def test_auth_error_on_bad_token_response():
    with pytest.raises(AuthError):
        AdmitadFeedScraper(client_id="c", client_secret="s",
                           session=_FakeSession(token={})).scrape()


def test_missing_credentials_raises():
    with pytest.raises(MissingCredentials):
        AdmitadFeedScraper(client_id="", client_secret="", session=_FakeSession()).scrape()


def test_website_id_scopes_coupons_url():
    s = AdmitadFeedScraper(client_id="c", client_secret="s", website_id="98765",
                           session=_FakeSession())
    assert s.coupons_url.endswith("/coupons/website/98765/")


def test_auto_discovers_active_website_for_codes_and_links():
    sites = [{"id": 111, "name": "old", "status": "suspended"},
             {"id": 222, "name": "couponlive.in", "status": "active"}]
    s = AdmitadFeedScraper(client_id="c", client_secret="s",
                           session=_FakeSession(websites=sites))
    out = s.scrape()
    assert s.website_id == "222"
    coupon_gets = [c for c in s.session.calls if c[0] == "GET" and "/coupons/" in c[1]]
    assert coupon_gets and all(c[1].endswith("/coupons/website/222/") for c in coupon_gets)
    assert out  # mapping still works on the website-scoped feed


def test_no_websites_falls_back_to_bare_coupons_list():
    s = AdmitadFeedScraper(client_id="c", client_secret="s", session=_FakeSession())
    s.scrape()
    assert s.website_id == ""
    assert any(c[1].endswith("/coupons/") for c in s.session.calls if c[0] == "GET")


def test_merchant_name_tags_stripped_and_end_date_mapped():
    from datetime import datetime, timezone

    page = {"results": [{"id": 9, "name": "Rs 2000 off", "promocode": "LAP2K",
                         "status": "active", "campaign": {"name": "Acer [CPS] IN"},
                         "goto_link": "https://ad.admitad.com/g/z/",
                         "date_end": "2026-12-31 23:59:00"}],
            "_meta": {"count": 1}}
    out = _scraper(pages={0: page}).scrape()
    assert out[0].merchant_name == "Acer"
    assert out[0].expires_at == datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
