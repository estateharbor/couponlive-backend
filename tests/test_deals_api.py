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


def test_editorial_deals_rank_before_newer_feed_deals(db_session):
    from datetime import timedelta

    from scheduler.import_editorial import SOURCE_NAME

    ajio = Merchant(name="AJIO", normalized_name="ajio")
    db_session.add(ajio)
    feed = Source(name="vCommission", ingestion_method=IngestionMethod.affiliate_api)
    editorial = Source(name=SOURCE_NAME, ingestion_method=IngestionMethod.affiliate_api)
    db_session.add_all([feed, editorial])
    db_session.flush()

    old = _now() - timedelta(days=2)
    bank = Coupon(merchant_id=ajio.id, code=None, external_ref="bank",
                  description="10% off with HSBC cards", status=CouponStatus.unverified,
                  first_seen=old, last_seen=old)
    feed_deals = [
        Coupon(merchant_id=ajio.id, code=None, external_ref=f"f{i}",
               description=f"Feed deal {i}", status=CouponStatus.unverified,
               first_seen=_now(), last_seen=_now())
        for i in range(3)
    ]
    db_session.add_all([bank, *feed_deals])
    db_session.flush()
    db_session.add(CouponSource(coupon_id=bank.id, source_id=editorial.id,
                                first_seen_at=old, last_seen_at=old))
    for d in feed_deals:
        db_session.add(CouponSource(coupon_id=d.id, source_id=feed.id,
                                    first_seen_at=_now(), last_seen_at=_now()))
    db_session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    body = TestClient(app).get("/deals", params={"merchant": "ajio", "limit": 2}).json()
    # The older editorial deal leads even though every feed deal is newer.
    assert body[0]["description"] == "10% off with HSBC cards"
    assert len(body) == 2


def test_deals_merchant_filter(client):
    amazon = {d["merchant_name"] for d in client.get("/deals", params={"merchant": "amazon"}).json()}
    assert amazon == {"Amazon"}                # Flipkart deal filtered out
