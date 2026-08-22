"""Myntra checkout-flow validator config.

RISK: Myntra (Flipkart group) uses bot detection; the coupon field lives on the
authenticated bag/checkout page, so validation typically REQUIRES a logged-in
session. Selectors below are starting points and MUST be tuned against a live
authorized session. Do not run at high frequency.
"""
from validators.browser import MerchantConfig

CONFIG = MerchantConfig(
    merchant_normalized_name="myntra",
    product_url="https://www.myntra.com/",           # replace w/ a representative in-stock PDP
    add_to_cart_selectors=[
        "div.pdp-add-to-bag",
        "button:has-text('ADD TO BAG')",
    ],
    cart_url="https://www.myntra.com/checkout/cart",
    coupon_input_selectors=[
        "input#coupon",
        "input[placeholder*='coupon' i]",
        "input[name*='coupon' i]",
    ],
    apply_button_selectors=[
        "a.applyBtn",
        "button:has-text('APPLY')",
    ],
    success_texts=["coupon applied", "you saved", "discount applied"],
    error_texts=["invalid coupon", "not applicable", "coupon expired", "unable to apply"],
    requires_login=True,
    risk_note="Requires login; Flipkart-group bot detection. Low frequency only.",
)
