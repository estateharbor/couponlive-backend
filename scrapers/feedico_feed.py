"""Feedico coupon-catalog ingestor (third affiliate coupon source).

Feedico aggregates 40k+ merchants / 70k+ *deduplicated* promo codes across many
networks (CJ, Awin, Impact, Admitad, …) behind one REST API. We call its catalog
endpoint and flow results through the same normalize -> dedupe -> store pipeline.

API (https://feedico.io):
  POST /api/v1/catalog/coupons   (auth: `Authorization: Bearer fdco_<token>`)
  Free tier: 1000 requests/month — so sync a couple times a day, not hourly.

IMPORTANT — DISCOVERY ONLY: Feedico's catalog returns the code + merchant but NO
publisher-specific affiliate tracking link, so these codes DON'T earn commission
(unlike Cuelinks). They still add real coverage to the directory. `source_url` is
set to the merchant site when present, else left empty.

FIELD MAPPING is defensive (the interactive docs aren't fetchable). Confirm the
real shape with the built-in diagnostic once a key is set, then tighten:

    docker compose exec worker python -m scrapers.feedico_feed

`FEEDICO_API_URL` / `FEEDICO_COUNTRY` are env-overridable so we can correct the
endpoint or region filter without a code change.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

import requests

from core.config import get_settings
from core.logging import get_logger
from models.enums import IngestionMethod
from models.schemas import RawCoupon
from scrapers.base import BaseScraper
from scrapers.cuelinks_feed import _clean  # decode entities + strip HTML tags
from scrapers.desidime import _parse_discount
from scrapers.linkmydeals_feed import _extract_offers, _first

log = get_logger("ingest.feedico")


class MissingCredentials(RuntimeError):
    """Raised when the Feedico API key is not configured."""


def _offers_from(payload: Any) -> list[dict]:
    """Find the coupons list across envelope shapes. Falls back to the first
    list-of-dicts value when the wrapper key is unfamiliar (e.g. `items`)."""
    offers = _extract_offers(payload)
    if offers:
        return offers
    if isinstance(payload, dict):
        for val in payload.values():
            if isinstance(val, list) and any(isinstance(x, dict) for x in val):
                return [x for x in val if isinstance(x, dict)]
    return []


class FeedicoFeedScraper(BaseScraper):
    source_name = "Feedico"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        country: str | None = None,
        max_pages: int = 20,
        page_size: int = 200,
        session: requests.Session | None = None,
    ):
        s = get_settings()
        self.api_url = api_url or s.feedico_api_url
        self.api_key = api_key if api_key is not None else s.feedico_api_key
        self.country = country if country is not None else s.feedico_country
        self.max_pages = max_pages
        self.page_size = page_size
        self.session = session or requests.Session()

    # -- field mapping (confirm against a live sample via the diagnostic) ----
    def _map_offer(self, item: dict, fetched_at: datetime) -> RawCoupon | None:
        merchant = _first(item, "merchant", "brandName", "brand_name", "networkName",
                          "store", "store_name", "merchant_name")
        code = _first(item, "code", "coupon_code", "couponCode")
        external_ref = _first(item, "id", "couponId", "coupon_id", "uid", "_id")
        # Need a merchant AND something to dedup on: a code, or a stable id.
        if not merchant or (not code and external_ref in (None, "")):
            return None

        title = _clean(_first(item, "title", "offerTitle", "offer_title", "name"))
        description = _clean(_first(item, "description", "desc", "details"))
        dtype, dval = _parse_discount(f"{title} {description}")

        # Discovery-only: no tracking link. Use whatever landing URL is present.
        url = _first(item, "offerUrl", "offer_url", "trackingUrl", "url", "link",
                    "merchantWebsiteUrl", "merchant_website_url", "website")
        text = title or description or None

        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=str(code).strip() if code else None,
            external_ref=str(external_ref).strip() if external_ref else None,
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
        return {"Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json", "Content-Type": "application/json"}

    def _body(self, page: int) -> dict[str, Any]:
        body: dict[str, Any] = {"page": page, "limit": self.page_size}
        if self.country:
            body["country"] = self.country
        return body

    def _fetch_page(self, page: int) -> list[dict]:
        resp = self.session.post(
            self.api_url, headers=self._headers(), json=self._body(page), timeout=60
        )
        resp.raise_for_status()
        return _offers_from(resp.json())

    def scrape(self) -> list[RawCoupon]:
        if not self.api_key:
            raise MissingCredentials("Feedico API key not set (FEEDICO_API_KEY).")

        log.info("feedico.fetch", url=self.api_url, country=self.country, max_pages=self.max_pages)
        fetched_at = datetime.now(timezone.utc)
        active: list[RawCoupon] = []
        seen: set[str] = set()
        total_raw = 0

        for page in range(1, self.max_pages + 1):
            offers = self._fetch_page(page)
            if not offers:
                break
            total_raw += len(offers)
            new_on_page = 0
            for it in offers:
                rc = self._map_offer(it, fetched_at)
                if rc is None:
                    continue
                key = rc.external_ref or f"{rc.merchant_name.lower()}|{rc.code}"
                if key in seen:
                    continue
                seen.add(key)
                active.append(rc)
                new_on_page += 1
            if new_on_page == 0:
                break

        codes = sum(1 for c in active if c.code)
        log.info("feedico.parsed", total=total_raw, mapped=len(active),
                 codes=codes, deals=len(active) - codes)
        return active


def diagnose() -> None:
    """Print the REAL response shape so `_map_offer` can be tightened.

        docker compose exec worker python -m scrapers.feedico_feed
    """
    s = get_settings()
    if not s.feedico_api_key:
        print("FEEDICO_API_KEY not set — add it to .env first.")
        return
    sc = FeedicoFeedScraper()
    print(f"POST {sc.api_url}  body={sc._body(1)}")
    resp = sc.session.post(sc.api_url, headers=sc._headers(), json=sc._body(1), timeout=60)
    print("HTTP", resp.status_code)
    try:
        payload = resp.json()
    except Exception:
        print("non-JSON response (first 500 chars):")
        print(resp.text[:500])
        return
    offers = _offers_from(payload)
    print(f"offers found on page 1: {len(offers)}")
    if isinstance(payload, dict):
        print("envelope keys:", list(payload.keys()))
    if offers:
        sample = offers[0]
        print("FIRST OFFER KEYS:", list(sample.keys()))
        print("FIRST OFFER JSON:")
        print(json.dumps(sample, indent=2, ensure_ascii=False)[:1500])
        mapped = FeedicoFeedScraper()._map_offer(sample, datetime.now(timezone.utc))
        print("MAPPED ->", mapped.model_dump() if mapped else None)


if __name__ == "__main__":
    diagnose()
