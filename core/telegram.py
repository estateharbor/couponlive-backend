"""Telegram channel posting + Redis dedupe (traffic auto-poster).

send_message posts to the configured channel via the Bot API (no SDK). is_posted
/mark_posted dedupe by a stable key in a Redis set so an item is never posted
twice. All calls degrade gracefully (return False / no-op) when unconfigured."""
from __future__ import annotations

import requests

from core.config import get_settings
from core.logging import get_logger

log = get_logger("telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"
_POSTED_SET = "tg:posted"


def telegram_configured() -> bool:
    s = get_settings()
    return bool(s.telegram_bot_token and s.telegram_channel_id)


def send_message(text: str) -> bool:
    """Post HTML text to the channel. Returns False (no raise) if unconfigured/failed."""
    s = get_settings()
    if not telegram_configured():
        return False
    try:
        r = requests.post(
            _API.format(token=s.telegram_bot_token),
            json={
                "chat_id": s.telegram_channel_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("telegram.send_failed", error=str(exc))
        return False


def _redis():
    import redis

    return redis.Redis.from_url(get_settings().redis_url)


def is_posted(key: str) -> bool:
    try:
        return bool(_redis().sismember(_POSTED_SET, key))
    except Exception:
        return False


def mark_posted(key: str) -> None:
    try:
        _redis().sadd(_POSTED_SET, key)
    except Exception:
        pass
