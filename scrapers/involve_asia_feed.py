"""Involve Asia Offers-API ingestor (affiliate coupon/deal source for India + SE Asia).

Involve Asia is an affiliate network (Myntra, Nykaa, Klook, …). Its API is a
TWO-STEP flow, unlike Cuelinks/LinkMyDeals:
  1. POST key+secret to `{base}/authenticate`  -> a bearer token.
  2. POST page/limit to `{base}/offers/all` with `Authorization: Bearer <token>`
     -> a page of offers.

Each offer becomes a `RawCoupon`:
  * with a `coupon_code` when the offer carries one (joins the codes directory),
  * else code-less (a deal), identified by `external_ref` (the offer id).

The TRACKING link (`tracking_link`) is the affiliate deeplink that earns
commission, so it is preferred for `source_url`.

FIELD MAPPING is DEFENSIVE across likely key names because the exact live shape
can vary by account. Confirm it against a live sample with the built-in
diagnostic, then tighten `_map_offer`:

    docker compose exec worker python -m scrapers.involve_asia_feed

Endpoints are derived from `INVOLVE_ASIA_API_URL` (base), so a different base can
be set in .env without a code change.
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
from scrapers.desidime import _parse_discount  # "50% off" / "₹200 off" heuristic
from scrapers.linkmydeals_feed import _OFFER_TYPE_MAP, _extract_offers, _first

log = get_logger("ingest.involve_asia")

_TAG = re.compile(r"<[^>]+>")
# Offer statuses we treat as usable; empty is allowed (some responses omit it).
_LIVE_STATUSES = {"", "active", "live", "running", "enabled", "approved", "public"}


def _clean(value: Any) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", _TAG.sub(" ", html.unescape(str(value)))).strip()


class MissingCredentials(RuntimeError):
    """Raised when the Involve Asia key/secret are not configured."""


class AuthError(RuntimeError):
    """Raised when /authenticate does not return a usable token."""


def _map_discount(offer_label: Any, text: str) -> tuple[DiscountType, float | None]:
    dtype, dval = _parse_discount(text or "")
    if offer_label:
        key = str(offer_label).strip().lower().replace("-", " ")
        for needle, dt in _OFFER_TYPE_MAP.items():
            if needle in key:
                dtype = dt
                break
    return dtype, dval


class InvolveAsiaFeedScraper(BaseScraper):
    source_name = "Involve Asia"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        max_pages: int = 20,
        limit: int = 100,
        session: requests.Session | None = None,
    ):
        settings = get_settings()
        self.base = (api_url or settings.involve_asia_api_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.involve_asia_api_key
        self.api_secret = (
            api_secret if api_secret is not None else settings.involve_asia_api_secret
        )
        self.max_pages = max_pages
        self.limit = limit
        self.session = session or requests.Session()

    # -- endpoints -----------------------------------------------------------
    @property
    def auth_url(self) -> str:
        return f"{self.base}/authenticate"

    @property
    def offers_url(self) -> str:
        return f"{self.base}/offers/all"

    # -- auth ----------------------------------------------------------------
    def authenticate(self) -> str:
        """Exchange key+secret for a bearer token."""
        if not self.api_key or not self.api_secret:
            raise MissingCredentials(
                "Involve Asia key/secret not set "
                "(INVOLVE_ASIA_API_KEY / INVOLVE_ASIA_API_SECRET)."
            )
        resp = self.session.post(
            self.auth_url,
            data={"key": self.api_key, "secret": self.api_secret},
            headers={"Accept": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        # Token nests under data.token on the documented response; be defensive.
        token = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                token = _first(data, "token", "access_token", "bearer", "jwt")
            token = token or _first(payload, "token", "access_token")
        if not token:
            raise AuthError(f"no token in authenticate response: keys={_keys(payload)}")
        return str(token)

    # -- field mapping (confirm against a live sample via the diagnostic) -----
    def _map_offer(self, item: dict, fetched_at: datetime) -> RawCoupon | None:
        merchant = _first(
            item, "offer_name", "merchant", "merchant_name", "campaign",
            "campaign_name", "store", "store_name", "advertiser", "advertiser_name",
        )
        external_ref = _first(item, "offer_id", "id", "campaign_id", "uid")
        code = _first(item, "coupon_code", "code", "coupon", "voucher_code", "promo_code")
        if not merchant or external_ref in (None, ""):
            return None  # no stable identity -> skip rather than store junk

        title = _clean(_first(item, "title", "offer_title", "coupon_title", "name"))
        description = _clean(
            _first(item, "description", "offer_description", "details", "preview")
        )
        offer_label = _first(item, "type", "offer_type", "coupon_type", "category")
        dtype, dval = _map_discount(offer_label, f"{title} {description}")

        # Prefer the TRACKING deeplink (earns commission) over a plain landing URL.
        url = _first(
            item, "tracking_link", "tracking_url", "trackinglink", "deeplink",
            "aff_link", "affiliate_link", "offer_link", "preview_url", "url", "link",
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
    def _fetch_page(self, token: str, page: int) -> list[dict]:
        resp = self.session.post(
            self.offers_url,
            data={"page": page, "limit": self.limit},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        return _extract_offers(resp.json())

    def scrape(self) -> list[RawCoupon]:
        token = self.authenticate()  # raises MissingCredentials / AuthError
        log.info("involve_asia.fetch", url=self.offers_url, max_pages=self.max_pages)
        fetched_at = datetime.now(timezone.utc)
        active: list[RawCoupon] = []
        seen_refs: set[str] = set()
        total_raw = 0

        for page in range(1, self.max_pages + 1):
            offers = self._fetch_page(token, page)
            if not offers:
                break
            total_raw += len(offers)
            new_on_page = 0
            for it in offers:
                status = str(_first(it, "status", "offer_status") or "").strip().lower()
                if status not in _LIVE_STATUSES:
                    continue
                rc = self._map_offer(it, fetched_at)
                if rc is None or rc.external_ref in seen_refs:
                    continue
                seen_refs.add(rc.external_ref)
                active.append(rc)
                new_on_page += 1
            if new_on_page == 0:
                break  # feed repeating / exhausted

        codes = sum(1 for c in active if c.code)
        log.info("involve_asia.parsed", total=total_raw, mapped=len(active),
                 codes=codes, deals=len(active) - codes)
        return active


def _keys(payload: Any) -> Any:
    return list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__


def diagnose() -> None:
    """Authenticate + print the REAL offers shape so `_map_offer` can be tightened.

        docker compose exec worker python -m scrapers.involve_asia_feed
    """
    settings = get_settings()
    if not settings.involve_asia_api_key or not settings.involve_asia_api_secret:
        print("INVOLVE_ASIA_API_KEY / INVOLVE_ASIA_API_SECRET not set — add to .env first.")
        return
    s = InvolveAsiaFeedScraper()
    print(f"POST {s.auth_url}")
    try:
        token = s.authenticate()
    except Exception as exc:  # noqa: BLE001 — diagnostic surface
        print("AUTH FAILED:", exc)
        return
    print("auth OK — token length:", len(token))

    print(f"POST {s.offers_url} (page 1, limit {s.limit})")
    resp = s.session.post(
        s.offers_url,
        data={"page": 1, "limit": s.limit},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=60,
    )
    print("HTTP", resp.status_code)
    try:
        payload = resp.json()
    except Exception:
        print("non-JSON response (first 500 chars):")
        print(resp.text[:500])
        return
    if isinstance(payload, dict):
        print("envelope keys:", list(payload.keys()))
    offers = _extract_offers(payload)
    print(f"offers found on page 1: {len(offers)}")
    if offers:
        sample = offers[0]
        print("FIRST OFFER KEYS:", list(sample.keys()))
        print("FIRST OFFER JSON:")
        print(json.dumps(sample, indent=2, ensure_ascii=False)[:1800])
        mapped = s._map_offer(sample, datetime.now(timezone.utc))
        print("MAPPED ->", mapped.model_dump() if mapped else None)


if __name__ == "__main__":
    diagnose()
