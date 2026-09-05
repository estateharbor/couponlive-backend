"""Regression: one failing row must not discard the whole ingest batch.

Before the per-row SAVEPOINT fix, a single row that raised triggered
`session.rollback()`, wiping every already-processed row in the batch AND the
source row — so one bad offer silently dropped an entire feed (observed live
when a Cuelinks row hit a Postgres constraint and 0 of ~270 offers persisted).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

import scrapers.pipeline as pipeline
from models.models import Coupon, Source
from models.schemas import RawCoupon
from scrapers.pipeline import ingest_raw


def _raw(code: str, ref: str) -> RawCoupon:
    return RawCoupon(
        merchant_name=f"Store {ref}", code=code, external_ref=ref,
        scraped_at=datetime.now(timezone.utc),
    )


def test_one_bad_row_keeps_the_rest(db_session, monkeypatch):
    batch = [_raw("AAA", "1"), _raw("BOOM", "2"), _raw("CCC", "3")]

    original = pipeline._upsert_coupon

    def flaky(session, source, nc, summary):
        if nc.code == "BOOM":
            raise ValueError("simulated constraint violation")
        return original(session, source, nc, summary)

    monkeypatch.setattr(pipeline, "_upsert_coupon", flaky)
    summary = ingest_raw(db_session, "TestSrc", batch)

    # Good rows persisted; the bad one is isolated, not fatal.
    codes = {c.code for c in db_session.scalars(select(Coupon)).all()}
    assert codes == {"AAA", "CCC"}
    assert summary.coupons_created == 2          # counter not inflated by the failure
    assert len(summary.errors) == 1

    # The source row survives (the old bug rolled it back too).
    assert db_session.scalar(select(Source).where(Source.name == "TestSrc")) is not None
