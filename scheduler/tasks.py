"""Celery tasks + beat schedule for scraping and validation.

Priority model (Redis broker priorities; lower number = higher priority):
- newly-scraped codes are validated first (priority 0),
- top-merchant re-validation runs periodically (priority 1),
- scraping runs on each source's cadence.

The task bodies are thin: real logic lives in scrapers.pipeline and
scheduler.validation, so it stays unit-testable without a broker.
"""
from __future__ import annotations

from datetime import timedelta

from celery.schedules import crontab
from sqlalchemy import or_, select

from core.config import get_settings
from core.logging import get_logger
from models.base import get_sessionmaker, utcnow
from scheduler.celery_app import celery_app
from scheduler.validation import record_validation_result, select_coupons_to_validate
from scrapers.pipeline import expire_suspended, ingest_raw
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
        # After ingest, enqueue validation for brand-new codes at top priority
        # (only when checkout validation is explicitly enabled).
        if get_settings().validation_enabled:
            for _prio, coupon in select_coupons_to_validate(session, limit=200):
                if coupon.last_validated_at is None:
                    validate_coupon.apply_async(args=[coupon.id], priority=0)
        return {"source": source_name, "created": summary.coupons_created,
                "deduped": summary.deduped_count}
    finally:
        session.close()


@celery_app.task(name="sync_linkmydeals")
def sync_linkmydeals() -> dict:
    """Incremental LinkMyDeals sync: new/updated -> pipeline; suspended -> expired.

    Reads/advances the persisted cursor (sources.sync_cursor). The run timestamp
    is captured BEFORE the pull so offers changed mid-pull aren't missed next time.
    """
    import time

    from models.models import Source
    from scrapers.linkmydeals_feed import LinkMyDealsFeedScraper, MissingCredentials

    session = get_sessionmaker()()
    try:
        source = session.scalar(select(Source).where(Source.name == "LinkMyDeals"))
        cursor = source.sync_cursor if source else None
        run_ts = int(time.time())

        scraper = LinkMyDealsFeedScraper(last_extract=int(cursor) if cursor else None)
        try:
            active = scraper.scrape()
        except MissingCredentials as exc:
            log.warning("linkmydeals.skipped", reason=str(exc))
            return {"source": "LinkMyDeals", "skipped": "no api key"}

        summary = ingest_raw(session, "LinkMyDeals", active)
        expired = expire_suspended(session, scraper.suspended)

        # Advance the cursor so the next run pulls incrementally.
        source = session.scalar(select(Source).where(Source.name == "LinkMyDeals"))
        if source is not None:
            source.sync_cursor = str(run_ts)
            session.commit()

        # New codes still go through checkout validation like any other source
        # (when it's enabled — otherwise affiliate-trust already made them valid).
        if get_settings().validation_enabled:
            for _prio, coupon in select_coupons_to_validate(session, limit=500):
                if coupon.last_validated_at is None:
                    validate_coupon.apply_async(args=[coupon.id], priority=0)

        return {"source": "LinkMyDeals", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "expired": expired,
                "incremental": bool(cursor)}
    finally:
        session.close()


@celery_app.task(name="sync_cuelinks")
def sync_cuelinks() -> dict:
    """Full pull of the Cuelinks Offers feed -> pipeline (codes + deals).

    Cuelinks' Offers API returns the current live set each call (no incremental
    cursor like LinkMyDeals), so we ingest the whole batch; the pipeline dedupes
    and advances last_seen. New codes for validator-backed merchants get picked
    up by the periodic `enqueue_revalidations` sweep.
    """
    from scrapers.cuelinks_feed import CuelinksFeedScraper, MissingCredentials

    session = get_sessionmaker()()
    try:
        scraper = CuelinksFeedScraper()
        try:
            offers = scraper.scrape()
        except MissingCredentials as exc:
            log.warning("cuelinks.skipped", reason=str(exc))
            return {"source": "Cuelinks", "skipped": "no api key"}

        summary = ingest_raw(session, "Cuelinks", offers)
        return {"source": "Cuelinks", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "raw": summary.raw_count,
                "errors": len(summary.errors),
                "sample_error": summary.errors[0] if summary.errors else None}
    finally:
        session.close()


@celery_app.task(name="sync_feedico")
def sync_feedico() -> dict:
    """Full pull of the Feedico coupon catalog -> pipeline (codes; discovery-only,
    no affiliate tracking link). Free tier is 1000 req/mo, so this runs on a slow
    cadence (see FEEDICO_SYNC_FREQUENCY_MINUTES)."""
    from scrapers.feedico_feed import FeedicoFeedScraper, MissingCredentials

    session = get_sessionmaker()()
    try:
        try:
            offers = FeedicoFeedScraper().scrape()
        except MissingCredentials as exc:
            log.warning("feedico.skipped", reason=str(exc))
            return {"source": "Feedico", "skipped": "no api key"}

        summary = ingest_raw(session, "Feedico", offers)
        return {"source": "Feedico", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "raw": summary.raw_count,
                "errors": len(summary.errors),
                "sample_error": summary.errors[0] if summary.errors else None}
    finally:
        session.close()


@celery_app.task(name="extract_tool_trials")
def extract_tool_trials(tool_id: int) -> dict:
    """LLM-extract + refresh trial facts for one tool (Free Trials T2)."""
    from core.llm import LLMUnavailable, add_tokens_today
    from models.models import Tool
    from scrapers.trial_extraction import extract_for_tool

    session = get_sessionmaker()()
    try:
        tool = session.get(Tool, tool_id)
        if tool is None:
            return {"error": "tool not found", "tool_id": tool_id}
        try:
            result = extract_for_tool(session, tool)
        except LLMUnavailable as exc:
            log.warning("extract.skipped", reason=str(exc))
            return {"tool_id": tool_id, "skipped": "no llm key"}
        add_tokens_today(int(result.get("tokens", 0)))
        return result
    finally:
        session.close()


@celery_app.task(name="extract_due_trials")
def extract_due_trials() -> dict:
    """Beat sweep: extract trial facts for tools that need it, within the daily
    token budget and a per-run cap. Content-hash gating makes unchanged pages
    free (no LLM call)."""
    from core.llm import LLMUnavailable, add_tokens_today, budget_remaining
    from models.enums import TrialStatus
    from models.models import Tool
    from scrapers.trial_extraction import extract_for_tool

    settings = get_settings()
    if not (settings.emergent_llm_key or settings.gemini_api_key or settings.openai_api_key):
        return {"skipped": "no llm key"}

    session = get_sessionmaker()()
    try:
        tools = session.scalars(select(Tool).where(Tool.status == TrialStatus.live)).all()
        processed = tokens = 0
        for tool in tools:
            if processed >= 20 or budget_remaining() <= 0:
                break
            try:
                result = extract_for_tool(session, tool)
            except LLMUnavailable:
                break
            used = int(result.get("tokens", 0))
            if used:
                add_tokens_today(used)
                tokens += used
                processed += 1
        return {"tools_processed": processed, "tokens": tokens,
                "budget_remaining": budget_remaining()}
    finally:
        session.close()


@celery_app.task(name="verify_trial")
def verify_trial(offer_id: int) -> dict:
    """Live-verify one trial offer (Free Trials T3)."""
    from models.models import TrialOffer
    from validators.trial_verifier import record_verification, verify_offer

    if not get_settings().trial_verification_enabled:
        return {"offer_id": offer_id, "skipped": "trial verification disabled"}
    session = get_sessionmaker()()
    try:
        offer = session.get(TrialOffer, offer_id)
        if offer is None:
            return {"error": "offer not found", "offer_id": offer_id}
        outcome = verify_offer(offer, offer.tool.name)
        record_verification(offer, outcome)
        session.commit()
        return {"offer_id": offer_id, "status": offer.verification_status.value,
                "confidence": offer.confidence_score, "tier": outcome.highest_tier}
    finally:
        session.close()


@celery_app.task(name="verify_due_trials")
def verify_due_trials() -> dict:
    """Beat sweep: live-verify trial offers that are new or gone stale.
    Opt-in via TRIAL_VERIFICATION_ENABLED; capped per run (browser + LLM cost)."""
    from models.enums import TrialStatus
    from models.models import Tool, TrialOffer
    from validators.trial_verifier import record_verification, verify_offer

    settings = get_settings()
    if not settings.trial_verification_enabled:
        return {"skipped": "trial verification disabled"}

    session = get_sessionmaker()()
    try:
        cutoff = utcnow() - timedelta(hours=settings.trial_reverify_hours)
        stmt = (
            select(TrialOffer)
            .join(Tool)
            .where(
                TrialOffer.status == TrialStatus.live,
                Tool.status == TrialStatus.live,
                or_(TrialOffer.last_verified_at.is_(None), TrialOffer.last_verified_at < cutoff),
            )
            .order_by(TrialOffer.last_verified_at.asc().nulls_first())
            .limit(15)
        )
        offers = session.scalars(stmt).all()
        by_status: dict[str, int] = {}
        for offer in offers:
            outcome = verify_offer(offer, offer.tool.name)
            record_verification(offer, outcome)
            key = offer.verification_status.value
            by_status[key] = by_status.get(key, 0) + 1
        session.commit()
        return {"verified_run": len(offers), "by_status": by_status}
    finally:
        session.close()


@celery_app.task(name="validate_coupon", bind=True, max_retries=2, default_retry_delay=60)
def validate_coupon(self, coupon_id: int) -> dict:
    from models.models import Coupon  # local import to keep task module light

    if not get_settings().validation_enabled:
        return {"coupon_id": coupon_id, "skipped": "validation disabled"}
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
    if not get_settings().validation_enabled:
        return {"dispatched": 0, "skipped": "validation disabled"}
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
    # LinkMyDeals incremental feed sync (API call — runs on its own cadence).
    "sync-linkmydeals": {
        "task": "sync_linkmydeals",
        "schedule": timedelta(minutes=get_settings().linkmydeals_sync_frequency_minutes),
    },
    # Cuelinks Offers feed sync (coupons + deals across 400+ merchants).
    "sync-cuelinks": {
        "task": "sync_cuelinks",
        "schedule": timedelta(minutes=get_settings().cuelinks_sync_frequency_minutes),
    },
    # Feedico coupon-catalog sync (slow cadence — free tier is 1000 req/month).
    "sync-feedico": {
        "task": "sync_feedico",
        "schedule": timedelta(minutes=get_settings().feedico_sync_frequency_minutes),
    },
    # Free Trials: LLM-extract/refresh trial facts (no-op until an LLM key is set).
    "extract-trials": {
        "task": "extract_due_trials",
        "schedule": timedelta(minutes=get_settings().trial_extract_frequency_minutes),
    },
    # Free Trials: live verify trial links (no-op until TRIAL_VERIFICATION_ENABLED).
    "verify-trials": {
        "task": "verify_due_trials",
        "schedule": timedelta(minutes=get_settings().trial_verify_frequency_minutes),
    },
}
