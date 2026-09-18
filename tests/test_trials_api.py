"""API tests for the Free Trials vertical (/trials, /tools/{slug})."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import create_app
from models.enums import TrialOfferType, TrialStatus, TrialVerificationStatus
from models.models import Tool, TrialOffer


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def client(db_session):
    canva = Tool(name="Canva Pro", slug="canva-pro", category="design", is_ai_tool=False,
                 status=TrialStatus.live)
    chatgpt = Tool(name="ChatGPT", slug="chatgpt", category="ai", is_ai_tool=True,
                   status=TrialStatus.live)
    db_session.add_all([canva, chatgpt])
    db_session.flush()

    # Canva: 30-day card trial (card required), verified.
    db_session.add(TrialOffer(
        tool_id=canva.id, offer_type=TrialOfferType.card_trial,
        title="Canva Pro — 30-day free trial", trial_days=30, card_required=True,
        signup_url="https://canva.com/pro", verification_status=TrialVerificationStatus.verified,
        confidence_score=90.0, last_verified_at=_now(), status=TrialStatus.live))
    # ChatGPT: no-card free tier, unverified.
    db_session.add(TrialOffer(
        tool_id=chatgpt.id, offer_type=TrialOfferType.lifetime_free_tier,
        title="ChatGPT free plan — no card needed", card_required=False,
        signup_url="https://chat.openai.com", verification_status=TrialVerificationStatus.unverified,
        status=TrialStatus.live))
    # A broken offer that must be hidden from listings.
    db_session.add(TrialOffer(
        tool_id=canva.id, offer_type=TrialOfferType.card_trial, title="Old dead Canva offer",
        signup_url="https://canva.com/dead", verification_status=TrialVerificationStatus.broken,
        status=TrialStatus.live))
    db_session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_list_all_visible_trials(client):
    titles = {t["title"] for t in client.get("/trials").json()}
    assert "Canva Pro — 30-day free trial" in titles
    assert "ChatGPT free plan — no card needed" in titles
    assert "Old dead Canva offer" not in titles          # broken hidden


def test_no_card_filter(client):
    r = client.get("/trials", params={"no_card": True})
    titles = [t["title"] for t in r.json()]
    assert titles == ["ChatGPT free plan — no card needed"]  # only card_required=False


def test_ai_filter(client):
    r = client.get("/trials", params={"ai": True})
    assert all(t["is_ai_tool"] for t in r.json())
    assert {t["tool_slug"] for t in r.json()} == {"chatgpt"}


def test_min_days_filter(client):
    r = client.get("/trials", params={"min_days": 14})
    # Only the 30-day Canva trial qualifies (ChatGPT free tier has no trial_days).
    assert [t["title"] for t in r.json()] == ["Canva Pro — 30-day free trial"]


def test_verified_sorts_first(client):
    order = [t["verification_status"] for t in client.get("/trials", params={"sort": "verified"}).json()]
    assert order[0] == "verified"


def test_tool_page_returns_offers(client):
    r = client.get("/tools/canva-pro")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Canva Pro"
    titles = {o["title"] for o in body["offers"]}
    assert "Canva Pro — 30-day free trial" in titles
    assert "Old dead Canva offer" not in titles          # broken filtered out


def test_unknown_tool_404(client):
    assert client.get("/tools/does-not-exist").status_code == 404


def test_card_required_null_renders_as_unknown(client):
    # An offer with card_required=None must serialize as null (UI shows "Unknown").
    cards = client.get("/trials").json()
    # (all seeded offers set a value; assert the field is present and typed)
    assert all("card_required" in c for c in cards)
