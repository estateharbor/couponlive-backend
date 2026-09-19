"""Tests for the Telegram auto-poster: selection, link-back, and dedupe."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.enums import (
    CouponStatus,
    DiscountType,
    TrialOfferType,
    TrialStatus,
    TrialVerificationStatus,
)
from models.models import Coupon, Merchant, Tool, TrialOffer
from scheduler.telegram_poster import build_coupon_post, build_trial_post, post_new


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def seeded(db_session):
    m = Merchant(name="Kushals", normalized_name="kushals")
    tool = Tool(name="Canva Pro", slug="canva-pro", status=TrialStatus.live)
    db_session.add_all([m, tool])
    db_session.flush()
    db_session.add(Coupon(merchant_id=m.id, code="NEW200", status=CouponStatus.valid,
                          discount_type=DiscountType.fixed, discount_value=200,
                          confidence_score=0.9, first_seen=_now(), last_seen=_now(),
                          last_validated_at=_now()))
    db_session.add(TrialOffer(tool_id=tool.id, offer_type=TrialOfferType.card_trial,
                              title="Canva Pro — 30-day free trial", signup_url="https://canva.com/pro",
                              verification_status=TrialVerificationStatus.verified,
                              last_verified_at=_now(), status=TrialStatus.live))
    db_session.commit()
    return db_session


class _Fakes:
    def __init__(self):
        self.sent: list[str] = []
        self.seen: set[str] = set()

    def send(self, text):
        self.sent.append(text)
        return True

    def is_posted(self, key):
        return key in self.seen

    def mark_posted(self, key):
        self.seen.add(key)


def test_posts_coupon_and_trial_with_site_links(seeded):
    f = _Fakes()
    res = post_new(seeded, send=f.send, is_posted=f.is_posted, mark_posted=f.mark_posted, limit=5)
    assert res["posted"] == 2
    blob = "\n".join(f.sent)
    assert "Kushals" in blob and "NEW200" in blob and "Canva Pro" in blob
    # Links point at couponlive.in (traffic to the site) with the utm tag.
    assert "couponlive.in/store/kushals/" in blob
    assert "couponlive.in/tool/canva-pro/" in blob
    assert "utm_source=telegram" in blob


def test_dedupes_on_second_run(seeded):
    f = _Fakes()
    first = post_new(seeded, send=f.send, is_posted=f.is_posted, mark_posted=f.mark_posted, limit=5)
    second = post_new(seeded, send=f.send, is_posted=f.is_posted, mark_posted=f.mark_posted, limit=5)
    assert first["posted"] == 2 and second["posted"] == 0


def test_respects_limit(seeded):
    f = _Fakes()
    res = post_new(seeded, send=f.send, is_posted=f.is_posted, mark_posted=f.mark_posted, limit=1)
    assert res["posted"] == 1


def test_failed_send_not_marked(seeded):
    f = _Fakes()
    res = post_new(seeded, send=lambda t: False, is_posted=f.is_posted, mark_posted=f.mark_posted, limit=5)
    assert res["posted"] == 0 and not f.seen  # nothing marked -> retried next run


def test_message_format():
    assert "<code>SAVE20</code>" in build_coupon_post("Nike", "20% Off", "SAVE20", "https://x")
    assert "free trial" in build_trial_post("Canva", "30-day free trial", "https://x")
