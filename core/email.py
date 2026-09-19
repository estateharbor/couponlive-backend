"""Provider-agnostic transactional email (Free Trials T4).

Primary: Resend. Fallback: Brevo. Plain `requests`, no SDK. `send_email` returns
True on success, False if no provider is configured or the send failed (callers
treat False as "not sent yet" and can retry) — it never raises.
"""
from __future__ import annotations

import re

import requests

from core.config import get_settings
from core.logging import get_logger

log = get_logger("email")

RESEND_URL = "https://api.resend.com/emails"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"

_FROM_RE = re.compile(r"^\s*(?:(?P<name>.*?)\s*<)?(?P<email>[^<>\s]+@[^<>\s]+?)>?\s*$")


def _parse_from(value: str) -> tuple[str, str]:
    """'Name <a@b.com>' -> ('Name', 'a@b.com'); 'a@b.com' -> ('', 'a@b.com')."""
    m = _FROM_RE.match(value or "")
    if not m:
        return "", value
    return (m.group("name") or "").strip(), m.group("email")


def _resend(to: str, subject: str, html: str, *, key: str, sender: str, timeout: int) -> bool:
    r = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"from": sender, "to": [to], "subject": subject, "html": html},
        timeout=timeout,
    )
    r.raise_for_status()
    return True


def _brevo(to: str, subject: str, html: str, *, key: str, sender: str, timeout: int) -> bool:
    name, email = _parse_from(sender)
    r = requests.post(
        BREVO_URL,
        headers={"api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
        json={
            "sender": {"email": email, **({"name": name} if name else {})},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return True


def email_configured() -> bool:
    s = get_settings()
    return bool(s.resend_api_key or s.brevo_api_key)


def send_email(to: str, subject: str, html: str, *, timeout: int = 30) -> bool:
    """Send one transactional email. Returns False (no raise) if unconfigured/failed."""
    s = get_settings()
    if s.resend_api_key:
        try:
            return _resend(to, subject, html, key=s.resend_api_key, sender=s.email_from, timeout=timeout)
        except Exception as exc:
            log.warning("email.resend_failed", error=str(exc))
    if s.brevo_api_key:
        try:
            return _brevo(to, subject, html, key=s.brevo_api_key, sender=s.email_from, timeout=timeout)
        except Exception as exc:
            log.warning("email.brevo_failed", error=str(exc))
    if not email_configured():
        log.info("email.skipped_no_provider", to=to)
    return False
