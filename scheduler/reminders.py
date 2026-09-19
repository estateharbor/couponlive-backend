"""Cancel-reminder dispatch (Free Trials T4).

Finds active reminders whose end date is within a not-yet-sent offset window and
emails them; expires ones whose end date has passed. `send` is injectable so
tests run without a provider. An email offset is marked sent only when the send
actually succeeds, so nothing is lost while no provider is configured (they go
out once a key is added)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import get_logger
from models.enums import TrialReminderStatus
from models.models import TrialReminder

log = get_logger("reminders.dispatch")

Sender = Callable[[str, str, str], bool]


def _manage(token: str) -> str:
    base = get_settings().app_url.rstrip("/")
    return f"{base}/reminders/{token}"


def _reminder_html(r: TrialReminder, days_until: int) -> str:
    when = "today" if days_until == 0 else ("tomorrow" if days_until == 1 else f"in {days_until} days")
    price = f"₹{r.renew_price_inr:,.0f}" if r.renew_price_inr is not None else (r.renew_note or "the paid plan")
    cancel = (
        f'<p><a href="{r.cancel_url}" style="background:#0a5ff3;color:#fff;padding:10px 16px;'
        f'border-radius:8px;text-decoration:none;display:inline-block">Cancel {r.tool_name} now &rarr;</a></p>'
        if r.cancel_url else ""
    )
    return (
        f"<h2>Your {r.tool_name} free trial ends {when}</h2>"
        f"<p>It ends on <strong>{r.ends_on:%d %b %Y}</strong>. After that it renews at "
        f"<strong>{price}</strong>. Cancel before then if you don't want to be charged.</p>"
        f"{cancel}"
        f'<p style="color:#888;font-size:12px">'
        f'<a href="{_manage(r.token)}/cancel">I\'ve cancelled — stop reminders</a> · '
        f'<a href="{_manage(r.token)}/unsubscribe">Unsubscribe</a></p>'
    )


def dispatch_due_reminders(
    session: Session, *, send: Sender, today: date | None = None
) -> dict:
    today = today or date.today()
    reminders = list(session.scalars(
        select(TrialReminder).where(TrialReminder.status == TrialReminderStatus.active)
    ))
    sent = expired = 0
    for r in reminders:
        if r.ends_on < today:
            r.status = TrialReminderStatus.expired
            expired += 1
            continue
        days_until = (r.ends_on - today).days
        offsets = [int(x) for x in r.remind_days_before.split(",") if x.strip().isdigit()]
        already = {x for x in r.sent_offsets.split(",") if x.strip()}
        newly: list[str] = []
        for o in sorted(offsets, reverse=True):
            if str(o) in already:
                continue
            if 0 <= days_until <= o:
                ok = send(
                    r.email,
                    f"⏳ {r.tool_name} trial ends "
                    + ("today" if days_until == 0 else "tomorrow" if days_until == 1 else f"in {days_until} days"),
                    _reminder_html(r, days_until),
                )
                if ok:
                    newly.append(str(o))
                    sent += 1
        if newly:
            r.sent_offsets = ",".join(sorted(already | set(newly)))
    session.commit()
    result = {"active": len(reminders), "emails_sent": sent, "expired": expired}
    log.info("reminders.dispatch_done", **result)
    return result
