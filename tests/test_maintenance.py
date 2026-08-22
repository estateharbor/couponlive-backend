"""Phase 6 maintenance + alerting tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.enums import CouponStatus, IngestionMethod
from models.models import Coupon, Merchant, Source
from scheduler.maintenance import check_source_staleness, expire_stale_coupons


def _now():
    return datetime.now(timezone.utc)


def _merchant(db):
    m = Merchant(name="Myntra", normalized_name="myntra")
    db.add(m); db.flush()
    return m


def test_expire_stale_low_confidence(db_session):
    m = _merchant(db_session)
    # stale + low confidence -> expired
    stale = Coupon(merchant_id=m.id, code="OLD", status=CouponStatus.valid,
                   confidence_score=0.1, first_seen=_now() - timedelta(days=5),
                   last_seen=_now(), last_validated_at=_now() - timedelta(hours=100))
    # fresh + high confidence -> kept
    fresh = Coupon(merchant_id=m.id, code="NEWish", status=CouponStatus.valid,
                   confidence_score=0.9, first_seen=_now(), last_seen=_now(),
                   last_validated_at=_now())
    # brand-new, never validated, low confidence -> NOT expired (too young)
    young = Coupon(merchant_id=m.id, code="YOUNG", status=CouponStatus.unverified,
                   confidence_score=0.0, first_seen=_now(), last_seen=_now(),
                   last_validated_at=None)
    db_session.add_all([stale, fresh, young]); db_session.commit()

    n = expire_stale_coupons(db_session)
    assert n == 1
    db_session.refresh(stale); db_session.refresh(fresh); db_session.refresh(young)
    assert stale.status is CouponStatus.expired
    assert fresh.status is CouponStatus.valid
    assert young.status is CouponStatus.unverified


def test_check_source_staleness_flags_old(db_session):
    db_session.add(Source(name="FreshSrc", ingestion_method=IngestionMethod.scrape_requests,
                          last_success_at=_now()))
    db_session.add(Source(name="StaleSrc", ingestion_method=IngestionMethod.scrape_requests,
                          last_success_at=_now() - timedelta(hours=100)))
    db_session.add(Source(name="NeverSrc", ingestion_method=IngestionMethod.scrape_requests,
                          last_success_at=None))
    db_session.commit()

    stale = set(check_source_staleness(db_session))
    assert "StaleSrc" in stale and "NeverSrc" in stale
    assert "FreshSrc" not in stale


def test_alert_zero_results_logs(monkeypatch):
    from core import alerting
    sent = []
    monkeypatch.setattr(alerting, "send_alert",
                        lambda event, level="warning", **f: sent.append((event, f)))
    alerting.alert_scrape_result("Desidime", raw_count=0, success_rate=0.0)
    assert sent and sent[0][0] == "scrape.zero_results"
