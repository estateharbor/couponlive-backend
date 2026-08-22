"""AJIO checkout-flow validator config.

RISK: MODERATE. AJIO (Reliance) gates the coupon field on the authenticated
bag/checkout page. Selectors are starting points; tune against a live session.
"""
from validators.browser import MerchantConfig

CONFIG = MerchantConfig(
    merchant_normalized_name="ajio",
    product_url="https://www.ajio.com/",             # replace w/ a representative in-stock product
    add_to_cart_selectors=[
        "div.pdp-addtocart-button",
        "button:has-text('ADD TO BAG')",
    ],
    cart_url="https://www.ajio.com/cart",
    coupon_input_selectors=[
        "input#promoCode",
        "input[placeholder*='coupon' i]",
        "input[placeholder*='promo' i]",
    ],
    apply_button_selectors=[
        "button:has-text('APPLY')",
        "button.button-apply",
    ],
    success_texts=["coupon applied", "you saved", "discount applied"],
    error_texts=["invalid coupon", "not applicable", "expired", "unable to apply"],
    requires_login=True,
    risk_note="Coupon field on authenticated bag page; tune selectors live.",
)
