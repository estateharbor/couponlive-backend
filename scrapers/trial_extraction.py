"""LLM extraction of free-trial facts from a tool's pricing page (T2).

Flow (per tool):
  1. Fetch the pricing/trial page (requests; Playwright fallback if JS-rendered).
  2. Clean to text, truncate, compute content_hash — SKIP the LLM if unchanged.
  3. LLM -> structured JSON (temperature 0, provider-agnostic via core.llm).
  4. Validate with Pydantic; DROP any offer whose evidence_quote isn't actually
     on the page (anti-hallucination).
  5. Upsert facts onto TrialOffer rows (one per offer_type). Extraction fills
     FACTS only; it never sets `verified` — that's the live-verification job (T3).

Honesty: unknown stays NULL (renders "Unknown"); we never invent facts.

Diagnostic (one tool):  docker compose exec worker python -m scrapers.trial_extraction <tool-slug>
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.llm import LLMUnavailable, llm_extract_json
from core.logging import get_logger
from models.base import get_sessionmaker
from models.enums import TrialOfferType
from models.models import Tool, TrialOffer

log = get_logger("extract.trials")

_UA = "CouponLiveBot/1.0 (+https://couponlive.in/bot)"
_MAX_CHARS = 12000

SYSTEM_PROMPT = (
    "You extract free-trial and promotional-offer facts from a software pricing page.\n"
    "Return ONLY valid JSON matching the schema. Use null when a fact is not explicitly "
    "stated — never guess.\n"
    "Prices: extract exactly as shown; if shown in INR fill *_inr, if USD fill *_usd.\n"
    '"card_required" is true only if the page says a card/payment method is needed to start '
    'the trial; false only if it explicitly says "no credit card required" (or equivalent); '
    "otherwise null.\n"
    "Every offer MUST include evidence_quote: a short EXACT substring copied verbatim from the "
    "page text that supports the trial claim.\n"
    "JSON schema: {\"has_free_trial\": bool, \"offers\": [{\"offer_type\": one of "
    "[no_card_trial,card_trial,freemium_premium_trial,extended_trial,startup_credit,"
    "student_offer,telecom_bundle,bank_card_offer,ai_credits,lifetime_free_tier], "
    "\"title\": str, \"trial_days\": int|null, \"credit_amount\": num|null, "
    "\"credit_currency\": str|null, \"card_required\": bool|null, \"auto_renews\": bool|null, "
    "\"renew_price_inr\": num|null, \"renew_price_usd\": num|null, \"renew_period\": "
    "\"month\"|\"year\"|null, \"eligibility\": str|null, \"india_mentioned\": bool|null, "
    "\"evidence_quote\": str}], \"confidence\": 0.0-1.0}"
)


class ExtractedOffer(BaseModel):
    offer_type: str = "unknown"
    title: str = ""
    trial_days: int | None = None
    credit_amount: float | None = None
    credit_currency: str | None = None
    card_required: bool | None = None
    auto_renews: bool | None = None
    renew_price_inr: float | None = None
    renew_price_usd: float | None = None
    renew_period: str | None = None
    eligibility: str | None = None
    india_mentioned: bool | None = None
    evidence_quote: str = ""


class ExtractionResult(BaseModel):
    has_free_trial: bool = False
    offers: list[ExtractedOffer] = []
    confidence: float = 0.0


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def fetch_page(url: str, *, session: requests.Session | None = None) -> str:
    """Return cleaned page text; fall back to Playwright when JS-rendered/thin."""
    sess = session or requests.Session()
    text = ""
    try:
        r = sess.get(url, headers={"User-Agent": _UA}, timeout=45)
        r.raise_for_status()
        text = _clean_html(r.text)
    except Exception as exc:
        log.warning("extract.fetch_failed", url=url, error=str(exc))

    if len(text) >= 500:
        return text[:_MAX_CHARS]

    # Thin/blocked/JS-rendered → try a real browser (worker image ships Chromium).
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_UA)
                page.goto(url, wait_until="networkidle", timeout=45000)
                text = _clean_html(page.content())
            finally:
                browser.close()
    except Exception as exc:
        log.warning("extract.playwright_failed", url=url, error=str(exc))
    return text[:_MAX_CHARS]


def _map_type(raw: str) -> TrialOfferType:
    try:
        return TrialOfferType(_norm(raw).replace(" ", "_").replace("-", "_"))
    except ValueError:
        return TrialOfferType.unknown


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_for_tool(
    session: Session, tool: Tool, *, force: bool = False
) -> dict:
    """Extract + upsert offers for one tool. Returns a small summary dict."""
    url = tool.pricing_page_url or tool.website_url
    if not url:
        return {"tool": tool.slug, "skipped": "no pricing url"}

    text = fetch_page(url)
    if len(text) < 200:
        return {"tool": tool.slug, "skipped": "page too thin/blocked"}

    chash = _content_hash(text)
    existing = list(session.scalars(select(TrialOffer).where(TrialOffer.tool_id == tool.id)))
    if not force and any(o.content_hash == chash for o in existing):
        return {"tool": tool.slug, "skipped": "unchanged (content_hash)"}

    try:
        raw, tokens = llm_extract_json(SYSTEM_PROMPT, f"URL: {url}\n\nPAGE TEXT:\n{text}")
    except LLMUnavailable:
        raise
    except Exception as exc:
        return {"tool": tool.slug, "error": f"llm: {exc}"}

    try:
        result = ExtractionResult.model_validate(raw)
    except ValidationError as exc:
        return {"tool": tool.slug, "error": f"schema: {exc.errors()[:1]}"}

    page_norm = _norm(text)
    by_type: dict[TrialOfferType, TrialOffer] = {}
    for o in existing:
        by_type.setdefault(o.offer_type, o)

    applied = dropped = 0
    for eo in result.offers:
        # Anti-hallucination: the evidence quote must really be on the page.
        if not eo.evidence_quote or _norm(eo.evidence_quote)[:80] not in page_norm:
            dropped += 1
            continue
        otype = _map_type(eo.offer_type)
        offer = by_type.get(otype)
        if offer is None:
            offer = TrialOffer(tool_id=tool.id, offer_type=otype,
                               title=eo.title or f"{tool.name} free trial",
                               signup_url=url)
            session.add(offer)
            by_type[otype] = offer
        if eo.title:
            offer.title = eo.title
        offer.offer_type = otype
        offer.trial_days = eo.trial_days
        offer.credit_amount = eo.credit_amount
        offer.credit_currency = eo.credit_currency
        offer.card_required = eo.card_required
        offer.auto_renews = eo.auto_renews
        offer.renew_price_inr = eo.renew_price_inr
        offer.renew_price_usd = eo.renew_price_usd
        offer.renew_period = eo.renew_period
        if eo.eligibility:
            offer.eligibility = eo.eligibility
        if eo.india_mentioned is not None:
            offer.india_available = eo.india_mentioned
        offer.source = "vendor_page"
        offer.source_url = url
        offer.content_hash = chash
        # Extraction fills FACTS only — it never marks an offer verified. The
        # live-verification worker (T3) owns verification_status/confidence.
        applied += 1

    # Stamp the hash on all of the tool's offers so the skip-check works next run.
    for o in existing:
        o.content_hash = chash
    session.commit()
    return {"tool": tool.slug, "applied": applied, "dropped_no_evidence": dropped,
            "has_free_trial": result.has_free_trial, "tokens": tokens}


def diagnose(slug: str) -> None:
    session = get_sessionmaker()()
    try:
        tool = session.scalar(select(Tool).where(Tool.slug == slug))
        if tool is None:
            print(f"no tool with slug {slug!r}")
            return
        print(f"extracting {tool.name} from {tool.pricing_page_url or tool.website_url}")
        print(extract_for_tool(session, tool, force=True))
    finally:
        session.close()


if __name__ == "__main__":
    import sys

    diagnose(sys.argv[1] if len(sys.argv) > 1 else "canva-pro")
