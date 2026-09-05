"""Cuelinks Offers-API ingestor (second affiliate coupon/deal source).

Cuelinks is an affiliate-network aggregator: its Offers API returns live
coupons AND deals across 400+ merchants (including Amazon India) in one feed, so
— like LinkMyDeals — we call the REST endpoint and flow the results through the
same normalize -> dedupe -> store pipeline. Implements `BaseScraper`.

Two kinds of offer arrive on the same feed:
  * **coupon codes** -> a `RawCoupon` WITH `code` (joins the honest codes directory),
  * **deals** (no code, e.g. most Amazon offers) -> a `RawCoupon` with `code=None`,
    identified by `external_ref`; these power the "Amazon Deals" section and never
    appear in the codes directory (which requires a non-null code).

AUTH: Cuelinks' current API authenticates with an `Authorization: Token <key>`
header; the older Offers endpoint used an `api_key` query param. We send BOTH so
the same code works regardless of which the account/endpoint expects.

FIELD MAPPING: the public docs are interactive (not fetchable), so `_map_offer`
maps DEFENSIVELY across the likely key names. Run the built-in diagnostic once a
key is configured to see the REAL keys, then tighten:

    docker compose exec worker python -m scrapers.cuelinks_feed

`CUELINKS_API_URL` is env-overridable in case the endpoint differs from the
default, so we can correct it without a code change.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

import requests

from core.config import get_settings
from core.logging import get_logger
from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.base import BaseScraper
from scrapers.desidime import _parse_discount  # reuse "50% off" / "₹200 off" heuristic
# Reuse the envelope/lookup helpers proven against the LinkMyDeals feed.
from scrapers.linkmydeals_feed import _OFFER_TYPE_MAP, _extract_offers, _first, _num

log = get_logger("ingest.cuelinks")

_TAG = re.compile(r"<[^>]+>")
# Cuelinks `status` values we treat as usable; anything else (expired/paused/…)
# is skipped. Empty status is allowed (older responses omit it).
_LIVE_STATUSES = {"", "live", "active", "running", "enabled"}


def _clean(value: Any) -> str:
    """Decode HTML entities, strip tags (descriptions arrive as <li> markup),
    and collapse whitespace."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", _TAG.sub(" ", html.unescape(str(value)))).strip()


class MissingCredentials(RuntimeError):
    """Raised when the Cuelinks API key is not configured."""


def _map_discount(offer_label: Any, text: str) -> tuple[DiscountType, float | None]:
    """Parse amount + type from the offer text, refining TYPE from a structured
    label (Percentage/Flat/Cashback/…) when Cuelinks provides one."""
    dtype, dval = _parse_discount(text or "")
    if offer_label:
        key = str(offer_label).strip().lower().replace("-", " ")
        for needle, dt in _OFFER_TYPE_MAP.items():
            if needle in key:
                dtype = dt
                break
    return dtype, dval


class CuelinksFeedScraper(BaseScraper):
    source_name = "Cuelinks"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        max_pages: int = 10,
        session: requests.Session | None = None,
    ):
        settings = get_settings()
        self.api_url = api_url or settings.cuelinks_api_url
        self.api_key = api_key if api_key is not None else settings.cuelinks_api_key
        self.max_pages = max_pages
        self.session = session or requests.Session()

    # -- field mapping (confirm against a live sample via the diagnostic) ----
    def _map_offer(self, item: dict, fetched_at: datetime) -> RawCoupon | None:
        # Merchant sits in `campaign` (confirmed live); keep other names as fallbacks.
        merchant = _first(
            item, "campaign", "merchant", "merchant_name", "store", "store_name",
            "campaign_name",
        )
        external_ref = _first(item, "id", "offer_id", "cuelink_id", "uid")
        code = _first(item, "coupon_code", "code", "coupon", "couponcode")
        if not merchant or external_ref in (None, ""):
            return None  # no stable identity -> skip rather than store junk

        # Decode entities + strip HTML (descriptions arrive as <li> markup).
        title = _clean(_first(item, "title", "offer_title", "name"))
        description = _clean(_first(item, "description", "offer_description", "long_description"))
        offer_label = _first(item, "type", "offer_type", "coupon_type")
        dtype, dval = _map_discount(offer_label, f"{title} {description}")

        # `affiliate_url` is the Cuelinks TRACKING link (earns commission) — prefer
        # it over the plain merchant `url`, which is NOT tracked.
        url = _first(
            item, "affiliate_url", "click_url", "cue_url", "tracking_url", "short_url",
            "url", "link", "offer_url", "deeplink", "smartlink", "affiliate_link",
        )
        text = title or description or None

        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=str(code).strip() if code else None,
            external_ref=str(external_ref).strip(),
            requires_reveal=False,
            description=str(text).strip() if text else None,
            discount_type=dtype,
            discount_value=dval,
            source_url=str(url).strip() if url else None,
            scraped_at=fetched_at,
            ingestion_method=self.ingestion_method,
        )

    # -- fetch ---------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.api_key}", "Accept": "application/json"}

    def _params(self, page: int) -> dict[str, Any]:
        # api_key covers the legacy query-param endpoint; header covers the new one.
        return {"api_key": self.api_key, "format": "json", "page": page}

    def _fetch_page(self, page: int) -> list[dict]:
        resp = self.session.get(
            self.api_url, headers=self._headers(), params=self._params(page), timeout=60
        )
        resp.raise_for_status()
        return _extract_offers(resp.json())

    def scrape(self) -> list[RawCoupon]:
        if not self.api_key:
            raise MissingCredentials("Cuelinks API key not set (CUELINKS_API_KEY).")

        log.info("cuelinks.fetch", url=self.api_url, max_pages=self.max_pages)
        fetched_at = datetime.now(timezone.utc)
        active: list[RawCoupon] = []
        seen_refs: set[str] = set()
        total_raw = 0

        for page in range(1, self.max_pages + 1):
            offers = self._fetch_page(page)
            if not offers:
                break  # ran past the last page
            total_raw += len(offers)
            new_on_page = 0
            for it in offers:
                # Skip offers the feed marks not-live (expired/paused/…).
                status = str(_first(it, "status") or "").strip().lower()
                if status not in _LIVE_STATUSES:
                    continue
                rc = self._map_offer(it, fetched_at)
                if rc is None or rc.external_ref in seen_refs:
                    continue
                seen_refs.add(rc.external_ref)
                active.append(rc)
                new_on_page += 1
            # A page that adds nothing new means the feed is repeating -> stop.
            if new_on_page == 0:
                break

        codes = sum(1 for c in active if c.code)
        log.info("cuelinks.parsed", total=total_raw, mapped=len(active),
                 codes=codes, deals=len(active) - codes)
        return active


def diagnose() -> None:
    """Print the REAL response shape so `_map_offer` can be tightened.

    Run inside the worker (which has network + the key):
        docker compose exec worker python -m scrapers.cuelinks_feed
    """
    settings = get_settings()
    if not settings.cuelinks_api_key:
        print("CUELINKS_API_KEY not set — add it to .env first.")
        return
    s = CuelinksFeedScraper()
    print(f"GET {s.api_url}")
    resp = s.session.get(s.api_url, headers=s._headers(), params=s._params(1), timeout=60)
    print("HTTP", resp.status_code)
    try:
        payload = resp.json()
    except Exception:
        print("non-JSON response (first 500 chars):")
        print(resp.text[:500])
        return
    offers = _extract_offers(payload)
    print(f"offers found on page 1: {len(offers)}")
    if isinstance(payload, dict):
        print("envelope keys:", list(payload.keys()))
    if offers:
        sample = offers[0]
        print("FIRST OFFER KEYS:", list(sample.keys()))
        print("FIRST OFFER JSON:")
        print(json.dumps(sample, indent=2, ensure_ascii=False)[:1500])
        mapped = CuelinksFeedScraper()._map_offer(sample, datetime.now(timezone.utc))
        print("MAPPED ->", mapped.model_dump() if mapped else None)


if __name__ == "__main__":
    diagnose()
