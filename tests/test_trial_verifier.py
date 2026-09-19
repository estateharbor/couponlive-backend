"""Tests for live trial verification (T3) — pure logic + orchestration, no network."""
from __future__ import annotations

from models.enums import TrialVerificationStatus
import validators.trial_verifier as tv


class _Offer:
    def __init__(self, source="vendor_page", signup_url="https://x/trial", title="Foo trial"):
        self.source = source
        self.signup_url = signup_url
        self.title = title
        self.verification_status = TrialVerificationStatus.unverified
        self.confidence_score = 0.0
        self.last_verified_at = None
        self.last_verified_from = None


def test_confidence_tier2_vendor_is_verified():
    score = tv.compute_trial_confidence(2, "vendor_page")
    assert score >= 80
    assert tv.status_for(score) is TrialVerificationStatus.verified


def test_confidence_tier1_only_is_likely_active():
    score = tv.compute_trial_confidence(1, "vendor_page")
    assert 55 <= score < 80
    assert tv.status_for(score) is TrialVerificationStatus.likely_active


def test_confidence_low_source_stale_is_unverified():
    score = tv.compute_trial_confidence(1, "community", hours_since_pass=200)
    assert score < 55
    assert tv.status_for(score) is TrialVerificationStatus.unverified


def test_status_thresholds():
    assert tv.status_for(80) is TrialVerificationStatus.verified
    assert tv.status_for(79.9) is TrialVerificationStatus.likely_active
    assert tv.status_for(54.9) is TrialVerificationStatus.unverified


def test_tier1_http_classification():
    class _Resp:
        def __init__(self, status, text, url="https://x/final"):
            self.status_code = status
            self.text = text
            self.url = url

    class _Sess:
        def __init__(self, resp):
            self._r = resp

        def get(self, *a, **k):
            return self._r

    ok, _u, _t = tv.tier1_http("https://x", session=_Sess(_Resp(200, "Start your free trial today")))
    assert ok is True
    ok, _u, note = tv.tier1_http("https://x", session=_Sess(_Resp(404, "Page not found")))
    assert ok is False
    ok, _u, note = tv.tier1_http("https://x", session=_Sess(_Resp(200, "This plan has been discontinued")))
    assert ok is False  # dead-page marker


def test_verify_offer_tier2_pass(monkeypatch):
    monkeypatch.setattr(tv, "tier1_http", lambda url, session=None: (True, url, "body"))
    monkeypatch.setattr(tv, "tier2_browser", lambda url: (True, True, "free trial ... sign up"))
    out = tv.verify_offer(_Offer(), "Foo")
    assert out.highest_tier == 2 and out.link_dead is False


def test_verify_offer_dead_link(monkeypatch):
    monkeypatch.setattr(tv, "tier1_http", lambda url, session=None: (False, url, "http 404"))
    out = tv.verify_offer(_Offer(), "Foo")
    assert out.link_dead is True and out.highest_tier == 0
    o = _Offer()
    tv.record_verification(o, out)
    assert o.verification_status is TrialVerificationStatus.broken and o.confidence_score == 0.0


def test_verify_offer_tier3_confirms(monkeypatch):
    monkeypatch.setattr(tv, "tier1_http", lambda url, session=None: (True, url, "body"))
    monkeypatch.setattr(tv, "tier2_browser", lambda url: (False, False, "ambiguous page"))
    monkeypatch.setattr(tv, "tier3_llm", lambda text, tool, title: (True, "llm confirmed"))
    out = tv.verify_offer(_Offer(), "Foo")
    assert out.highest_tier == 3
    o = _Offer()
    tv.record_verification(o, out)
    assert o.verification_status is TrialVerificationStatus.verified  # tier3 vendor -> >=80


def test_verify_offer_tier1_only_when_unconfirmed(monkeypatch):
    monkeypatch.setattr(tv, "tier1_http", lambda url, session=None: (True, url, "body"))
    monkeypatch.setattr(tv, "tier2_browser", lambda url: (False, False, "ambiguous"))
    monkeypatch.setattr(tv, "tier3_llm", lambda text, tool, title: (False, "unconfirmed"))
    out = tv.verify_offer(_Offer(), "Foo")
    assert out.highest_tier == 1 and out.link_dead is False
