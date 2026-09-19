"""Tests for cancel-reminders (T4): API create/manage + dispatch scheduling."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import create_app
from models.enums import TrialReminderStatus
from models.models import TrialReminder
from scheduler.reminders import dispatch_due_reminders


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_create_requires_consent(client):
    r = client.post("/reminders", json={"email": "a@b.com", "tool_name": "Canva", "ends_on": _future(10)})
    assert r.status_code == 400


def test_create_requires_valid_email(client):
    r = client.post("/reminders", json={"email": "not-an-email", "tool_name": "Canva",
                                        "ends_on": _future(10), "consent": True})
    assert r.status_code == 400


def test_create_needs_an_end_date(client):
    r = client.post("/reminders", json={"email": "a@b.com", "tool_name": "Canva", "consent": True})
    assert r.status_code == 400


def test_create_resolves_end_from_started_plus_days(client, db_session):
    r = client.post("/reminders", json={
        "email": "USER@Example.com", "tool_name": "Canva Pro",
        "started_on": date.today().isoformat(), "trial_days": 30, "consent": True,
        "renew_price_inr": 499,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active" and body["token"]
    assert body["remind_days_before"] == [3, 1]
    row = db_session.scalar(
        __import__("sqlalchemy").select(TrialReminder).where(TrialReminder.token == body["token"])
    )
    assert row.email == "user@example.com"                 # normalized lower
    assert row.ends_on == date.today() + timedelta(days=30)


def test_cancel_link_marks_cancelled(client, db_session):
    token = client.post("/reminders", json={"email": "a@b.com", "tool_name": "X",
                                             "ends_on": _future(5), "consent": True}).json()["token"]
    r = client.get(f"/reminders/{token}/cancel")
    assert r.status_code == 200 and "cancel" in r.text.lower()
    row = db_session.scalar(
        __import__("sqlalchemy").select(TrialReminder).where(TrialReminder.token == token)
    )
    assert row.status is TrialReminderStatus.cancelled


def _mk(db_session, **kw):
    r = TrialReminder(
        email="a@b.com", tool_name=kw.get("tool_name", "Tool"),
        ends_on=kw["ends_on"], remind_days_before=kw.get("offsets", "3,1"),
        sent_offsets=kw.get("sent", ""), token=kw["token"],
        status=TrialReminderStatus.active,
    )
    db_session.add(r)
    db_session.commit()
    return r


def test_dispatch_sends_in_window_and_expires_past(db_session):
    today = date(2026, 6, 10)
    _mk(db_session, token="t2", ends_on=today + timedelta(days=2))   # within 3-day window
    _mk(db_session, token="t10", ends_on=today + timedelta(days=10))  # not yet due
    _mk(db_session, token="tpast", ends_on=today - timedelta(days=1))  # expired

    sent: list[str] = []
    res = dispatch_due_reminders(db_session, send=lambda to, s, h: sent.append(to) or True, today=today)
    assert res["emails_sent"] == 1 and res["expired"] == 1
    assert len(sent) == 1


def test_dispatch_no_double_send(db_session):
    today = date(2026, 6, 10)
    _mk(db_session, token="t2", ends_on=today + timedelta(days=2))
    send = lambda to, s, h: True
    first = dispatch_due_reminders(db_session, send=send, today=today)
    second = dispatch_due_reminders(db_session, send=send, today=today)
    assert first["emails_sent"] == 1 and second["emails_sent"] == 0  # offset already marked sent


def test_dispatch_unconfigured_send_does_not_mark(db_session):
    today = date(2026, 6, 10)
    _mk(db_session, token="t2", ends_on=today + timedelta(days=2))
    # send returns False (no provider) -> not marked, retried next time.
    res = dispatch_due_reminders(db_session, send=lambda to, s, h: False, today=today)
    assert res["emails_sent"] == 0
    later = dispatch_due_reminders(db_session, send=lambda to, s, h: True, today=today)
    assert later["emails_sent"] == 1  # now it sends
