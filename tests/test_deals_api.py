"""API tests for the /deals endpoint (code-less offers, e.g. Amazon deals)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import create_app
from models.enums import CouponStatus, IngestionMethod
from models.models import Coupon, CouponSource, Merchant, Source


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def client(db_session):
    amazon = Merchant(name="Amazon", normalized_name="amazon")
    flipkart = Merchant(name="Flipkart", normalized_name="flipkart")
    db_session.add_all([amazon, flipkart])
    db_session.flush()
    src = Source(name="Cuelinks", ingestion_method=IngestionMethod.affiliate_api)
    db_session.add(src)
    db_session.flush()

    # A code-less Amazon deal (should appear, with its affiliate url).
    deal = Coupon(merchant_id=amazon.id, code=None, external_ref="d1",
                  description="Up to 60% Off Fashion", status=CouponStatus.unverified,
                  first_seen=_now(), last_seen=_now())
    # A coded Amazon coupon (should NOT appear in /deals).
    coded = Coupon(merchant_id=amazon.id, code="AMZ40", external_ref="d2",
                   description="Flat 40% Off", status=CouponStatus.unverified,
                   first_seen=_now(), last_seen=_now())
    # An expired code-less Amazon deal (excluded).
    expired = Coupon(merchant_id=amazon.id, code=None, external_ref="d3",
                     description="Old deal", status=CouponStatus.expired,
                     first_seen=_now(), last_seen=_now())
    # A Flipkart deal (excluded by merchant filter).
    flip = Coupon(merchant_id=flipkart.id, code=None, external_ref="d4",
                  description="Flipkart deal", status=CouponStatus.unverified,
                  first_seen=_now(), last_seen=_now())
    db_session.add_all([deal, coded, expired, flip])
    db_session.flush()
    db_session.add(CouponSource(coupon_id=deal.id, source_id=src.id,
                                source_url="https://cue/amazon-deal",
                                first_seen_at=_now(), last_seen_at=_now()))
    db_session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_deals_returns_codeless_with_url(client):
    r = client.get("/deals", params={"merchant": "amazon"})
    assert r.status_code == 200
    body = r.json()
    assert [d["description"] for d in body] == ["Up to 60% Off Fashion"]
    assert body[0]["url"] == "https://cue/amazon-deal"


def test_deals_exclude_coded_and_expired(client):
    refs = {d["description"] for d in client.get("/deals").json()}
    assert "Flat 40% Off" not in refs          # coded coupon excluded
    assert "Old deal" not in refs              # expired excluded


def test_deals_merchant_filter(client):
    amazon = {d["merchant_name"] for d in client.get("/deals", params={"merchant": "amazon"}).json()}
    assert amazon == {"Amazon"}                # Flipkart deal filtered out
