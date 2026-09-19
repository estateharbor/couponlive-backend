"""/reminders — cancel-before-auto-debit reminders (Free Trials T4).

Account-less: a reminder is keyed by the email entered + a random token used for
one-click manage/unsubscribe from the email. Explicit consent required (DPDP);
minimal PII (email only). Emails are sent by the scheduler; a confirmation is
sent on creation when an email provider is configured.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from core.config import get_settings
from core.logging import get_logger
from models.enums import TrialReminderStatus
from models.models import TrialReminder
from models.schemas import ReminderIn, ReminderOut

router = APIRouter(prefix="/reminders", tags=["reminders"])
log = get_logger("reminders")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _manage_url(token: str) -> str:
    return f"{get_settings().app_url.rstrip('/')}/reminders/{token}/cancel"


def _out(r: TrialReminder) -> ReminderOut:
    offsets = [int(x) for x in r.remind_days_before.split(",") if x.strip().isdigit()]
    return ReminderOut(token=r.token, tool_name=r.tool_name, ends_on=r.ends_on,
                       status=r.status, remind_days_before=offsets, manage_url=_manage_url(r.token))


def _confirmation_html(r: TrialReminder) -> str:
    price = f"₹{r.renew_price_inr:,.0f}" if r.renew_price_inr is not None else (r.renew_note or "the paid plan")
    offsets = ", ".join(f"{o}" for o in r.remind_days_before.split(","))
    cancel = f'<p><a href="{r.cancel_url}">Cancel your {r.tool_name} trial &rarr;</a></p>' if r.cancel_url else ""
    return (
        f"<h2>Reminder set for {r.tool_name}</h2>"
        f"<p>Your trial ends on <strong>{r.ends_on:%d %b %Y}</strong>, after which it renews at "
        f"<strong>{price}</strong>.</p>"
        f"<p>We'll email you <strong>{offsets} day(s) before</strong> so you can cancel in time.</p>"
        f"{cancel}"
        f'<p style="color:#888;font-size:12px">Mark as cancelled / stop reminders: '
        f'<a href="{_manage_url(r.token)}">manage this reminder</a>.</p>'
    )


@router.post("", response_model=ReminderOut)
def create_reminder(body: ReminderIn, db: Session = Depends(get_db)):
    if not body.consent:
        raise HTTPException(status_code=400, detail="consent is required to email you reminders")
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="a valid email is required")

    ends_on = body.ends_on
    if ends_on is None and body.started_on and body.trial_days:
        ends_on = body.started_on + timedelta(days=body.trial_days)
    if ends_on is None:
        raise HTTPException(status_code=400, detail="provide ends_on, or started_on + trial_days")
    if ends_on < date.today():
        raise HTTPException(status_code=400, detail="the trial end date is in the past")

    offsets = body.remind_days_before or [3, 1]
    offsets = sorted({o for o in offsets if 0 <= o <= 60}, reverse=True) or [3, 1]

    reminder = TrialReminder(
        email=email,
        offer_id=body.offer_id,
        tool_name=body.tool_name.strip()[:255] or "your trial",
        ends_on=ends_on,
        renew_price_inr=body.renew_price_inr,
        renew_note=(body.renew_note or None),
        cancel_url=(body.cancel_url or None),
        remind_days_before=",".join(str(o) for o in offsets),
        sent_offsets="",
        token=secrets.token_urlsafe(24),
        status=TrialReminderStatus.active,
    )
    db.add(reminder)
    db.commit()

    # Best-effort confirmation email (no-op if no provider configured).
    try:
        from core.email import send_email

        send_email(email, f"Reminder set — cancel {reminder.tool_name} before it renews",
                   _confirmation_html(reminder))
    except Exception as exc:  # never fail the request on email trouble
        log.warning("reminders.confirm_email_failed", error=str(exc))

    return _out(reminder)


def _get(db: Session, token: str) -> TrialReminder:
    r = db.scalar(select(TrialReminder).where(TrialReminder.token == token))
    if r is None:
        raise HTTPException(status_code=404, detail="reminder not found")
    return r


@router.get("/{token}", response_model=ReminderOut)
def get_reminder(token: str, db: Session = Depends(get_db)):
    return _out(_get(db, token))


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>"
        f"<div style='font-family:system-ui;max-width:520px;margin:12vh auto;padding:0 20px;text-align:center'>"
        f"<h1 style='font-size:22px'>{title}</h1><p style='color:#555'>{body}</p>"
        f"<p><a href='{get_settings().app_url}/free-trials/' style='color:#0a5ff3'>Back to CouponLive Free Trials</a></p></div>"
    )


# GET (one-click from the email) — marks cancelled and shows a confirmation page.
@router.get("/{token}/cancel")
def cancel_reminder_link(token: str, db: Session = Depends(get_db)):
    r = _get(db, token)
    r.status = TrialReminderStatus.cancelled
    db.commit()
    return _page("Reminder cancelled",
                 f"We won't remind you about {r.tool_name} again. If you cancelled the trial too, you're all set.")


@router.get("/{token}/unsubscribe")
def unsubscribe_link(token: str, db: Session = Depends(get_db)):
    r = _get(db, token)
    r.status = TrialReminderStatus.unsubscribed
    db.commit()
    return _page("Unsubscribed", f"You won't get further emails about {r.tool_name}.")


# POST variants for programmatic use (e.g. a future dashboard).
@router.post("/{token}/cancel", response_model=ReminderOut)
def cancel_reminder(token: str, db: Session = Depends(get_db)):
    r = _get(db, token)
    r.status = TrialReminderStatus.cancelled
    db.commit()
    return _out(r)
