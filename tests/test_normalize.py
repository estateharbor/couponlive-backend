"""Unit tests for the normalization + dedup layer (pure, no network/DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.normalize import (
    normalize_and_dedupe,
    normalize_code,
    normalize_merchant_name,
)


def _rc(**kw) -> RawCoupon:
    base = dict(
        merchant_name="Myntra",
        scraped_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        ingestion_method=IngestionMethod.scrape_requests,
    )
    base.update(kw)
    return RawCoupon(**base)


def test_normalize_merchant_name_collapses_variants():
    keys = {
        normalize_merchant_name("Myntra"),
        normalize_merchant_name("myntra.com"),
        normalize_merchant_name("Myntra India"),
        normalize_merchant_name("  MYNTRA  Store "),
    }
    assert keys == {"myntra"}


def test_normalize_code():
    assert normalize_code("  save20 ") == "SAVE20"
    assert normalize_code("Get Coupon") is None  # placeholder label
    assert normalize_code("") is None
    assert normalize_code(None) is None


def test_dedupe_on_code_keeps_span_and_merges_sources():
    early = datetime(2026, 8, 20, tzinfo=timezone.utc)
    late = early + timedelta(days=2)
    raw = [
        _rc(code="SAVE20", scraped_at=early, source_url="https://a"),
        _rc(code="save20", scraped_at=late, source_url="https://b"),  # same code, later
    ]
    out = normalize_and_dedupe(raw)
    assert len(out) == 1
    nc = out[0]
    assert nc.code == "SAVE20"
    assert nc.first_seen == early and nc.last_seen == late
    assert len(nc.sources) == 2  # provenance merged


def test_codeless_offers_dedupe_on_external_ref():
    raw = [
        _rc(code=None, external_ref="551335", requires_reveal=True, source_url="https://a"),
        _rc(code=None, external_ref="551335", requires_reveal=True, source_url="https://b"),
        _rc(code=None, external_ref="999999", requires_reveal=True, source_url="https://c"),
    ]
    out = normalize_and_dedupe(raw)
    assert len(out) == 2
    assert all(nc.requires_reveal for nc in out)


def test_codeless_without_external_ref_is_dropped():
    # Nothing to identify it by -> must not create an unbounded NULL/NULL dupe.
    raw = [_rc(code=None, external_ref=None)]
    assert normalize_and_dedupe(raw) == []


def test_backfills_missing_fields_across_duplicates():
    raw = [
        _rc(code="X1", description=None, discount_type=DiscountType.unknown, discount_value=None),
        _rc(code="X1", description="20% off everything",
            discount_type=DiscountType.percentage, discount_value=20),
    ]
    out = normalize_and_dedupe(raw)
    assert len(out) == 1
    assert out[0].description == "20% off everything"
    assert out[0].discount_type is DiscountType.percentage
    assert out[0].discount_value == 20
