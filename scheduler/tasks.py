"""Celery tasks + beat schedule for scraping and validation.

Priority model (Redis broker priorities; lower number = higher priority):
- newly-scraped codes are validated first (priority 0),
- top-merchant re-validation runs periodically (priority 1),
- scraping runs on each source's cadence.

The task bodies are thin: real logic lives in scrapers.pipeline and
scheduler.validation, so it stays unit-testable without a broker.
"""
from __future__ import annotations

from celery.schedules import crontab

from core.logging import get_logger
from models.base import get_sessionmaker
from scheduler.celery_app import celery_app
from scheduler.validation import record_validation_result, select_coupons_to_validate
from scrapers.pipeline import ingest_raw
from scrapers.registry import get_scraper
from validators.registry import get_validator

log = get_logger("tasks")


@celery_app.task(name="scrape_source")
def scrape_source(source_name: str) -> dict:
    session = get_sessionmaker()()
    try:
        scraper = get_scraper(source_name)
        raw = scraper.scrape()
        summary = ingest_raw(session, source_name, raw)
        # After ingest, enqueue validation for brand-new codes at top priority.
        for _prio, coupon in select_coupons_to_validate(session, limit=200):
            if coupon.last_validated_at is None:
                validate_coupon.apply_async(args=[coupon.id], priority=0)
        return {"source": source_name, "created": summary.coupons_created,
                "deduped": summary.deduped_count}
    finally:
        session.close()


@celery_app.task(name="validate_coupon", bind=True, max_retries=2, default_retry_delay=60)
def validate_coupon(self, coupon_id: int) -> dict:
    from models.models import Coupon  # local import to keep task module light

    session = get_sessionmaker()()
    try:
        coupon = session.get(Coupon, coupon_id)
        if coupon is None or not coupon.code:
            return {"coupon_id": coupon_id, "skipped": "missing or code-less"}
        validator = get_validator(coupon.merchant.normalized_name)
        if validator is None:
            return {"coupon_id": coupon_id, "skipped": "no validator"}
        result = validator.validate(coupon.code, coupon.merchant.name)
        record_validation_result(session, coupon, result)
        session.commit()
        return {"coupon_id": coupon_id, "result": result.result.value}
    finally:
        session.close()


@celery_app.task(name="expire_stale_coupons")
def expire_stale_coupons_task() -> dict:
    from scheduler.maintenance import expire_stale_coupons

    session = get_sessionmaker()()
    try:
        return {"expired": expire_stale_coupons(session)}
    finally:
        session.close()


@celery_app.task(name="check_source_health")
def check_source_health_task() -> dict:
    from scheduler.maintenance import check_source_staleness

    session = get_sessionmaker()()
    try:
        return {"stale_sources": check_source_staleness(session)}
    finally:
        session.close()


@celery_app.task(name="enqueue_revalidations")
def enqueue_revalidations() -> dict:
    """Beat task: dispatch due re-validations (top-merchant + stale)."""
    session = get_sessionmaker()()
    try:
        dispatched = 0
        for prio, coupon in select_coupons_to_validate(session, limit=500):
            validate_coupon.apply_async(args=[coupon.id], priority=prio)
            dispatched += 1
        return {"dispatched": dispatched}
    finally:
        session.close()


# --- Beat schedule -------------------------------------------------------
celery_app.conf.beat_schedule = {
    # Re-validation sweep: dispatch due coupons periodically.
    "enqueue-revalidations": {
        "task": "enqueue_revalidations",
        "schedule": crontab(minute="*/30"),
    },
    # Per-source scrape scheduling is registered dynamically from the `sources`
    # table in a fuller build; a static example for the first source:
    "scrape-desidime": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour="*/6"),
        "args": ("Desidime",),
    },
    # Expire stale, low-confidence coupons so they stop being served by default.
    "expire-stale-coupons": {
        "task": "expire_stale_coupons",
        "schedule": crontab(minute=15, hour="*"),
    },
    # Early-warning sweep: alert if any source has gone stale.
    "check-source-health": {
        "task": "check_source_health",
        "schedule": crontab(minute="*/30"),
    },
}
