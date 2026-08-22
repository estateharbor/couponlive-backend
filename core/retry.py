"""Retry / backoff helpers and randomized rate-limiting.

Shared by scrapers and (later) validators so backoff policy lives in one
place. Scraping is read-only and low-risk; the *validator* will need more
conservative settings because automated checkout actions are far more likely
to trip bot detection — that policy is set at the validator layer, not here.
"""
from __future__ import annotations

import random
import time

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings

# A default retry decorator for transient network failures.
network_retry = retry(
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)


def polite_delay() -> float:
    """Sleep a randomized interval between requests and return the seconds slept.

    Bounds come from config (dev vs prod). This is deliberate, courteous
    rate-limiting for public listing pages — not an evasion mechanism.
    """
    settings = get_settings()
    lo = settings.scrape_min_delay_seconds
    hi = max(lo, settings.scrape_max_delay_seconds)
    seconds = random.uniform(lo, hi)
    time.sleep(seconds)
    return seconds
