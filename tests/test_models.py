"""Phase 1 schema coherence tests.

Runs the ORM models against in-memory SQLite (no Postgres required) to prove:
- all tables/relationships build and create cleanly,
- the (merchant_id, code) unique constraint holds,
- the confidence-score check constraint is enforced,
- cascade delete removes a coupon's provenance/logs/feedback,
- RawCoupon / ValidationResult schemas validate.

Postgres-specific behaviour (native enums, the composite serve index) is
exercised by the Alembic migration against real Postgres, not here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from models.base import Base
from models.enums import (
    CouponStatus,
    DiscountType,
    IngestionMethod,
    ValidationResultEnum,
)
from models.models import (
    Coupon,
    CouponSource,
    Merchant,
    Source,
    UserFeedback,
    ValidationLog,
)
from models.schemas import RawCoupon, ValidationResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    # SQLite ignores FK/cascade by default; turn it on so the cascade test is real.
    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_con, _):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with Session() as s:
        yield s


def _seed_coupon(session) -> Coupon:
    merchant = Merchant(name="Myntra", normalized_name="myntra", website="https://myntra.com")
    source = Source(name="Desidime", base_url="https://desidime.com",
                    ingestion_method=IngestionMethod.scrape_requests)
    session.add_all([merchant, source])
    session.flush()

    coupon = Coupon(
        merchant_id=merchant.id,
        code="SAVE20",
        description="20% off",
        discount_type=DiscountType.percentage,
        discount_value=20,
        first_seen=_now(),
        last_seen=_now(),
        status=CouponStatus.unverified,
    )
    session.add(coupon)
    session.flush()

    session.add(CouponSource(
        coupon_id=coupon.id, source_id=source.id,
        source_url="https://desidime.com/x", first_seen_at=_now(), last_seen_at=_now(),
    ))
    session.commit()
    return coupon


def test_tables_build_and_basic_insert(session):
    coupon = _seed_coupon(session)
    fetched = session.get(Coupon, coupon.id)
    assert fetched is not None
    assert fetched.merchant.normalized_name == "myntra"
    assert len(fetched.sources) == 1
    assert fetched.status is CouponStatus.unverified


def test_unique_merchant_code(session):
    coupon = _seed_coupon(session)
    dup = Coupon(
        merchant_id=coupon.merchant_id, code="SAVE20",
        first_seen=_now(), last_seen=_now(),
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()


def test_confidence_score_check_constraint(session):
    coupon = _seed_coupon(session)
    bad = Coupon(
        merchant_id=coupon.merchant_id, code="TOOHIGH",
        first_seen=_now(), last_seen=_now(), confidence_score=1.5,
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()


def test_cascade_delete_cleans_provenance_and_logs(session):
    coupon = _seed_coupon(session)
    session.add(ValidationLog(
        coupon_id=coupon.id, validated_at=_now(),
        result=ValidationResultEnum.valid, response_snapshot="₹200 applied",
    ))
    session.add(UserFeedback(coupon_id=coupon.id, worked=True, submitted_at=_now(), ip_hash="abc"))
    session.commit()

    session.delete(session.get(Coupon, coupon.id))
    session.commit()

    assert session.scalars(select(CouponSource)).all() == []
    assert session.scalars(select(ValidationLog)).all() == []
    assert session.scalars(select(UserFeedback)).all() == []


def test_raw_coupon_schema_roundtrips():
    raw = RawCoupon(
        merchant_name="Nykaa", code=" save10 ", discount_type=DiscountType.percentage,
        discount_value=10, source_url="https://x", scraped_at=_now(),
    )
    assert raw.code == " save10 "  # normalization is the dedup layer's job, not the schema's
    assert raw.ingestion_method is IngestionMethod.scrape_requests


def test_validation_result_schema():
    vr = ValidationResult(result=ValidationResultEnum.invalid,
                          error_message="code rejected", checked_at=_now())
    assert vr.result is ValidationResultEnum.invalid
