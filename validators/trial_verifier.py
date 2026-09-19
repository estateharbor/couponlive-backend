"""Live verification for free-trial offers (T3).

Tiered cascade (cheap -> expensive):
  Tier 1 (HTTP)    signup_url loads (2xx, not a dead/404/discontinued page).
  Tier 2 (Browser) Playwright renders it; a signup/CTA + trial keyword is present.
  Tier 3 (LLM)     when Tier 2 is ambiguous, ask the model "is this offer still
                   available?" and require an evidence quote from the page.

Outcome -> confidence score (0-100, §8.5) -> status:
  >=80 verified · 55-79 likely_active · <55 unverified · link dead -> broken.

Only a passing run earns `verified`. Verification NEVER invents facts — it only
sets status/confidence/last_verified_at. Reuses the Emergent/LLM key (T2) and the
Playwright worker image (coupon validation). Playwright is imported lazily.

Diagnostic: docker compose exec worker python -m validators.trial_verifier <tool-slug>
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from core.config import get_settings
from core.logging import get_logger
from models.enums import TrialVerificationStatus
from models.models import Tool, TrialOffer

log = get_logger("verify.trials")

_UA = "CouponLiveBot/1.0 (+https://couponlive.in/bot)"

_TRIAL_MARKERS = re.compile(
    r"(free trial|try (it )?free|start (your )?(free )?trial|no credit card|days free|"
    r"₹\s*0\b|get started|sign up free|start free|free plan|free forever)",
    re.IGNORECASE,
)
_SIGNUP_MARKERS = re.compile(
    r"(sign\s?up|get started|create (an )?account|start free|try free|register)", re.IGNORECASE
)
_DEAD_MARKERS = re.compile(
    r"(page not found|404|no longer available|has been discontinued|plan discontinued|"
    r"not available in your (region|country))",
    re.IGNORECASE,
)

_SOURCE_RELIABILITY = {
    "vendor_page": 1.0,
    "affiliate_feed": 0.9,
    "producthunt": 0.7,
    "manual": 0.7,
    "community": 0.5,
}

_TIER_WEIGHT = {0: 0.0, 1: 0.5, 2: 0.85, 3: 1.0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_trial_confidence(
    highest_tier: int,
    source: str | None,
    *,
    hours_since_pass: float = 0.0,
    positive: int = 0,
    total: int = 0,
    verified_from_in: bool = False,
) -> float:
    """§8.5 blend, 0-100. Feedback is Bayesian (neutral 0.5 with no votes)."""
    recency = max(0.0, 1 - hours_since_pass / 168)
    tier_weight = _TIER_WEIGHT.get(highest_tier, 0.0)
    feedback = (positive + 1) / (total + 2)
    source_rel = _SOURCE_RELIABILITY.get((source or "").lower(), 0.5)
    geo_bonus = 1.0 if verified_from_in else 0.85
    score = 100 * (
        0.35 * recency + 0.25 * tier_weight + 0.2 * feedback + 0.1 * source_rel + 0.1 * geo_bonus
    )
    return round(score, 1)


def status_for(score: float) -> TrialVerificationStatus:
    if score >= 80:
        return TrialVerificationStatus.verified
    if score >= 55:
        return TrialVerificationStatus.likely_active
    return TrialVerificationStatus.unverified


@dataclass
class VerifyOutcome:
    highest_tier: int          # 0 = link dead
    link_dead: bool
    note: str
    final_url: str | None = None


# -- tiers -----------------------------------------------------------------
def tier1_http(url: str, *, session: requests.Session | None = None) -> tuple[bool, str, str]:
    """(ok, final_url, text) — ok False when the link is dead/404/discontinued."""
    sess = session or requests.Session()
    try:
        r = sess.get(url, headers={"User-Agent": _UA}, timeout=30, allow_redirects=True)
    except Exception as exc:
        return False, url, f"request failed: {exc}"
    body = r.text or ""
    if r.status_code >= 400:
        return False, str(r.url), f"http {r.status_code}"
    if _DEAD_MARKERS.search(body[:6000]):
        return False, str(r.url), "dead-page marker"
    return True, str(r.url), body


def tier2_browser(url: str) -> tuple[bool, bool, str]:
    """(trial_text_found, signup_found, page_text). Best-effort; never raises."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        log.warning("verify.playwright_missing", error=str(exc))
        return False, False, ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_UA)
                page.set_default_timeout(30000)
                page.goto(url, wait_until="networkidle")
                text = page.inner_text("body")
            finally:
                browser.close()
    except Exception as exc:
        log.warning("verify.tier2_failed", url=url, error=str(exc))
        return False, False, ""
    return bool(_TRIAL_MARKERS.search(text)), bool(_SIGNUP_MARKERS.search(text)), text


def tier3_llm(text: str, tool_name: str, title: str) -> tuple[bool, str]:
    """(available, note). Requires an evidence quote actually on the page."""
    from core.llm import LLMUnavailable, add_tokens_today, llm_extract_json

    system = (
        "You verify whether a specific free-trial/free-plan offer is still available on a page. "
        "Return ONLY JSON: {\"available\": bool, \"evidence_quote\": \"exact short substring from the page\"}. "
        "available=true only if the page clearly still offers this trial/free plan."
    )
    user = f"TOOL: {tool_name}\nOFFER: {title}\n\nPAGE TEXT:\n{text[:10000]}"
    try:
        data, tokens = llm_extract_json(system, user)
        add_tokens_today(tokens)
    except LLMUnavailable:
        return False, "no llm key"
    except Exception as exc:
        return False, f"llm error: {exc}"
    available = bool(data.get("available"))
    quote = re.sub(r"\s+", " ", str(data.get("evidence_quote") or "")).strip().lower()
    page_norm = re.sub(r"\s+", " ", text).lower()
    if available and quote and quote[:60] in page_norm:
        return True, "llm confirmed"
    return False, "llm unconfirmed / no evidence"


# -- orchestration ---------------------------------------------------------
def verify_offer(offer: TrialOffer, tool_name: str) -> VerifyOutcome:
    ok, final_url, body = tier1_http(offer.signup_url)
    if not ok:
        return VerifyOutcome(highest_tier=0, link_dead=True, note=f"tier1: {body[:80]}", final_url=final_url)

    trial_text, signup, page_text = tier2_browser(offer.signup_url)
    if trial_text and signup:
        return VerifyOutcome(highest_tier=2, link_dead=False, note="tier2: trial+signup found", final_url=final_url)

    # Ambiguous (page loads but no clear trial/signup markers) -> ask the LLM.
    text_for_llm = page_text or body
    available, note = tier3_llm(text_for_llm, tool_name, offer.title)
    if available:
        return VerifyOutcome(highest_tier=3, link_dead=False, note=f"tier3: {note}", final_url=final_url)

    # Link is alive but we couldn't confirm the trial -> tier1 only.
    return VerifyOutcome(highest_tier=1, link_dead=False, note=f"tier1 only ({note})", final_url=final_url)


def record_verification(offer: TrialOffer, outcome: VerifyOutcome) -> None:
    """Apply an outcome to the offer (status/confidence/last_verified_at)."""
    if outcome.link_dead:
        offer.verification_status = TrialVerificationStatus.broken
        offer.confidence_score = 0.0
        offer.last_verified_at = _now()
        return
    score = compute_trial_confidence(outcome.highest_tier, offer.source, hours_since_pass=0.0)
    offer.confidence_score = score
    offer.verification_status = status_for(score)
    offer.last_verified_at = _now()
    offer.last_verified_from = offer.last_verified_from or None  # geo proxy not wired yet


def diagnose(slug: str) -> None:
    from sqlalchemy import select

    from models.base import get_sessionmaker

    session = get_sessionmaker()()
    try:
        tool = session.scalar(select(Tool).where(Tool.slug == slug))
        if tool is None:
            print(f"no tool {slug!r}")
            return
        for offer in tool.offers:
            outcome = verify_offer(offer, tool.name)
            record_verification(offer, outcome)
            print(f"  {offer.title!r}: tier={outcome.highest_tier} dead={outcome.link_dead} "
                  f"-> {offer.verification_status.value} ({offer.confidence_score}) [{outcome.note}]")
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    import sys

    diagnose(sys.argv[1] if len(sys.argv) > 1 else "canva-pro")
