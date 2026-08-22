"""Browser-harness proof: run the REAL Playwright validator against a local
controlled checkout fixture (no live merchant). Verifies the add-code -> apply
-> parse-result mechanics actually work.

Marked `browser` so it's opt-in and skips cleanly where Chromium isn't present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from models.enums import ValidationResultEnum
from validators.browser import BrowserValidator, MerchantConfig

pytestmark = pytest.mark.browser

FIXTURE = (Path(__file__).parent / "fixtures" / "checkout.html").resolve().as_uri()


def _config() -> MerchantConfig:
    return MerchantConfig(
        merchant_normalized_name="fixture",
        product_url=FIXTURE,        # stay on the fixture page (acts as the cart)
        add_to_cart_selectors=[],
        coupon_input_selectors=["input#coupon"],
        apply_button_selectors=["button#apply"],
        success_texts=["coupon applied", "you saved"],
        error_texts=["invalid coupon"],
    )


@pytest.fixture(scope="module")
def validator():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        pytest.skip("playwright not installed")
    return BrowserValidator(_config(), headless=True)


def test_valid_code_detected(validator):
    res = validator.validate("SAVE20", "Fixture")
    if res.result is ValidationResultEnum.unverifiable and res.error_message and "unavailable" in res.error_message:
        pytest.skip("chromium not available")
    assert res.result is ValidationResultEnum.valid


def test_invalid_code_detected(validator):
    res = validator.validate("BADCODE", "Fixture")
    assert res.result is ValidationResultEnum.invalid
    assert res.response_snapshot and "invalid coupon" in res.response_snapshot.lower()
