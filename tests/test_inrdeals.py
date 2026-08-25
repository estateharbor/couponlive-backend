"""Tests for the INRDeals affiliate-API ingestor (mocked HTTP — no key needed).

Proves the ingestor maps a coupon-feed payload into RawCoupons WITH codes and
flows through the same pipeline, without requiring live credentials.
"""
from __future__ import annotations

import pytest

from models.enums import DiscountType, IngestionMethod
from scrapers.inrdeals import InrdealsIngestor, MissingCredentials, _extract_items


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


# Mirrors the real INRDeals coupon-feed shape: result.data[], merchant nested
# under logo.store_name, "label" = discount, "coupon_code" = the code.
SAMPLE = {
    "result": {
        "current_page": 1,
        "data": [
            {"id": 1001, "url": "https://inr.deals/track?...=flipkart",
             "label": "Flat ₹200 OFF", "offer": "Flat ₹200 off on first order",
             "coupon_code": "FKNEW200", "logo": {"id": 65, "store_name": "Flipkart"}},
            {"id": 1002, "url": "https://inr.deals/track?...=myntra",
             "label": "20% OFF", "offer": "20% off on fashion",
             "coupon_code": "MYNTRA20", "logo": {"id": 193, "store_name": "Myntra"}},
            {"id": 1003, "label": "Deal", "offer": "Deal without a code",
             "coupon_code": "", "logo": {"id": 9, "store_name": "SomeDeal"}},  # dropped
        ],
        "next_page_url": None,
    },
}


def test_maps_feed_to_coupons_with_codes():
    ing = InrdealsIngestor(token="t", username="u", store_ids=[65],
                           session=_FakeSession(SAMPLE))
    rows = ing.scrape()
    assert len(rows) == 2  # the code-less deal is dropped

    by_code = {r.code: r for r in rows}
    assert set(by_code) == {"FKNEW200", "MYNTRA20"}
    assert all(r.requires_reveal is False for r in rows)          # feed gives real codes
    assert all(r.ingestion_method is IngestionMethod.affiliate_api for r in rows)

    fk = by_code["FKNEW200"]
    assert fk.merchant_name == "Flipkart"
    assert fk.discount_type is DiscountType.fixed and fk.discount_value == 200
    assert fk.external_ref == "1001"

    my = by_code["MYNTRA20"]
    assert my.discount_type is DiscountType.percentage and my.discount_value == 20


def test_sends_credentials_and_store_id():
    sess = _FakeSession(SAMPLE)
    InrdealsIngestor(token="secret", username="user1", store_ids=[65], session=sess).scrape()
    url, params = sess.calls[0]
    assert params == {"token": "secret", "id": "user1", "store_id": 65}


def test_missing_credentials_raises():
    with pytest.raises(MissingCredentials):
        InrdealsIngestor(token="", username="", session=_FakeSession(SAMPLE)).scrape()


@pytest.mark.parametrize("payload,expected", [
    ([{"a": 1}], 1),
    ({"data": [{"a": 1}, {"b": 2}]}, 2),
    ({"result": {"data": [{"a": 1}]}}, 1),       # real INRDeals shape
    ({"result": {"coupons": [{"a": 1}]}}, 1),    # nested envelope
    ({"nope": 1}, 0),
])
def test_extract_items_handles_envelopes(payload, expected):
    assert len(_extract_items(payload)) == expected
