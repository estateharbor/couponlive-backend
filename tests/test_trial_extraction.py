"""Tests for LLM trial extraction (T2) — no network, fake LLM + fake page.

Covers the load-bearing guarantees: the evidence-quote anti-hallucination check,
that extraction fills facts WITHOUT marking anything verified, and content-hash
gating (unchanged pages skip the LLM)."""
from __future__ import annotations

import pytest

from models.enums import TrialStatus, TrialVerificationStatus
from models.models import Tool, TrialOffer
import scrapers.trial_extraction as te

PAGE = (
    "Canva Pro pricing. Design anything with premium templates, brand kit, background "
    "remover and more. Start your free trial today. Get 30 days free, no credit card required. "
    "Invite your whole team and collaborate in real time. After the trial Canva Pro renews at "
    "$12.99/month, billed monthly, or save with annual billing. Cancel anytime from your account "
    "settings before the trial ends and you won't be charged. Millions of teams use Canva Pro."
)

# One real offer (evidence on the page) + one hallucinated offer (evidence absent).
FAKE_LLM = {
    "has_free_trial": True,
    "confidence": 0.9,
    "offers": [
        {"offer_type": "no_card_trial", "title": "Canva Pro — 30 days free",
         "trial_days": 30, "card_required": False, "auto_renews": True,
         "renew_price_usd": 12.99, "renew_period": "month",
         "evidence_quote": "Get 30 days free, no credit card required"},
        {"offer_type": "student_offer", "title": "Totally made up student plan",
         "trial_days": 365, "card_required": False,
         "evidence_quote": "students get 1 year free forever guaranteed"},  # NOT on page
    ],
}


@pytest.fixture
def tool(db_session):
    t = Tool(name="Canva Pro", slug="canva-pro", category="design",
             pricing_page_url="https://canva.com/pro", status=TrialStatus.live)
    db_session.add(t)
    db_session.commit()
    return t


def _patch(monkeypatch, page=PAGE, llm=FAKE_LLM):
    monkeypatch.setattr(te, "fetch_page", lambda url, session=None: page)
    monkeypatch.setattr(te, "llm_extract_json", lambda system, user, **kw: (llm, 1234))


def test_applies_real_offer_and_drops_hallucination(db_session, tool, monkeypatch):
    _patch(monkeypatch)
    res = te.extract_for_tool(db_session, tool, force=True)
    assert res["applied"] == 1          # only the evidence-backed offer
    assert res["dropped_no_evidence"] == 1  # the fabricated one is rejected

    offers = db_session.query(TrialOffer).filter_by(tool_id=tool.id).all()
    titles = {o.title for o in offers}
    assert "Canva Pro — 30 days free" in titles
    assert "Totally made up student plan" not in titles

    o = next(o for o in offers if o.title == "Canva Pro — 30 days free")
    assert o.card_required is False and o.trial_days == 30
    assert float(o.renew_price_usd) == 12.99 and o.source == "vendor_page"
    # Extraction must NOT fake verification.
    assert o.verification_status is TrialVerificationStatus.unverified


def test_content_hash_skips_unchanged(db_session, tool, monkeypatch):
    _patch(monkeypatch)
    te.extract_for_tool(db_session, tool, force=True)          # first pass writes hash
    res2 = te.extract_for_tool(db_session, tool, force=False)  # same page → skip
    assert res2.get("skipped", "").startswith("unchanged")


def test_thin_page_skipped(db_session, tool, monkeypatch):
    monkeypatch.setattr(te, "fetch_page", lambda url, session=None: "too short")
    res = te.extract_for_tool(db_session, tool, force=True)
    assert "skipped" in res


def test_map_type_falls_back_to_unknown():
    from models.enums import TrialOfferType
    assert te._map_type("no_card_trial") is TrialOfferType.no_card_trial
    assert te._map_type("Price-Off Nonsense") is TrialOfferType.unknown
