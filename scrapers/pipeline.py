"""Ingestion pipeline: raw scraper output -> normalized -> deduped -> Postgres.

`ingest_raw` is the single entry point every source funnels through. It is
DB-session-injected so it can run against Postgres in prod or SQLite in tests.
It also records per-source run bookkeeping (last_scraped_at / last_success_at /
last_success_rate) that Phase 6 alerting keys off.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.alerting import alert_scrape_result
from core.logging import get_logger
from models.base import utcnow
from models.enums import CouponStatus, IngestionMethod
from models.models import Coupon, CouponSource, Merchant, Source
from models.schemas import RawCoupon
from scrapers.normalize import NormalizedCoupon, normalize_and_dedupe

log = get_logger("ingest")


def _aware(dt: datetime) -> datetime:
    """Coerce to tz-aware UTC. Backends like SQLite drop tzinfo on read, so we
    normalize before any min/max comparison to avoid naive/aware mismatches."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@dataclass
class IngestSummary:
    source: str
    raw_count: int = 0
    deduped_count: int = 0
    coupons_created: int = 0
    coupons_updated: int = 0
    provenance_created: int = 0
    merchants_created: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.raw_count == 0:
            return 0.0
        return round(self.deduped_count / self.raw_count, 4)


def _get_or_create_source(
    session: Session, name: str, method: IngestionMethod
) -> Source:
    src = session.scalar(select(Source).where(Source.name == name))
    if src is None:
        src = Source(name=name, ingestion_method=method)
        session.add(src)
        session.flush()
    elif src.ingestion_method != method:
        # Keep the source row's method in sync with how it actually ingested.
        src.ingestion_method = method
    return src


def _get_or_create_merchant(
    session: Session, canonical_name: str, normalized: str, summary: IngestSummary
) -> Merchant:
    m = session.scalar(
        select(Merchant).where(Merchant.normalized_name == normalized)
    )
    if m is None:
        m = Merchant(name=canonical_name, normalized_name=normalized)
        session.add(m)
        session.flush()
        summary.merchants_created += 1
    return m


def _find_coupon(session: Session, merchant_id: int, nc: NormalizedCoupon) -> Coupon | None:
    stmt = select(Coupon).where(Coupon.merchant_id == merchant_id)
    if nc.code:
        stmt = stmt.where(Coupon.code == nc.code)
    else:
        stmt = stmt.where(Coupon.code.is_(None), Coupon.external_ref == nc.external_ref)
    return session.scalar(stmt)


def _upsert_coupon(
    session: Session, source: Source, nc: NormalizedCoupon, summary: IngestSummary
) -> None:
    merchant = _get_or_create_merchant(
        session, nc.merchant_name, nc.normalized_merchant, summary
    )
    coupon = _find_coupon(session, merchant.id, nc)

    if coupon is None:
        coupon = Coupon(
            merchant_id=merchant.id,
            code=nc.code,
            external_ref=nc.external_ref,
            requires_reveal=nc.requires_reveal,
            description=nc.description,
            discount_type=nc.discount_type,
            discount_value=nc.discount_value,
            first_seen=nc.first_seen,
            last_seen=nc.last_seen,
            status=CouponStatus.unverified,
        )
        session.add(coupon)
        session.flush()
        summary.coupons_created += 1
    else:
        # Keep earliest first_seen, advance last_seen, backfill missing fields.
        coupon.first_seen = min(_aware(coupon.first_seen), _aware(nc.first_seen))
        coupon.last_seen = max(_aware(coupon.last_seen), _aware(nc.last_seen))
        if not coupon.description and nc.description:
            coupon.description = nc.description
        if coupon.discount_value is None and nc.discount_value is not None:
            coupon.discount_value = nc.discount_value
        summary.coupons_updated += 1

    _upsert_provenance(session, source, coupon, nc, summary)


def _upsert_provenance(
    session: Session,
    source: Source,
    coupon: Coupon,
    nc: NormalizedCoupon,
    summary: IngestSummary,
) -> None:
    link = session.scalar(
        select(CouponSource).where(
            CouponSource.coupon_id == coupon.id,
            CouponSource.source_id == source.id,
        )
    )
    # Representative provenance timestamps/url from this source's rows.
    src_url = nc.sources[0][0] if nc.sources else None
    if link is None:
        session.add(
            CouponSource(
                coupon_id=coupon.id,
                source_id=source.id,
                source_url=src_url,
                first_seen_at=nc.first_seen,
                last_seen_at=nc.last_seen,
            )
        )
        summary.provenance_created += 1
    else:
        link.first_seen_at = min(_aware(link.first_seen_at), _aware(nc.first_seen))
        link.last_seen_at = max(_aware(link.last_seen_at), _aware(nc.last_seen))
        if src_url:
            link.source_url = src_url


def ingest_raw(
    session: Session, source_name: str, raw: list[RawCoupon], *, commit: bool = True
) -> IngestSummary:
    """Normalize, dedupe, and upsert a raw batch; update source bookkeeping."""
    summary = IngestSummary(source=source_name, raw_count=len(raw))
    # A source's rows are homogeneous in ingestion method; take it from the batch.
    method = raw[0].ingestion_method if raw else IngestionMethod.scrape_requests
    source = _get_or_create_source(session, source_name, method)
    source.last_scraped_at = utcnow()

    deduped = normalize_and_dedupe(raw)
    summary.deduped_count = len(deduped)

    for nc in deduped:
        try:
            _upsert_coupon(session, source, nc, summary)
        except Exception as exc:  # isolate a bad row; don't fail the whole batch
            session.rollback()
            summary.errors.append(f"{nc.identity}: {exc}")
            log.warning("ingest.row_failed", identity=str(nc.identity), error=str(exc))

    # A run that parsed >0 rows counts as a success for health tracking.
    if summary.raw_count > 0 and not summary.errors:
        source.last_success_at = utcnow()
    source.last_success_rate = summary.success_rate

    # Phase 6 early-warning: zero results / low success rate => likely breakage.
    alert_scrape_result(source_name, summary.raw_count, summary.success_rate)

    if commit:
        session.commit()

    log.info(
        "ingest.done",
        source=source_name,
        raw=summary.raw_count,
        deduped=summary.deduped_count,
        created=summary.coupons_created,
        updated=summary.coupons_updated,
        merchants_created=summary.merchants_created,
        errors=len(summary.errors),
    )
    return summary
