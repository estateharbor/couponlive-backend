"""Tests for the LinkMyDeals feed ingestor + suspended-expiry (mocked HTTP).

Response-shape health check: if LinkMyDeals changes their field names, the
mapping assertions here break loudly — our early warning, same as the live
selector-health checks for HTML scrapers.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from models.enums import CouponStatus, DiscountType, IngestionMethod
from models.models import Coupon, Merchant
from scrapers.linkmydeals_feed import (
    LinkMyDealsFeedScraper,
    MissingCredentials,
    SuspendedOffer,
    _extract_offers,
)
from scrapers.pipeline import expire_suspended


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResp(self.payload)


# Real LinkMyDeals shape: discount is in `offer_text`, `offer_value` is a text
# LABEL (not a number), affiliate deeplink is `smartLink`, status "active".
SAMPLE = {
    "offers": [
        {"lmd_id": "111", "store": "Amazon India", "offer_text": "Get 50% Off on Electronics",
         "title": "50% Off", "code": "SAVE50", "offer": "Percentage Off",
         "offer_value": "Percentage Off", "type": "Code",
         "smartLink": "https://track/111", "status": "active"},
        {"lmd_id": "222", "store": "Flipkart", "offer_text": "Flat ₹200 Off on first order",
         "title": "₹200 Off", "code": "FLAT200", "offer": "Price Off",
         "offer_value": "Price Off", "type": "Code",
         "url": "https://track/222", "status": "active"},
        {"lmd_id": "333", "store": "Myntra", "offer_text": "Free Shipping on all orders",
         "title": "Free Shipping", "code": "", "offer": "Free Shipping",
         "type": "Deal", "smartLink": "https://track/333", "status": "active"},
        {"lmd_id": "444", "store": "Nykaa", "code": "OLD10", "status": "suspended"},
    ],
}


def test_maps_active_offers_and_splits_suspended():
    s = LinkMyDealsFeedScraper(api_key="k", session=_FakeSession(SAMPLE))
    active = s.scrape()

    assert len(active) == 3                    # the suspended one is not "active"
    assert len(s.suspended) == 1

    by_ref = {r.external_ref: r for r in active}
    assert set(by_ref) == {"111", "222", "333"}
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in active)

    amazon = by_ref["111"]
    assert amazon.merchant_name == "Amazon India" and amazon.code == "SAVE50"
    assert amazon.discount_type is DiscountType.percentage and amazon.discount_value == 50
    assert amazon.source_url == "https://track/111"      # smartlink preferred
    assert amazon.requires_reveal is False

    flip = by_ref["222"]
    assert flip.discount_type is DiscountType.fixed and flip.discount_value == 200

    deal = by_ref["333"]
    assert deal.code is None                             # deal kept, code-less
    assert deal.discount_type is DiscountType.free_shipping


def test_suspended_offer_captured():
    s = LinkMyDealsFeedScraper(api_key="k", session=_FakeSession(SAMPLE))
    s.scrape()
    sus = s.suspended[0]
    assert sus.merchant_name == "Nykaa" and sus.code == "OLD10" and sus.external_ref == "444"


def test_incremental_params_sent():
    sess = _FakeSession(SAMPLE)
    LinkMyDealsFeedScraper(api_key="secret", last_extract=1700000000, session=sess).scrape()
    _url, params = sess.calls[0]
    assert params["API_KEY"] == "secret"
    assert params["incremental"] == 1 and params["last_extract"] == 1700000000
    assert params["off_record"] == 1


def test_full_pull_has_no_incremental():
    sess = _FakeSession(SAMPLE)
    LinkMyDealsFeedScraper(api_key="secret", session=sess).scrape()
    _url, params = sess.calls[0]
    assert "incremental" not in params and "last_extract" not in params


def test_missing_key_raises():
    with pytest.raises(MissingCredentials):
        LinkMyDealsFeedScraper(api_key="", session=_FakeSession(SAMPLE)).scrape()


@pytest.mark.parametrize("payload,expected", [
    ({"offers": [{"a": 1}]}, 1),
    ([{"a": 1}, {"b": 2}], 2),
    ({"result": {"offers": [{"a": 1}]}}, 1),   # nested envelope
    ({"nope": 1}, 0),
])
def test_extract_offers_envelopes(payload, expected):
    assert len(_extract_offers(payload)) == expected


def test_expire_suspended_marks_expired(db_session):
    m = Merchant(name="Nykaa", normalized_name="nykaa")
    db_session.add(m); db_session.flush()
    now = datetime.now(timezone.utc)
    coded = Coupon(merchant_id=m.id, code="OLD10", external_ref="444",
                   status=CouponStatus.valid, first_seen=now, last_seen=now)
    dealless = Coupon(merchant_id=m.id, code=None, external_ref="555",
                      status=CouponStatus.valid, first_seen=now, last_seen=now)
    keep = Coupon(merchant_id=m.id, code="KEEP", external_ref="999",
                  status=CouponStatus.valid, first_seen=now, last_seen=now)
    db_session.add_all([coded, dealless, keep]); db_session.commit()

    n = expire_suspended(db_session, [
        SuspendedOffer("Nykaa", "OLD10", "444"),     # match by code
        SuspendedOffer("Nykaa", None, "555"),        # match code-less by external_ref
    ])
    assert n == 2
    db_session.refresh(coded); db_session.refresh(dealless); db_session.refresh(keep)
    assert coded.status is CouponStatus.expired
    assert dealless.status is CouponStatus.expired
    assert keep.status is CouponStatus.valid          # untouched
