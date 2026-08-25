"""INRDeals affiliate-API ingestor.

Unlike the HTML scrapers, this pulls *real coupon codes* from an authorized
affiliate feed, so `RawCoupon.code` is populated and `requires_reveal=False`.
It implements the same `BaseScraper` interface, so it flows through the exact
same normalize -> dedupe -> store pipeline as any scraper.

API (verified Aug 2026):
  GET https://inrdeals.com/api/v1/coupon-feed
  required params: token (API token), id (username), store_id (merchant id)
  optional params: search, category, start_date, end_date

CREDENTIALS: read from env (`INRDEALS_API_KEY`, `INRDEALS_USERNAME`) via
core.config — never hard-coded. If absent, `scrape()` raises
`MissingCredentials` so the scheduler skips this source cleanly.

FIELD MAPPING CAVEAT: INRDeals does not publish the response JSON schema
openly. `_map_coupon` maps defensively across the likely field names; confirm
against the first live response and tighten if needed (one place to change).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from core.config import get_settings
from core.logging import get_logger
from core.retry import polite_delay
from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.base import BaseScraper
from scrapers.desidime import _parse_discount  # reuse the discount heuristic

log = get_logger("ingest.inrdeals")

API_URL = "https://inrdeals.com/api/v1/coupon-feed"


class MissingCredentials(RuntimeError):
    """Raised when INRDeals token/username are not configured."""


def _first(item: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-empty value among candidate keys."""
    for k in keys:
        if k in item and item[k] not in (None, "", []):
            return item[k]
    return None


def _extract_items(payload: Any) -> list[dict]:
    """Find the list of coupon dicts regardless of envelope shape."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "coupons", "result", "results", "response"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        # Some feeds nest one level deeper: {"data": {"coupons": [...]}}
        for val in payload.values():
            if isinstance(val, dict):
                nested = _extract_items(val)
                if nested:
                    return nested
    return []


class InrdealsIngestor(BaseScraper):
    source_name = "INRDeals"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        store_ids: list[int] | None = None,
        token: str | None = None,
        username: str | None = None,
        session: requests.Session | None = None,
    ):
        settings = get_settings()
        self.token = token if token is not None else settings.inrdeals_api_key
        self.username = username if username is not None else settings.inrdeals_username
        # Iterate specific merchant ids; empty -> one call for the latest feed.
        self.store_ids = store_ids or []
        self.session = session or requests.Session()

    # -- mapping -----------------------------------------------------------
    def _map_coupon(self, item: dict, fetched_at: datetime) -> RawCoupon | None:
        # INRDeals nests the merchant name under logo.store_name.
        merchant = _first(item, "store", "store_name", "merchant", "merchant_name")
        if not merchant:
            logo = item.get("logo")
            if isinstance(logo, dict):
                merchant = _first(logo, "store_name", "name")

        code = _first(item, "coupon_code", "code", "couponcode", "coupon")
        if not merchant or not code:
            return None  # a deal without a code isn't a coupon for our purposes

        # "label" is the clean discount (e.g. "30% OFF"); "offer" is the title.
        label = _first(item, "label")
        offer = _first(item, "offer", "offer_title", "title", "name")
        dtype, dval = _parse_discount(f"{label or ''} {offer or ''}")

        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=str(code).strip(),
            external_ref=str(_first(item, "id", "coupon_id", "cid") or "") or None,
            requires_reveal=False,               # affiliate feed gives the real code
            description=(str(offer).strip() if offer else str(label).strip() if label else None),
            discount_type=dtype,
            discount_value=dval,
            source_url=_first(item, "url", "affiliate_url", "tracking_url", "link"),
            scraped_at=fetched_at,
            ingestion_method=self.ingestion_method,
        )

    # -- fetch -------------------------------------------------------------
    def _params(self, store_id: int | None) -> dict[str, Any]:
        p = {"token": self.token, "id": self.username}
        if store_id is not None:
            p["store_id"] = store_id
        return p

    def _fetch(self, store_id: int | None) -> list[dict]:
        resp = self.session.get(API_URL, params=self._params(store_id), timeout=25)
        resp.raise_for_status()
        return _extract_items(resp.json())

    def scrape(self) -> list[RawCoupon]:
        if not self.token or not self.username:
            raise MissingCredentials(
                "INRDeals token/username not set (INRDEALS_API_KEY / INRDEALS_USERNAME)."
            )

        fetched_at = datetime.now(timezone.utc)
        targets: list[int | None] = list(self.store_ids) or [None]
        out: list[RawCoupon] = []
        for i, store_id in enumerate(targets):
            log.info("inrdeals.fetch", store_id=store_id)
            items = self._fetch(store_id)
            for it in items:
                rc = self._map_coupon(it, fetched_at)
                if rc is not None:
                    out.append(rc)
            if i < len(targets) - 1:
                polite_delay()
        log.info("inrdeals.parsed", count=len(out), stores=len(targets))
        return out
