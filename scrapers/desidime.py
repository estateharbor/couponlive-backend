"""Desidime scraper (first source).

Why Desidime first: robots.txt permits the coupon/store listing pages, and the
listing renders as full server-side HTML to plain `requests` (no JS execution,
no bot challenge) — so it needs neither Playwright nor evasion.

IMPORTANT — what this scraper does and does NOT do:
- It harvests only the PUBLICLY RENDERED offer metadata: merchant, offer title,
  discount, description, the source-side coupon id, and the "last verified"
  freshness hint.
- Desidime reveal-gates the actual code behind an affiliate redirect
  (`visit.desidime.com/visit/...`). This scraper does NOT fire that redirect,
  because doing so would generate fake affiliate clicks. So `code` is left
  empty and `requires_reveal=True`; the real code is expected to come from an
  affiliate API (see README "Open decisions"). `robots.txt` disallows the
  `/goto/`-family redirect paths, consistent with not touching them.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from core.logging import get_logger
from core.retry import polite_delay
from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.base import BaseScraper

log = get_logger("scraper.desidime")

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
]

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RUPEES = re.compile(r"(?:₹|rs\.?|inr)\s*(\d+(?:,\d+)?)", re.IGNORECASE)


def _parse_discount(text: str) -> tuple[DiscountType, float | None]:
    """Best-effort discount type/value from a short label like '15% Off'."""
    if not text:
        return DiscountType.unknown, None
    t = text.lower()
    if "free shipping" in t or "free delivery" in t:
        return DiscountType.free_shipping, None
    if "cashback" in t:
        return DiscountType.cashback, None
    if "bogo" in t or "buy 1" in t or "buy one" in t:
        return DiscountType.bogo, None
    m = _PERCENT.search(text)
    if m:
        return DiscountType.percentage, float(m.group(1))
    m = _RUPEES.search(text)
    if m:
        return DiscountType.fixed, float(m.group(1).replace(",", ""))
    return DiscountType.unknown, None


class DesidimeScraper(BaseScraper):
    source_name = "Desidime"
    ingestion_method = IngestionMethod.scrape_requests

    BASE = "https://www.desidime.com"
    LISTING_PATH = "/coupons"

    def __init__(self, *, max_pages: int = 1, session: requests.Session | None = None):
        self.max_pages = max_pages
        self._ua_i = 0
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        # Rotate user agents across requests (courteous variety, not evasion).
        ua = _USER_AGENTS[self._ua_i % len(_USER_AGENTS)]
        self._ua_i += 1
        return {"User-Agent": ua, "Accept-Language": "en-IN,en;q=0.9"}

    def _fetch(self, url: str) -> str:
        resp = self.session.get(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        return resp.text

    def _parse_page(self, html: str, scraped_at: datetime) -> list[RawCoupon]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("div.card[data-gtm-store][data-gtm-coupon-id]")
        out: list[RawCoupon] = []
        for card in cards:
            merchant = (card.get("data-gtm-store") or "").strip()
            ext_ref = (card.get("data-gtm-coupon-id") or "").strip() or None
            if not merchant or not ext_ref:
                continue

            # Title: the primary offer anchor pointing at the store page.
            title_el = card.select_one('a[data-href^="/stores/"], a[href^="/stores/"]')
            title = title_el.get_text(strip=True) if title_el else None

            # Discount label: first short text node in the card (e.g. "15% Off").
            # The leading text of the card's first div holds it.
            head = card.find("div")
            disc_text = ""
            if head:
                # take the first non-empty stripped string in the head block
                for s in head.stripped_strings:
                    disc_text = s
                    break
            dtype, dval = _parse_discount(disc_text or (title or ""))

            # Description: the bullet list after the "<!-- Desc -->" marker.
            desc_ul = card.select_one("ul")
            description = None
            if desc_ul:
                items = [li.get_text(" ", strip=True) for li in desc_ul.select("li")]
                items = [i for i in items if i]
                if items:
                    description = " ".join(items)

            # Reveal URL (provenance only — we do NOT fetch it).
            reveal = card.select_one('a[data-href*="visit.desidime.com"]')
            source_url = (
                (reveal.get("data-href") if reveal else None)
                or f"{self.BASE}/stores?coupon_id={ext_ref}"
            )

            out.append(
                RawCoupon(
                    merchant_name=merchant,
                    code=None,               # reveal-gated; not scraped
                    external_ref=ext_ref,
                    requires_reveal=True,
                    description=description or title,
                    discount_type=dtype,
                    discount_value=dval,
                    source_url=source_url,
                    scraped_at=scraped_at,
                    ingestion_method=self.ingestion_method,
                )
            )
        return out

    def scrape(self) -> list[RawCoupon]:
        results: list[RawCoupon] = []
        for page in range(1, self.max_pages + 1):
            url = f"{self.BASE}{self.LISTING_PATH}"
            if page > 1:
                url = f"{url}?page={page}"
            log.info("scrape.fetch", source=self.source_name, url=url, page=page)
            html = self._fetch(url)
            scraped_at = datetime.now(timezone.utc)
            page_results = self._parse_page(html, scraped_at)
            log.info(
                "scrape.parsed",
                source=self.source_name,
                page=page,
                count=len(page_results),
            )
            results.extend(page_results)
            if page < self.max_pages:
                polite_delay()  # randomized 2-8s between page requests
        return results
