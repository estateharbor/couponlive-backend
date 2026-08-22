"""Normalization + dedup layer.

Turns a raw batch of `RawCoupon` (from any scraper/ingestor) into a deduped
set of canonical offers with merged source provenance. Pure functions — no
DB, no network — so it is fast to unit-test and shared by every source.

Identity rule:
- If a code is present  -> identity = (normalized_merchant, normalized_code)
- If no code (reveal-gated offer) -> identity = (normalized_merchant, external_ref)

Merchant names are fuzzy-collapsed so "Myntra", "myntra.com", and "Myntra
India" converge to one canonical key.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from rapidfuzz import fuzz

from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon

# Suffix/noise tokens stripped from merchant names before matching.
_MERCHANT_NOISE = re.compile(
    r"\b(india|official|store|stores|coupons?|offers?|online|shop|www|com|in)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_merchant_name(name: str) -> str:
    """Canonical merchant key: ascii-fold, lowercase, drop noise, collapse."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower()
    name = _MERCHANT_NOISE.sub(" ", name)
    name = _NON_ALNUM.sub(" ", name).strip()
    return re.sub(r"\s+", "", name)  # collapse to a compact token


def normalize_code(code: str | None) -> str | None:
    """Uppercase + strip. Returns None for empty/placeholder codes."""
    if not code:
        return None
    c = code.strip().upper()
    c = re.sub(r"\s+", "", c)
    if not c or c in {"NA", "N/A", "NONE", "GETCOUPON", "GETDEAL"}:
        return None
    return c


def match_canonical_merchant(
    raw_name: str, known: dict[str, str], threshold: int = 90
) -> str:
    """Return the canonical merchant name for `raw_name`.

    `known` maps normalized_key -> display_name for merchants we've seen this
    batch. Uses fuzzy ratio on the normalized key so near-duplicate spellings
    converge; otherwise registers the raw name as a new canonical.
    """
    key = normalize_merchant_name(raw_name)
    if not key:
        return raw_name.strip()
    if key in known:
        return known[key]
    best_key, best_score = None, 0
    for existing_key in known:
        score = fuzz.ratio(key, existing_key)
        if score > best_score:
            best_key, best_score = existing_key, score
    if best_key is not None and best_score >= threshold:
        return known[best_key]
    known[key] = raw_name.strip()
    return raw_name.strip()


@dataclass
class NormalizedCoupon:
    """Deduped, canonical offer ready to upsert. Provenance merged across sources."""

    merchant_name: str
    normalized_merchant: str
    code: str | None
    external_ref: str | None
    requires_reveal: bool
    description: str | None
    discount_type: DiscountType
    discount_value: float | None
    first_seen: datetime
    last_seen: datetime
    # provenance: list of (source_url, seen_at, ingestion_method)
    sources: list[tuple[str | None, datetime, IngestionMethod]] = field(default_factory=list)

    @property
    def identity(self) -> tuple[str, str]:
        """Dedup key. Code when present, else external_ref."""
        if self.code:
            return (self.normalized_merchant, f"code:{self.code}")
        return (self.normalized_merchant, f"ref:{self.external_ref}")


def normalize_and_dedupe(raw: list[RawCoupon]) -> list[NormalizedCoupon]:
    """Collapse a raw batch into canonical, deduped offers.

    - fuzzy-canonicalize merchant names,
    - normalize codes,
    - dedupe on identity, keeping earliest first_seen / latest last_seen,
    - merge source provenance.
    """
    known_merchants: dict[str, str] = {}
    merged: dict[tuple[str, str], NormalizedCoupon] = {}

    for rc in raw:
        canonical = match_canonical_merchant(rc.merchant_name, known_merchants)
        nmerchant = normalize_merchant_name(canonical)
        code = normalize_code(rc.code)
        # A code-less record MUST have an external_ref to have a stable identity;
        # skip anything we can't identify (prevents unbounded NULL/NULL dupes).
        if code is None and not rc.external_ref:
            continue

        nc = NormalizedCoupon(
            merchant_name=canonical,
            normalized_merchant=nmerchant,
            code=code,
            external_ref=rc.external_ref,
            requires_reveal=rc.requires_reveal or (code is None),
            description=rc.description,
            discount_type=rc.discount_type or DiscountType.unknown,
            discount_value=rc.discount_value,
            first_seen=rc.scraped_at,
            last_seen=rc.scraped_at,
            sources=[(rc.source_url, rc.scraped_at, rc.ingestion_method)],
        )

        key = nc.identity
        if key not in merged:
            merged[key] = nc
        else:
            existing = merged[key]
            existing.first_seen = min(existing.first_seen, nc.first_seen)
            existing.last_seen = max(existing.last_seen, nc.last_seen)
            # Prefer a non-empty description / discount if the existing lacks one.
            if not existing.description and nc.description:
                existing.description = nc.description
            if existing.discount_type is DiscountType.unknown:
                existing.discount_type = nc.discount_type
            if existing.discount_value is None:
                existing.discount_value = nc.discount_value
            existing.sources.extend(nc.sources)

    return list(merged.values())
