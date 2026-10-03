"""Admitad (Mitgo) coupons-API ingestor — global + India affiliate network.

Admitad uses OAuth2. Flow:
  1. POST client_credentials to `{base}/token/` (HTTP Basic client_id:client_secret)
     -> a bearer access token.
  2. GET `{base}/coupons/` with the token -> coupons (limit/offset paginated,
     `{results:[...], _meta:{count,limit,offset}}`).

Each coupon -> a RawCoupon: `promocode` gives the code (None -> code-less deal),
and `goto_link` is the Admitad affiliate deeplink (earns commission), used as
source_url. Merchant is the coupon's `campaign` name.

Defensive field mapping; confirm the live shape with the diagnostic then tighten:

    docker compose exec worker python -m scrapers.admitad_feed
"""
from __future__ import annotations

import base64
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
from scrapers.desidime import _parse_discount
from scrapers.linkmydeals_feed import _first

log = get_logger("ingest.admitad")

_TAG = re.compile(r"<[^>]+>")


def _clean(value: Any) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", _TAG.sub(" ", html.unescape(str(value)))).strip()


class MissingCredentials(RuntimeError):
    """Raised when the Admitad client id/secret are not configured."""


class AuthError(RuntimeError):
    """Raised when the OAuth token request fails."""


class AdmitadFeedScraper(BaseScraper):
    source_name = "Admitad"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        api_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        scope: str | None = None,
        website_id: str | None = None,
        limit: int = 200,
        max_pages: int = 50,
        session: requests.Session | None = None,
    ):
        s = get_settings()
        self.base = (api_url or s.admitad_api_url).rstrip("/")
        self.client_id = client_id if client_id is not None else s.admitad_client_id
        self.client_secret = (
            client_secret if client_secret is not None else s.admitad_client_secret
        )
        self.scope = scope if scope is not None else s.admitad_scope
        self.website_id = website_id if website_id is not None else s.admitad_website_id
        self.limit = limit
        self.max_pages = max_pages
        self.session = session or requests.Session()

    @property
    def token_url(self) -> str:
        return f"{self.base}/token/"

    @property
    def coupons_url(self) -> str:
        # A website id scopes coupons + their goto_link to that ad space.
        if self.website_id:
            return f"{self.base}/coupons/website/{self.website_id}/"
        return f"{self.base}/coupons/"

    # -- auth ----------------------------------------------------------------
    def authenticate(self) -> str:
        if not self.client_id or not self.client_secret:
            raise MissingCredentials(
                "Admitad client id/secret not set "
                "(ADMITAD_CLIENT_ID / ADMITAD_CLIENT_SECRET)."
            )
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = self.session.post(
            self.token_url,
            data={"grant_type": "client_credentials", "client_id": self.client_id,
                  "scope": self.scope},
            headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise AuthError(f"token HTTP {resp.status_code}: {resp.text[:300]}")
        token = (resp.json() or {}).get("access_token")
        if not token:
            raise AuthError("no access_token in token response")
        return str(token)

    # -- field mapping (confirm against a live sample) -----------------------
    def _map_coupon(self, it: dict, now: datetime) -> RawCoupon | None:
        if str(it.get("status") or "active").lower() not in ("active", ""):
            return None
        camp = it.get("campaign")
        merchant = (camp.get("name") if isinstance(camp, dict) else None) or _first(
            it, "advertiser_name", "campaign_name", "advcampaign_name",
        )
        ext = _first(it, "id", "coupon_id")
        if not merchant or ext in (None, ""):
            return None
        code = _first(it, "promocode", "promo_code", "coupon_code")
        name = _clean(_first(it, "name", "short_name"))
        description = _clean(_first(it, "description")) or name or f"{merchant} offer"
        discount_label = _clean(_first(it, "discount"))
        dtype, dval = _parse_discount(f"{discount_label} {name} {description}")
        # goto_link is the Admitad affiliate deeplink (earns commission).
        url = _first(it, "goto_link", "gotolink", "link", "landing")

        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=str(code).strip() if code else None,
            external_ref=f"admitad_{ext}",
            requires_reveal=False,
            description=description,
            discount_type=dtype,
            discount_value=dval,
            source_url=str(url).strip() if url else None,
            scraped_at=now,
            ingestion_method=self.ingestion_method,
        )

    # -- fetch ---------------------------------------------------------------
    def _fetch_page(self, token: str, offset: int) -> tuple[list[dict], int]:
        resp = self.session.get(
            self.coupons_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"limit": self.limit, "offset": offset},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        results = payload.get("results") or []
        count = ((payload.get("_meta") or {}).get("count")) or 0
        return [r for r in results if isinstance(r, dict)], int(count)

    def scrape(self) -> list[RawCoupon]:
        token = self.authenticate()  # raises MissingCredentials / AuthError
        log.info("admitad.fetch", url=self.coupons_url, limit=self.limit)
        now = datetime.now(timezone.utc)
        out: list[RawCoupon] = []
        seen: set[str] = set()

        for page in range(self.max_pages):
            rows, count = self._fetch_page(token, page * self.limit)
            if not rows:
                break
            for it in rows:
                rc = self._map_coupon(it, now)
                if rc is None or rc.external_ref in seen:
                    continue
                seen.add(rc.external_ref)
                out.append(rc)
            if (page + 1) * self.limit >= count:
                break

        codes = sum(1 for c in out if c.code)
        log.info("admitad.parsed", mapped=len(out), codes=codes, deals=len(out) - codes)
        return out


def diagnose() -> None:
    """Auth + print the REAL coupons shape so mapping can be confirmed.

        docker compose exec worker python -m scrapers.admitad_feed
    """
    s = get_settings()
    if not s.admitad_client_id or not s.admitad_client_secret:
        print("ADMITAD_CLIENT_ID / ADMITAD_CLIENT_SECRET not set — add to .env first.")
        return
    sc = AdmitadFeedScraper()
    print(f"POST {sc.token_url} (scope: {sc.scope})")
    try:
        token = sc.authenticate()
    except Exception as exc:  # noqa: BLE001
        print("AUTH FAILED:", exc)
        return
    print("auth OK — token length:", len(token))
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Your ad spaces ("websites") and how many coupons each can see. Coupons only
    # appear for advertiser programmes the website has joined.
    try:
        w = sc.session.get(f"{sc.base}/websites/v2/", headers=auth,
                           params={"limit": 20}, timeout=60)
        print("websites HTTP", w.status_code)
        sites = (w.json() or {}).get("results") or [] if w.ok else []
        if not w.ok:
            print("  ", w.text[:300])
        for site in sites:
            wid = site.get("id")
            c = sc.session.get(f"{sc.base}/coupons/website/{wid}/", headers=auth,
                               params={"limit": 1}, timeout=60)
            n = ((c.json() or {}).get("_meta") or {}).get("count") if c.ok else f"HTTP {c.status_code}"
            print(f"  website id={wid} name={site.get('name')!r} status={site.get('status')!r} "
                  f"→ coupons visible: {n}")
        if not sites:
            print("  (no websites on this account — add/approve one in the Admitad dashboard)")
        print("ADMITAD_WEBSITE_ID currently:", sc.website_id or "(not set)")
    except Exception as exc:  # noqa: BLE001
        print("websites lookup failed:", exc)

    print(f"GET {sc.coupons_url} (limit 3)")
    resp = sc.session.get(
        sc.coupons_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"limit": 3, "offset": 0}, timeout=60,
    )
    print("HTTP", resp.status_code)
    try:
        payload = resp.json()
    except Exception:
        print("non-JSON (first 400):", resp.text[:400])
        return
    if isinstance(payload, dict):
        print("envelope keys:", list(payload.keys()))
        print("_meta:", payload.get("_meta"))
    results = payload.get("results") or []
    print("coupons on page 1:", len(results))
    if results:
        print("FIRST COUPON KEYS:", list(results[0].keys()))
        print(json.dumps(results[0], indent=2, ensure_ascii=False)[:1800])
        mapped = sc._map_coupon(results[0], datetime.now(timezone.utc))
        print("MAPPED ->", mapped.model_dump() if mapped else None)


if __name__ == "__main__":
    diagnose()
