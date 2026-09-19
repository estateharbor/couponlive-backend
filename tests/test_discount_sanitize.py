"""Discount sanitization — labels must never contradict the description.

Covers the concrete misclassifications the audit found."""
from __future__ import annotations

import pytest

from models.enums import DiscountType as D
from scrapers.normalize import sanitize_discount


@pytest.mark.parametrize("dtype,dval,text,exp_type,exp_val", [
    # "Flat 100% Off" but really ₹500 off -> the rupee amount wins (100% implausible).
    (D.percentage, 100, "Flat 100% Off — get ₹500 off orders", D.fixed, 500),
    # Feed said 13% but the text says 12.5% -> the text value wins.
    (D.percentage, 13, "Get 12.5% off sitewide", D.percentage, 12.5),
    # Mislabeled "Free Shipping" on a rupee discount -> fixed.
    (D.free_shipping, None, "Flat ₹300 off on first order", D.fixed, 300),
    # Plausible percentage in text, feed unknown -> percentage.
    (D.unknown, None, "Get Up To 20% Discount", D.percentage, 20),
    # Implausible feed percentage with no text signal -> unknown (not a fake %).
    (D.percentage, 8377, "Sign-Up Offer", D.unknown, None),
    (D.percentage, 100, "Special member deal", D.unknown, None),
    # Real free shipping.
    (D.free_shipping, None, "Free shipping on all orders", D.free_shipping, None),
    # Cashback keyword.
    (D.percentage, 5, "Flat 5% cashback via wallet", D.cashback, 5),
    # Clean rupee.
    (D.fixed, 200, "Flat ₹200 Off", D.fixed, 200),
    # ₹ with comma.
    (D.unknown, None, "Save ₹1,000 on electronics", D.fixed, 1000),
])
def test_sanitize(dtype, dval, text, exp_type, exp_val):
    t, v = sanitize_discount(dtype, dval, text)
    assert t is exp_type
    assert v == exp_val


def test_no_text_keeps_plausible_feed_value():
    # No signal in text, feed value plausible -> keep it.
    assert sanitize_discount(D.percentage, 30, "") == (D.percentage, 30)
    assert sanitize_discount(D.fixed, 250, "") == (D.fixed, 250)
