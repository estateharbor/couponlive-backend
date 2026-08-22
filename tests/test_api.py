"""API tests (Phase 5) with a SQLite-backed TestClient."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import create_app
from models.enums import CouponStatus
from models.models import Coupon, Merchant, Source, ValidationLog
from models.enums import IngestionMethod, ValidationResultEnum


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def client(db_session):
    # Seed merchants
    myntra = Merchant(name="Myntra", normalized_name="myntra", priority=5)
    amazon = Merchant(name="Amazon", normalized_name="amazon", priority=0)
    db_session.add_all([myntra, amazon])
    db_session.flush()

    fresh = Coupon(merchant_id=myntra.id, code="FRESH20", status=CouponStatus.valid,
                   confidence_score=0.9, first_seen=_now(), last_seen=_now(),
                   last_validated_at=_now())
    stale = Coupon(merchant_id=myntra.id, code="STALE10", status=CouponStatus.valid,
                   confidence_score=0.8, first_seen=_now(), last_seen=_now(),
                   last_validated_at=_now() - timedelta(hours=10))
    bad = Coupon(merchant_id=amazon.id, code="BADCODE", status=CouponStatus.invalid,
                 confidence_score=0.05, first_seen=_now(), last_seen=_now(),
                 last_validated_at=_now())
    db_session.add_all([fresh, stale, bad])
    db_session.flush()
    db_session.add(ValidationLog(coupon_id=fresh.id, validated_at=_now(),
                                 result=ValidationResultEnum.valid))
    db_session.add(Source(name="Desidime", ingestion_method=IngestionMethod.scrape_requests,
                          last_scraped_at=_now(), last_success_at=_now(), last_success_rate=1.0))
    db_session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_default_coupons_only_fresh_valid(client):
    r = client.get("/coupons")
    assert r.status_code == 200
    codes = [c["code"] for c in r.json()]
    assert codes == ["FRESH20"]           # stale + invalid excluded, sorted by confidence


def test_include_stale_returns_both_valid(client):
    r = client.get("/coupons", params={"include_stale": True})
    codes = {c["code"] for c in r.json()}
    assert codes == {"FRESH20", "STALE10"}


def test_filter_by_merchant(client):
    r = client.get("/coupons", params={"merchant": "MYNTRA", "include_stale": True})
    assert {c["code"] for c in r.json()} == {"FRESH20", "STALE10"}
    assert all(c["merchant_name"] == "Myntra" for c in r.json())


def test_filter_by_status_invalid(client):
    r = client.get("/coupons", params={"status": "invalid"})
    assert [c["code"] for c in r.json()] == ["BADCODE"]


def test_merchants_counts(client):
    r = client.get("/merchants")
    by_name = {m["name"]: m for m in r.json()}
    assert by_name["Myntra"]["coupon_count"] == 2
    assert by_name["Myntra"]["valid_coupon_count"] == 1   # only the fresh one
    assert by_name["Amazon"]["coupon_count"] == 1
    assert by_name["Amazon"]["valid_coupon_count"] == 0


def test_feedback_updates_confidence_and_dedupes(client):
    cid = client.get("/coupons").json()[0]["id"]
    r1 = client.post(f"/coupons/{cid}/feedback", json={"worked": False})
    assert r1.status_code == 200 and r1.json()["recorded"] is True
    lowered = r1.json()["new_confidence_score"]
    assert lowered < 0.9                                  # negative feedback pulled it down

    r2 = client.post(f"/coupons/{cid}/feedback", json={"worked": False})
    assert r2.json()["recorded"] is False                 # same IP within 24h -> not double-counted


def test_feedback_404(client):
    assert client.post("/coupons/999999/feedback", json={"worked": True}).status_code == 404


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["total_coupons"] == 3
    assert body["total_valid_coupons"] == 1
    assert any(s["name"] == "Desidime" for s in body["sources"])
