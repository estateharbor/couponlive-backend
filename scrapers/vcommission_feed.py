"""vCommission (Trackier) publisher-API ingestor — India affiliate network.

vCommission runs on the Trackier platform. Three publisher endpoints matter:
  * `/v2/publisher/campaigns`  -> campaigns (merchant programs) WITH the affiliate
    `tracking_link`; page-based (`page`/`limit`, `data.campaigns`, `data.count`).
  * `/v2/publishers/coupons`   -> coupon CODES; token-paginated (`coupons`, `pageToken`).
  * `/v2/publishers/deals`     -> code-less deals; token-paginated (`deals`, `pageToken`).

The coupons/deals feeds carry only `campaign_id` (no URL), so we first build a
`campaign_id -> tracking_link` map from the campaigns feed and join it on, so
every coupon/deal clicks out through the TRACKED affiliate link (earns commission).

Auth: `X-Api-Key` header (per docs) + `apiKey` query param (per the vCommission
dashboard) — we send both so it works either way.

Confirm the live shape with the built-in diagnostic, then tighten if needed:

    docker compose exec worker python -m scrapers.vcommission_feed
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from core.config import get_settings
from core.logging import get_logger
from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.base import BaseScraper
from scrapers.desidime import _parse_discount

log = get_logger("ingest.vcommission")

_TAG = re.compile(r"<[^>]+>")


def _clean(value: Any) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", _TAG.sub(" ", html.unescape(str(value)))).strip()


# Trackier campaign names arrive as "Ajio.com Ecommerce CPS - India" — strip the
# payout-model / vertical / country cruft so the merchant is just "Ajio" (clean
# display AND dedupe with existing merchants).
_CM_COUNTRY = re.compile(
    r"\s*[-–|]\s*(india|in|uae|usa?|uk|global|ww|worldwide|bangladesh|nepal|"
    r"sri ?lanka|indonesia|singapore|malaysia|row|intl|international)\s*$",
    re.IGNORECASE,
)
_CM_MODEL = re.compile(
    r"\b(e-?commerce|cps|cpl|cpa|cpi|cpv|cpd|cpc|cpe|incent|non-?incent|"
    r"affiliate|coupons?|offers?|mobile app|app install)\b",
    re.IGNORECASE,
)
_CM_TLD = re.compile(r"\.(com|co\.in|in|net|org|shop|store|io|app)\b", re.IGNORECASE)


def _clean_merchant(name: Any) -> str:
    raw = str(name or "").strip()
    n = _CM_COUNTRY.sub("", raw)
    n = _CM_TLD.sub("", n)
    n = _CM_MODEL.sub(" ", n)
    n = re.sub(r"\s*[-–|]\s*", " ", n)      # leftover separators
    n = re.sub(r"\s+", " ", n).strip(" -–|")
    return n or raw


def _clean_tracking(url: Any) -> str | None:
    """Drop Trackier's `{placeholder}` query params (p1={your-transaction-id}, …)
    so the stored click-out is a clean, working affiliate deeplink."""
    if not url:
        return None
    try:
        p = urlsplit(str(url))
        kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if "{" not in k and "{" not in v]
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(kept), p.fragment))
    except Exception:
        return str(url)


class MissingCredentials(RuntimeError):
    """Raised when the vCommission API key is not configured."""


class VCommissionFeedScraper(BaseScraper):
    source_name = "vCommission"
    ingestion_method = IngestionMethod.affiliate_api

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        max_pages: int = 20,
        session: requests.Session | None = None,
    ):
        settings = get_settings()
        self.base = (api_url or settings.vcommission_api_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.vcommission_api_key
        self.max_pages = max_pages
        self.session = session or requests.Session()

    # -- endpoints (note: campaigns is singular, coupons/deals are plural) ----
    @property
    def campaigns_url(self) -> str:
        return f"{self.base}/publisher/campaigns"

    @property
    def coupons_url(self) -> str:
        return f"{self.base}/publishers/coupons"

    @property
    def deals_url(self) -> str:
        return f"{self.base}/publishers/deals"

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    # -- campaign map: campaign_id -> tracking_link (the affiliate deeplink) --
    def _fetch_campaign_links(self) -> dict[str, str]:
        links: dict[str, str] = {}
        for page in range(1, self.max_pages + 1):
            resp = self.session.get(
                self.campaigns_url,
                headers=self._headers(),
                # NB: showApproved=1 returns 0 campaigns for this account — omit it
                # so we get the full set (with tracking_link) to join on.
                params={"apiKey": self.api_key, "limit": 1000, "page": page},
                timeout=60,
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or {}
            campaigns = data.get("campaigns") or []
            if not campaigns:
                break
            for c in campaigns:
                cid = c.get("id")
                link = _clean_tracking(c.get("tracking_link"))
                if cid is not None and link:
                    links[str(cid)] = link
            # Stop once we've seen everything the feed reports.
            if len(campaigns) < 1000:
                break
        log.info("vcommission.campaigns", linked=len(links))
        return links

    # -- token-paginated fetch for coupons / deals ---------------------------
    def _fetch_paged(self, url: str, key: str) -> list[dict]:
        items: list[dict] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(self.max_pages):
            params = {"apiKey": self.api_key}
            if token:
                params["pageToken"] = token
            resp = self.session.get(url, headers=self._headers(), params=params, timeout=60)
            resp.raise_for_status()
            payload = resp.json() or {}
            rows = payload.get(key) or []
            items.extend(r for r in rows if isinstance(r, dict))
            # The feed exposes both pageToken and nextPageToken; prefer the latter.
            nxt = payload.get("nextPageToken") or payload.get("pageToken") or None
            if not rows or not nxt or nxt in seen:
                break
            seen.add(nxt)
            token = nxt
        return items

    # -- mapping -------------------------------------------------------------
    def _map_coupon(self, it: dict, links: dict[str, str], now: datetime) -> RawCoupon | None:
        if str(it.get("status") or "active").lower() != "active":
            return None  # skip pending/expired
        merchant = _clean_merchant(it.get("campaign_name"))
        code = it.get("code")
        ext = it.get("id")
        if not merchant or not code or ext in (None, ""):
            return None
        desc = _clean(it.get("description")) or f"{merchant} coupon"
        dtype, dval = _parse_discount(desc)
        link = links.get(str(it.get("campaign_id")))
        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=str(code).strip(),
            external_ref=f"vc_coupon_{ext}",
            requires_reveal=False,
            description=desc,
            discount_type=dtype,
            discount_value=dval,
            source_url=link,  # affiliate tracking link (None -> merchant homepage fallback)
            scraped_at=now,
            ingestion_method=self.ingestion_method,
        )

    def _map_deal(self, it: dict, links: dict[str, str], now: datetime) -> RawCoupon | None:
        if str(it.get("status") or "active").lower() != "active":
            return None
        merchant = _clean_merchant(it.get("campaign_name"))
        ext = it.get("id")
        if not merchant or ext in (None, ""):
            return None
        text = _clean(it.get("description")) or _clean(it.get("name")) or f"{merchant} deal"
        dtype, dval = _parse_discount(text)
        link = links.get(str(it.get("campaign_id")))
        return RawCoupon(
            merchant_name=str(merchant).strip(),
            code=None,
            external_ref=f"vc_deal_{ext}",
            requires_reveal=False,
            description=text,
            discount_type=dtype,
            discount_value=dval,
            source_url=link,
            scraped_at=now,
            ingestion_method=self.ingestion_method,
        )

    def scrape(self) -> list[RawCoupon]:
        if not self.api_key:
            raise MissingCredentials("vCommission API key not set (VCOMMISSION_API_KEY).")
        now = datetime.now(timezone.utc)
        links = self._fetch_campaign_links()

        out: list[RawCoupon] = []
        for it in self._fetch_paged(self.coupons_url, "coupons"):
            rc = self._map_coupon(it, links, now)
            if rc:
                out.append(rc)
        for it in self._fetch_paged(self.deals_url, "deals"):
            rc = self._map_deal(it, links, now)
            if rc:
                out.append(rc)

        codes = sum(1 for c in out if c.code)
        log.info("vcommission.parsed", mapped=len(out), codes=codes, deals=len(out) - codes)
        return out


def diagnose() -> None:
    """Print the REAL campaign/coupon/deal shapes so mapping can be confirmed.

        docker compose exec worker python -m scrapers.vcommission_feed
    """
    settings = get_settings()
    if not settings.vcommission_api_key:
        print("VCOMMISSION_API_KEY not set — add it to .env first.")
        return
    s = VCommissionFeedScraper()

    for label, url, key in [
        ("CAMPAIGNS", s.campaigns_url, None),
        ("COUPONS", s.coupons_url, "coupons"),
        ("DEALS", s.deals_url, "deals"),
    ]:
        print(f"\n=== {label}: GET {url} ===")
        params = {"apiKey": s.api_key}
        if label == "CAMPAIGNS":
            params.update({"limit": 3, "page": 1})
        resp = s.session.get(url, headers=s._headers(), params=params, timeout=60)
        print("HTTP", resp.status_code)
        try:
            payload = resp.json()
        except Exception:
            print("non-JSON (first 400):", resp.text[:400])
            continue
        if isinstance(payload, dict):
            print("envelope keys:", list(payload.keys()))
        rows = (payload.get("data", {}).get("campaigns") if label == "CAMPAIGNS"
                else payload.get(key)) or []
        print(f"{label} rows:", len(rows))
        if rows:
            print("FIRST KEYS:", list(rows[0].keys()))
            print(json.dumps(rows[0], indent=2, ensure_ascii=False)[:1200])

    print("\n=== full scrape() ===")
    try:
        out = s.scrape()
        codes = sum(1 for c in out if c.code)
        print(f"mapped: {len(out)} (codes {codes}, deals {len(out) - codes})")
        if out:
            print("SAMPLE MAPPED ->", out[0].model_dump())
    except Exception as exc:  # noqa: BLE001
        print("scrape failed:", exc)


if __name__ == "__main__":
    diagnose()
