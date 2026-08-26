"""LinkMyDeals coupon-feed ingestor.

LinkMyDeals is a coupon-feed API aggregator: instead of scraping HTML we call
their REST `getOffers` endpoint and get structured coupon data, which flows
through the exact same normalize -> dedupe -> store pipeline as any scraper.
Implements `BaseScraper` like every other source.

API (https://linkmydeals.com/api-documentation/):
  GET https://feed.linkmydeals.com/getOffers/
  params: API_KEY, format=json, incremental=0/1, last_extract=<unix>, off_record=0/1
  Incremental feed returns only new/updated/suspended offers since last_extract,
  each carrying a `status` field.

INCREMENTAL: the caller (scheduler.tasks.sync_linkmydeals) passes `last_extract`
from the persisted `sources.sync_cursor`; None => a full pull. We set
`off_record=1` so LinkMyDeals doesn't move its own server-side timestamp — we
manage the cursor ourselves.

SUSPENDED: offers with status "suspended" are collected in `self.suspended`
(NOT returned as active coupons); the task expires the matching rows directly.

FIELD MAPPING: see `_map_response`. Their docs give labels ("LMD ID", "Coupon
Code"); real JSON keys are lowercased. Mapping is defensive (`_first` across
likely key names) — confirm against the first live pull and tighten if needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from core.config import get_settings
from core.logging import get_logger
from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.base import BaseScraper

log = get_logger("ingest.linkmydeals")


class MissingCredentials(RuntimeError):
    """Raised when the LinkMyDeals API key is not configured."""


@dataclass
class SuspendedOffer:
    """A supplier 'suspended' signal — used to expire our matching coupon."""

    merchant_name: str
    code: str | None
    external_ref: str | None


def _first(item: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in item and item[k] not in (None, "", []):
            return item[k]
    return None


def _extract_offers(payload: Any) -> list[dict]:
    """Find the offers list regardless of envelope (offers/result/data or bare list)."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("offers", "result", "results", "data", "coupons"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        for val in payload.values():
            if isinstance(val, dict):
                nested = _extract_offers(val)
                if nested:
                    return nested
    return []


# LinkMyDeals "Offer" (type of offer) -> our DiscountType.
_OFFER_TYPE_MAP = {
    "percentage off": DiscountType.percentage,
    "percentage": DiscountType.percentage,
    "price off": DiscountType.fixed,
    "amount off": DiscountType.fixed,
    "flat": DiscountType.fixed,
    "cashback": DiscountType.cashback,
    "bogo": DiscountType.bogo,
    "buy one get one": DiscountType.bogo,
    "free shipping": DiscountType.free_shipping,
    "free delivery": DiscountType.free_shipping,
}

_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _num(text: Any) -> float | None:
    """First number in a string like '50%', '₹200', 'Flat 30', '50'."""
    if text is None:
        return None
    m = _NUMBER.search(str(text))
    return float(m.group(1)) if m else None


def _map_discount(offer_type: Any, offer_value: Any) -> tuple[DiscountType, float | None]:
    dtype = DiscountType.unknown
    if offer_type:
        key = str(offer_type).strip().lower()
        for needle, dt in _OFFER_TYPE_MAP.items():
            if needle in key:
                dtype = dt
                break
    # % sign in the value strongly implies percentage even if type is vague.
    if dtype is DiscountType.unknown and offer_value and "%" in str(offer_value):
        dtype = DiscountType.percentage
    return dtype, _num(offer_value)


class LinkMyDealsFeedScraper(BaseScraper):
    source_name = "LinkMyDeals"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        last_extract: int | None = None,
        session: requests.Session | None = None,
    ):
        settings = get_settings()
        self.api_url = api_url or settings.linkmydeals_api_url
        self.api_key = api_key if api_key is not None else settings.linkmydeals_api_key
        self.last_extract = last_extract          # None => full pull
        self.session = session or requests.Session()
        self.suspended: list[SuspendedOffer] = []

    # -- field mapping (confirm against a live sample) ---------------------
    def _map_response(self, item: dict, fetched_at: datetime) -> RawCoupon | None:
        merchant = _first(item, "store", "store_name", "merchant", "merchant_name")
        external_ref = _first(item, "lmd_id", "lmdid", "id", "offer_id")
        # Deals have an empty code; kept as code-less offers, identified by LMD ID.
        code = _first(item, "code", "coupon_code", "couponcode")
        if not merchant or external_ref in (None, ""):
            return None  # no stable identity -> skip rather than create junk

        title = _first(item, "title", "offer_text", "long_offer", "offer_name")
        offer_type = _first(item, "offer", "offer_type", "type_of_offer")
        offer_value = _first(item, "offer_value", "value", "discount")
        dtype, dval = _map_discount(offer_type, offer_value)

        # Prefer the affiliate deeplink (smartlink), else the plain landing URL.
        url = _first(item, "smartlink", "smartLink", "affiliate_link", "url", "deeplink")

        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=str(code).strip() if code else None,
            external_ref=str(external_ref).strip(),
            requires_reveal=False,               # feed gives the real code
            description=str(title).strip() if title else None,
            discount_type=dtype,
            discount_value=dval,
            source_url=str(url).strip() if url else None,
            scraped_at=fetched_at,
            ingestion_method=self.ingestion_method,
        )

    def _suspended_offer(self, item: dict) -> SuspendedOffer:
        return SuspendedOffer(
            merchant_name=str(_first(item, "store", "store_name", "merchant") or "").strip(),
            code=(str(_first(item, "code", "coupon_code") or "").strip() or None),
            external_ref=(str(_first(item, "lmd_id", "lmdid", "id") or "").strip() or None),
        )

    # -- fetch -------------------------------------------------------------
    def _params(self) -> dict[str, Any]:
        p: dict[str, Any] = {"API_KEY": self.api_key, "format": "json", "off_record": 1}
        if self.last_extract:
            p["incremental"] = 1
            p["last_extract"] = int(self.last_extract)
        return p

    def scrape(self) -> list[RawCoupon]:
        if not self.api_key:
            raise MissingCredentials("LinkMyDeals API key not set (LINKMYDEALS_API_KEY).")

        log.info("linkmydeals.fetch", incremental=bool(self.last_extract),
                 last_extract=self.last_extract)
        resp = self.session.get(self.api_url, params=self._params(), timeout=60)
        resp.raise_for_status()
        offers = _extract_offers(resp.json())

        fetched_at = datetime.now(timezone.utc)
        active: list[RawCoupon] = []
        self.suspended = []
        for it in offers:
            status = str(_first(it, "status") or "").strip().lower()
            if status == "suspended":
                self.suspended.append(self._suspended_offer(it))
                continue
            rc = self._map_response(it, fetched_at)
            if rc is not None:
                active.append(rc)

        log.info("linkmydeals.parsed", active=len(active),
                 suspended=len(self.suspended), total=len(offers))
        return active
