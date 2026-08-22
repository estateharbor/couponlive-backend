"""Alerting hook — early warning for scraper breakage / site redesigns.

Log-based by default (structured, so it's greppable and machine-ingestible).
If `ALERT_WEBHOOK_URL` is configured it will also POST a compact JSON payload —
opt-in, the user's own ops endpoint. Structured so Slack/email/PagerDuty can be
swapped in without touching callers.

Callers use the small helpers at the bottom (`alert_scrape_result`,
`alert_source_stale`) rather than hand-rolling payloads.
"""
from __future__ import annotations

from typing import Any

from core.config import get_settings
from core.logging import get_logger

log = get_logger("alert")


def send_alert(event: str, level: str = "warning", **fields: Any) -> None:
    """Emit a structured alert. Always logs; optionally POSTs to a webhook."""
    settings = get_settings()
    # `event` is structlog's positional message; pass it as such (no kwarg clash).
    getattr(log, level, log.warning)(event, **fields)

    url = settings.alert_webhook_url
    if not url:
        return
    try:
        import requests  # local import; alerting shouldn't hard-depend on it

        requests.post(url, json={"event": event, "level": level, **fields}, timeout=5)
    except Exception as exc:  # never let alerting failure break the caller
        log.warning("alert.webhook_failed", error=str(exc))


# --- convenience wrappers for the common cases ---------------------------
def alert_scrape_result(source: str, raw_count: int, success_rate: float) -> None:
    """Fire when a scrape returns zero results or a low success rate — the
    classic signal that a site redesign broke our selectors."""
    settings = get_settings()
    if raw_count == 0:
        send_alert("scrape.zero_results", level="error", source=source)
    elif success_rate < settings.alert_min_success_rate:
        send_alert(
            "scrape.low_success_rate",
            level="warning",
            source=source,
            success_rate=success_rate,
            threshold=settings.alert_min_success_rate,
        )


def alert_source_stale(source: str, hours_since_success: float) -> None:
    send_alert(
        "source.stale",
        level="error",
        source=source,
        hours_since_success=round(hours_since_success, 1),
    )
