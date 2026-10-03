"""Celery tasks + beat schedule for scraping and validation.

Priority model (Redis broker priorities; lower number = higher priority):
- newly-scraped codes are validated first (priority 0),
- top-merchant re-validation runs periodically (priority 1),
- scraping runs on each source's cadence.

The task bodies are thin: real logic lives in scrapers.pipeline and
scheduler.validation, so it stays unit-testable without a broker.
"""
from __future__ import annotations

import functools
import re
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

# /health is public: never let a credential from a request URL or response body
# (LinkMyDeals puts API_KEY in the query string) reach a stored error message.
_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|apikey|key|token|access_token|secret|client_secret|password)"
    r"[\"']?\s*[=:]\s*[\"']?)[^&\s,'\"}]+"
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1REDACTED", text)


# A coupon is only ever queued ONCE at a time. Without this, the 30-min sweep
# re-queued the same up-to-500 coupons every run (they're only marked checked
# after the job runs), so the backlog grew without bound.
_PENDING_KEY = "couponlive:validate:pending:{}"
_PENDING_TTL_SECONDS = 6 * 3600  # safety net if a job dies without clearing it


def _redis():
    import redis

    return redis.Redis.from_url(get_settings().redis_url)


def enqueue_validation(coupon_id: int, priority: int = 0) -> bool:
    """Queue a checkout validation unless one is already pending for this coupon.
    Returns True if queued. Falls back to plain queuing if Redis can't be reached."""
    try:
        if not _redis().set(_PENDING_KEY.format(coupon_id), 1, nx=True,
                            ex=_PENDING_TTL_SECONDS):
            return False
    except Exception as exc:  # noqa: BLE001 — dedupe is best-effort
        log.warning("validate.dedupe_unavailable", error=str(exc))
    validate_coupon.apply_async(args=[coupon_id], priority=priority)
    return True


def _clear_pending(coupon_id: int) -> None:
    try:
        _redis().delete(_PENDING_KEY.format(coupon_id))
    except Exception:  # noqa: BLE001
        pass


def _record_feed_outcome(source_name: str, error: str | None) -> None:
    """Store (or clear) the feed's last error on its Source row for /health."""
    from models.enums import IngestionMethod
    from models.models import Source

    session = get_sessionmaker()()
    try:
        src = session.scalar(select(Source).where(Source.name == source_name))
        if src is None:
            if error is None:
                return
            src = Source(name=source_name, ingestion_method=IngestionMethod.affiliate_api)
            session.add(src)
        if error:
            src.last_error = _redact(error)[:500]
            src.last_error_at = utcnow()
        else:
            src.last_error = None
        session.commit()
    except Exception as exc:  # bookkeeping must never mask the sync's own outcome
        log.warning("feed.record_outcome_failed", source=source_name, error=str(exc))
    finally:
        session.close()


def _tracked_feed(source_name: str):
    """Record a feed sync's failure (exception, auth error, empty feed, missing
    key) on its Source row, and clear it after a clean run. Re-raises exceptions
    so Celery still marks the task failed."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                log.exception("feed.sync_failed", source=source_name)
                _record_feed_outcome(source_name, f"{type(exc).__name__}: {exc}")
                raise
            err = None
            if isinstance(result, dict):
                err = result.get("error") or (
                    f"skipped: {result['skipped']}" if result.get("skipped") else None)
            _record_feed_outcome(source_name, err)
            return result
        return wrapper
    return deco


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
                    enqueue_validation(coupon.id, priority=0)
        return {"source": source_name, "created": summary.coupons_created,
                "deduped": summary.deduped_count}
    finally:
        session.close()


@celery_app.task(name="sync_linkmydeals")
@_tracked_feed("LinkMyDeals")
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
                    enqueue_validation(coupon.id, priority=0)

        return {"source": "LinkMyDeals", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "expired": expired,
                "incremental": bool(cursor)}
    finally:
        session.close()


@celery_app.task(name="sync_cuelinks")
@_tracked_feed("Cuelinks")
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


@celery_app.task(name="sync_involve_asia")
@_tracked_feed("Involve Asia")
def sync_involve_asia() -> dict:
    """Full pull of the Involve Asia Offers feed -> pipeline (codes + deals).

    Two-step auth (key+secret -> token) happens inside the scraper. Like Cuelinks
    it returns the current live set each call, so we ingest the whole batch and
    the pipeline dedupes. New codes for validator-backed merchants get picked up
    by the periodic `enqueue_revalidations` sweep.
    """
    from scrapers.involve_asia_feed import (
        AuthError,
        InvolveAsiaFeedScraper,
        MissingCredentials,
    )

    session = get_sessionmaker()()
    try:
        scraper = InvolveAsiaFeedScraper()
        try:
            offers = scraper.scrape()
        except MissingCredentials as exc:
            log.warning("involve_asia.skipped", reason=str(exc))
            return {"source": "Involve Asia", "skipped": "no credentials"}
        except AuthError as exc:
            log.warning("involve_asia.auth_failed", reason=str(exc))
            return {"source": "Involve Asia", "error": "auth failed"}

        summary = ingest_raw(session, "Involve Asia", offers)
        return {"source": "Involve Asia", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "raw": summary.raw_count,
                "errors": len(summary.errors),
                "sample_error": summary.errors[0] if summary.errors else None}
    finally:
        session.close()


@celery_app.task(name="sync_vcommission")
@_tracked_feed("vCommission")
def sync_vcommission() -> dict:
    """Full pull of the vCommission (Trackier) coupons + deals -> pipeline.

    Builds the campaign_id -> tracking_link map, then ingests coupon codes and
    code-less deals joined to their affiliate deeplink. New codes for
    validator-backed merchants get picked up by `enqueue_revalidations`.
    """
    from scrapers.vcommission_feed import MissingCredentials, VCommissionFeedScraper

    session = get_sessionmaker()()
    try:
        scraper = VCommissionFeedScraper()
        try:
            offers = scraper.scrape()
        except MissingCredentials as exc:
            log.warning("vcommission.skipped", reason=str(exc))
            return {"source": "vCommission", "skipped": "no api key"}

        summary = ingest_raw(session, "vCommission", offers)
        return {"source": "vCommission", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "raw": summary.raw_count,
                "errors": len(summary.errors),
                "sample_error": summary.errors[0] if summary.errors else None}
    finally:
        session.close()


@celery_app.task(name="sync_admitad")
@_tracked_feed("Admitad")
def sync_admitad() -> dict:
    """Full pull of the Admitad (Mitgo) coupons feed -> pipeline.

    OAuth2 client_credentials -> token -> /coupons/. promocode → code, goto_link →
    affiliate click-out. New codes for validator-backed merchants get picked up by
    `enqueue_revalidations`.
    """
    from scrapers.admitad_feed import AdmitadFeedScraper, AuthError, MissingCredentials

    session = get_sessionmaker()()
    try:
        scraper = AdmitadFeedScraper()
        try:
            offers = scraper.scrape()
        except MissingCredentials as exc:
            log.warning("admitad.skipped", reason=str(exc))
            return {"source": "Admitad", "skipped": "no credentials"}
        except AuthError as exc:
            log.warning("admitad.auth_failed", reason=str(exc))
            return {"source": "Admitad", "error": "auth failed"}

        summary = ingest_raw(session, "Admitad", offers)
        if not offers:
            # Auth worked but nothing came back: the account has no joined
            # advertiser programmes with coupons for this ad space (or needs
            # ADMITAD_WEBSITE_ID). Surface it rather than look "healthy-but-empty".
            return {"source": "Admitad", "error": (
                "Admitad returned 0 coupons — join advertiser programmes in the "
                "Admitad dashboard and/or set ADMITAD_WEBSITE_ID"), "raw": 0}
        return {"source": "Admitad", "created": summary.coupons_created,
                "updated": summary.coupons_updated, "raw": summary.raw_count,
                "errors": len(summary.errors),
                "sample_error": summary.errors[0] if summary.errors else None}
    finally:
        session.close()


@celery_app.task(name="sync_feedico")
@_tracked_feed("Feedico")
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


@celery_app.task(name="post_new_to_telegram")
def post_new_to_telegram() -> dict:
    """Beat: post newly-verified coupons + trials to the Telegram channel
    (no-op until TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID are set)."""
    from core.telegram import is_posted, mark_posted, send_message, telegram_configured
    from scheduler.telegram_poster import post_new

    if not telegram_configured():
        return {"skipped": "telegram not configured"}
    session = get_sessionmaker()()
    try:
        return post_new(session, send=send_message, is_posted=is_posted,
                        mark_posted=mark_posted, limit=get_settings().telegram_post_max_per_run)
    finally:
        session.close()


@celery_app.task(name="send_due_reminders")
def send_due_reminders() -> dict:
    """Beat: email cancel-reminders whose window is due; expire past ones.
    Emails only go out once an email provider is configured; until then the
    reminders are stored and simply wait."""
    from core.email import send_email
    from scheduler.reminders import dispatch_due_reminders

    session = get_sessionmaker()()
    try:
        return dispatch_due_reminders(session, send=send_email)
    finally:
        session.close()


@celery_app.task(name="validate_coupon", bind=True, max_retries=2, default_retry_delay=60,
                 soft_time_limit=150, time_limit=180)
def validate_coupon(self, coupon_id: int) -> dict:
    # A hung browser must not hold one of the worker's two slots forever.
    try:
        return _validate_coupon(coupon_id)
    finally:
        _clear_pending(coupon_id)


def _validate_coupon(coupon_id: int) -> dict:
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
            if enqueue_validation(coupon.id, priority=prio):
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
    # Involve Asia Offers feed sync (India + SE Asia merchants).
    "sync-involve-asia": {
        "task": "sync_involve_asia",
        "schedule": timedelta(minutes=get_settings().involve_asia_sync_frequency_minutes),
    },
    # vCommission (Trackier) coupons + deals sync (India merchants).
    "sync-vcommission": {
        "task": "sync_vcommission",
        "schedule": timedelta(minutes=get_settings().vcommission_sync_frequency_minutes),
    },
    # Admitad (Mitgo) coupons sync (global + India merchants).
    "sync-admitad": {
        "task": "sync_admitad",
        "schedule": timedelta(minutes=get_settings().admitad_sync_frequency_minutes),
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
    # Free Trials: send cancel-reminders (stores work always; emails once keyed).
    "send-reminders": {
        "task": "send_due_reminders",
        "schedule": timedelta(minutes=get_settings().reminder_check_frequency_minutes),
    },
    # Traffic: post newly-verified coupons/trials to Telegram (no-op until keyed).
    "post-telegram": {
        "task": "post_new_to_telegram",
        "schedule": timedelta(minutes=get_settings().telegram_post_frequency_minutes),
    },
}
