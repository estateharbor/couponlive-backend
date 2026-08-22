"""Flipkart checkout-flow validator config.

RISK: Flipkart uses bot detection and usually gates the coupon/offer field
behind login + a non-empty cart. Codes are often auto-applied "offers" rather
than a manual coupon box, so a missing input field may mean "no manual coupon
field", not a broken selector — the harness returns UNVERIFIABLE in that case.
Tune against a live authorized session.
"""
from validators.browser import MerchantConfig

CONFIG = MerchantConfig(
    merchant_normalized_name="flipkart",
    product_url="https://www.flipkart.com/",         # replace w/ a representative in-stock product
    add_to_cart_selectors=[
        "button:has-text('Add to cart')",
        "button:has-text('ADD TO CART')",
    ],
    cart_url="https://www.flipkart.com/viewcart",
    coupon_input_selectors=[
        "input[placeholder*='coupon' i]",
        "input[name*='coupon' i]",
    ],
    apply_button_selectors=[
        "button:has-text('Apply')",
        "span:has-text('Apply')",
    ],
    success_texts=["applied", "you save", "discount"],
    error_texts=["invalid", "not applicable", "expired", "could not"],
    requires_login=True,
    risk_note="Login + non-empty cart usually required; many offers auto-apply.",
)
