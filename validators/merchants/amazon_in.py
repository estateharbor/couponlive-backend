"""Amazon India checkout-flow validator config.

RISK: HIGH. Amazon has aggressive anti-automation and explicit ToS against
scraping/automated access; the promo-code field is on the authenticated
checkout page and often requires an address + eligible cart. This config exists
for completeness but Amazon is the LEAST advisable target to automate — prefer
promo verification via authorized programs/APIs. Tune only in an authorized
environment and expect frequent bot challenges.
"""
from validators.browser import MerchantConfig

CONFIG = MerchantConfig(
    merchant_normalized_name="amazon",
    product_url="https://www.amazon.in/",            # replace w/ a representative in-stock ASIN
    add_to_cart_selectors=[
        "input#add-to-cart-button",
        "input[name='submit.add-to-cart']",
    ],
    cart_url="https://www.amazon.in/gp/cart/view.html",
    go_to_cart_selectors=["a#nav-cart", "input[name='proceedToRetailCheckout']"],
    coupon_input_selectors=[
        "input#spc-gcpromoinput",
        "input[placeholder*='promo' i]",
        "input[name*='promo' i]",
    ],
    apply_button_selectors=[
        "span#gcApplyButtonId input",
        "button:has-text('Apply')",
    ],
    success_texts=["promotion applied", "you saved", "discount applied"],
    error_texts=["is not valid", "cannot be applied", "expired", "not eligible"],
    requires_login=True,
    risk_note="HIGH RISK: aggressive anti-automation + strict ToS. Least advisable target.",
)
