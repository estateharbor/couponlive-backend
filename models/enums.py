"""Enum types shared across ORM models and Pydantic schemas."""
from __future__ import annotations

from enum import Enum


class CouponStatus(str, Enum):
    """Lifecycle state of a coupon in our system.

    - unverified: scraped/ingested, never validated yet
    - valid:      last validation succeeded (a real discount applied)
    - invalid:    last validation failed (code rejected / no discount)
    - expired:    aged out by the staleness job, or source marked it expired
    """

    unverified = "unverified"
    valid = "valid"
    invalid = "invalid"
    expired = "expired"


class DiscountType(str, Enum):
    percentage = "percentage"        # e.g. 20% off
    fixed = "fixed"                  # e.g. ₹200 off
    free_shipping = "free_shipping"
    bogo = "bogo"                    # buy-one-get-one / bundle
    cashback = "cashback"
    unknown = "unknown"


class ValidationResultEnum(str, Enum):
    """Outcome of a single validation attempt (distinct from CouponStatus)."""

    valid = "valid"
    invalid = "invalid"
    unverifiable = "unverifiable"    # couldn't determine (blocked, timeout, layout change)


class IngestionMethod(str, Enum):
    """How a source's data reaches us. Affiliate APIs are preferred."""

    affiliate_api = "affiliate_api"
    scrape_requests = "scrape_requests"   # requests + BeautifulSoup
    scrape_playwright = "scrape_playwright"  # headless browser (JS/bot-protected)
