"""Nykaa checkout-flow validator config.

RISK: MODERATE. Nykaa exposes a coupon field on the cart page; login may be
required to fully apply. Selectors are starting points; tune against a live
session.
"""
from validators.browser import MerchantConfig

CONFIG = MerchantConfig(
    merchant_normalized_name="nykaa",
    product_url="https://www.nykaa.com/",            # replace w/ a representative in-stock product
    add_to_cart_selectors=[
        "button:has-text('Add to Bag')",
        "button.css-1p5m6zn",
    ],
    cart_url="https://www.nykaa.com/checkout/cart",
    coupon_input_selectors=[
        "input[placeholder*='coupon' i]",
        "input[placeholder*='promo' i]",
        "input[name*='coupon' i]",
    ],
    apply_button_selectors=[
        "button:has-text('Apply')",
        "span:has-text('APPLY')",
    ],
    success_texts=["coupon applied", "you saved", "discount applied", "savings"],
    error_texts=["invalid", "not applicable", "expired", "unable to apply"],
    requires_login=True,
    risk_note="Coupon field on cart; login may be required to apply.",
)
