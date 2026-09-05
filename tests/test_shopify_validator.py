"""Regression tests for the Shopify checkout validator's outcome classifier.

Pure/offline: they exercise `_classify` (the in-page probe -> VALID/INVALID/
UNVERIFIABLE mapping) with canned probe dicts, so no browser is needed. The
live browser mechanics are covered opt-in by the `browser`-marked suite.

The load-bearing property under test: a rejected code can NEVER read as valid,
and anything ambiguous fails safe to UNVERIFIABLE (so it is never awarded the
"Verified" badge).
"""
from __future__ import annotations

import pytest

from models.enums import ValidationResultEnum
from validators.merchants.shopify import ShopifyCheckoutValidator, SHOPIFY_STORES


def _classify(probe):
    return ShopifyCheckoutValidator._classify(probe)[0]


@pytest.mark.parametrize(
    "error",
    [
        "Enter a valid discount code or gift card",   # confirmed live on fuaark.com
        "This code isn't valid",
        "Discount code has expired",
        "Not applicable to items in your cart",
        "Spend ₹999 more to use this code (minimum not met)",
    ],
)
def test_rejected_code_is_invalid(error):
    probe = {"error": error, "hasError": True, "removeCtrl": False, "codeTag": False}
    assert _classify(probe) is ValidationResultEnum.invalid


def test_applied_discount_is_valid():
    # A remove-discount control appears only once a code is accepted.
    assert _classify({"error": "", "hasError": False, "removeCtrl": True, "codeTag": True}) \
        is ValidationResultEnum.valid


def test_no_signal_is_unverifiable():
    # Neither an error nor an applied-discount tag -> we genuinely can't tell.
    assert _classify({"error": "", "hasError": False, "removeCtrl": False, "codeTag": False}) \
        is ValidationResultEnum.unverifiable


def test_error_wins_over_stale_tag():
    # Safety: even if a code-shaped string lingers in the summary, an explicit
    # rejection must still classify INVALID — never valid.
    probe = {"error": "Enter a valid discount code", "hasError": True,
             "removeCtrl": True, "codeTag": True}
    assert _classify(probe) is ValidationResultEnum.invalid


def test_unrecognized_error_text_fails_safe():
    # An error we don't recognize is NOT treated as a valid application.
    probe = {"error": "Something went wrong, please retry", "hasError": True,
             "removeCtrl": False, "codeTag": False}
    assert _classify(probe) is ValidationResultEnum.unverifiable


def test_fuaark_registered_as_pilot():
    assert SHOPIFY_STORES.get("fuaark", "").startswith("https://")
