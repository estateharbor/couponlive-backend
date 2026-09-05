"""Generic Shopify checkout validator (guest, no login).

Shopify exposes a *uniform* guest checkout across every store it powers, so one
validator covers every Shopify merchant in the feed. The flow:

  1. GET /products.json          -> pick an in-stock variant (no HTML scraping).
  2. GET /cart/<variant>:1       -> Shopify seeds the cart and redirects into the
                                    hosted checkout (no login required).
  3. Fill the standard discount field (input[name="reductions"]) and Apply.
  4. Read the outcome:
       INVALID      the field's inline error ("Enter a valid discount code or
                    gift card", "isn't valid", "expired", "not applicable").
       VALID        an applied-discount tag appears / the total drops, no error.
       UNVERIFIABLE checkout unreachable or no clear signal -> fails SAFE, so we
                    never award a false "Verified".

Confirmed live against fuaark.com (2026-09): an invalid code surfaces its error
through the discount input's aria-describedby element, and the order total is
unchanged.

SAFETY / OPS NOTES (see also validators/browser.py and docs/validation.md):
- Never completes a purchase; only applies a code in the order summary and reads
  the result. The payment / captcha steps are never reached.
- Automated checkout touches merchant ToS and can trip bot-detection. Run only
  in an authorized environment at the low re-validation cadence, not tight loops.
- Playwright is imported lazily so the API and scrapers run without a browser.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.logging import get_logger
from core.proxy import get_proxy_provider
from models.enums import ValidationResultEnum
from models.schemas import ValidationResult
from validators.base import BaseValidator

log = get_logger("validator.shopify")

# Confirmed-Shopify merchants we validate, keyed by normalized_name (see
# scrapers.normalize.normalize_merchant_name) -> store base URL (no trailing /).
# Add a merchant here only after confirming its storefront is Shopify and its
# guest checkout exposes the standard discount field.
SHOPIFY_STORES: dict[str, str] = {
    # Pilot: kushals has live usable codes in the feed and a standard Shopify
    # guest checkout (confirmed 2026-09: NEW200 applies, a bad code errors).
    "kushals": "https://www.kushals.com",
    "fuaark": "https://fuaark.com",
}

# The discount input rejects a bad code with one of these (case-insensitive).
_ERROR_SIGNALS = (
    "enter a valid discount",
    "isn't valid",
    "isnt valid",
    "not valid",
    "can't be applied",
    "cant be applied",
    "cannot be applied",
    "not applicable",
    "expired",
    "no longer available",
    "minimum",  # "spend ₹X to use" style rejections still mean: not usable now
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Probe run inside the checkout page after Apply. Returns a small JSON-able dict
# describing the outcome so the Python side stays declarative and testable.
_PROBE_JS = r"""
(code) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const input = document.querySelector('input[name="reductions"]');
  // Inline error is exposed via the input's aria-describedby (confirmed live),
  // with visible [role=alert] / error nodes as a fallback.
  let error = '';
  if (input) {
    const id = input.getAttribute('aria-describedby');
    if (id) { const el = document.getElementById(id); if (el) error = norm(el.textContent); }
  }
  if (!error) {
    const el = document.querySelector('[role="alert"], [id*="error" i], [class*="Error" i]');
    if (el) error = norm(el.textContent);
  }
  // Applied-discount signal: a removable discount tag referencing the code, or a
  // reduction line in the order summary. Present only after a code is accepted.
  const up = (code || '').toUpperCase();
  const summary = document.querySelector('[class*="summary" i], aside, [role="complementary"]') || document.body;
  const summaryText = norm(summary.innerText).toUpperCase();
  const removeCtrl = !!document.querySelector(
    'button[aria-label^="Remove" i], [aria-label*="remove discount" i], [class*="reduction" i] button'
  );
  const codeTag = up.length >= 3 && summaryText.includes(up);
  return { error, hasError: !!error, removeCtrl, codeTag };
}
"""


class ShopifyCheckoutValidator(BaseValidator):
    """Drives the Shopify guest checkout to apply a code and read the result."""

    def __init__(self, merchant_normalized_name: str, base_url: str, *, headless: bool = True):
        self.merchant_normalized_name = merchant_normalized_name
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.nav_timeout_ms = 30000

    # -- result helper (mirrors validators.browser.BrowserValidator) ----------
    def _result(self, res: ValidationResultEnum, msg: str, snapshot: str = "") -> ValidationResult:
        return ValidationResult(
            result=res,
            error_message=None if res is ValidationResultEnum.valid else msg,
            response_snapshot=(snapshot or None) and snapshot[:500],
            checked_at=_now(),
        )

    @staticmethod
    def _classify(probe: dict) -> tuple[ValidationResultEnum, str]:
        """Map the in-page probe to a validation outcome. Order matters: an
        explicit rejection is checked FIRST so a bad code can never read as
        valid, and anything ambiguous fails safe to UNVERIFIABLE."""
        err = (probe.get("error") or "").lower()
        if probe.get("hasError") and any(sig in err for sig in _ERROR_SIGNALS):
            return ValidationResultEnum.invalid, f"discount rejected: {probe.get('error')!r}"
        # A discount tag / remove control is the positive signal Shopify only
        # renders once a code is accepted.
        if not probe.get("hasError") and (probe.get("removeCtrl") or probe.get("codeTag")):
            return ValidationResultEnum.valid, "discount applied"
        return (
            ValidationResultEnum.unverifiable,
            "no clear apply/reject signal (checkout may need selector tuning)",
        )

    # -- main -----------------------------------------------------------------
    def validate(self, code: str, merchant: str) -> ValidationResult:
        lease = get_proxy_provider().acquire()  # no-op by default; plug point
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # playwright not installed
            return self._result(ValidationResultEnum.unverifiable, f"playwright unavailable: {exc}")

        log.info("validate.start", merchant=self.merchant_normalized_name, code=code, proxy=lease.label)
        try:
            with sync_playwright() as p:
                launch_kwargs = {"headless": self.headless}
                if lease.proxies:  # real provider wired later
                    launch_kwargs["proxy"] = {"server": next(iter(lease.proxies.values()))}
                browser = p.chromium.launch(**launch_kwargs)
                page = browser.new_page()
                page.set_default_timeout(self.nav_timeout_ms)
                try:
                    variant = self._pick_variant(page)
                    if variant is None:
                        return self._result(
                            ValidationResultEnum.unverifiable, "no in-stock product to seed cart"
                        )
                    self._open_checkout_with_item(page, variant)
                    if not self._apply_code(page, code):
                        return self._result(
                            ValidationResultEnum.unverifiable, "discount field not reachable"
                        )
                    probe = page.evaluate(_PROBE_JS, code)
                    res, msg = self._classify(probe)
                    snap = (probe.get("error") or "")[:500]
                    outcome = self._result(res, msg, snap)
                finally:
                    browser.close()
            get_proxy_provider().report_result(lease, success=True)
            log.info("validate.done", merchant=self.merchant_normalized_name, code=code,
                     result=outcome.result.value)
            return outcome
        except Exception as exc:  # never raise for an ordinary failure
            get_proxy_provider().report_result(lease, success=False)
            log.warning("validate.error", merchant=self.merchant_normalized_name, code=code, error=str(exc))
            return self._result(ValidationResultEnum.unverifiable, f"flow error: {exc}")

    # -- flow steps -----------------------------------------------------------
    def _pick_variant(self, page) -> int | None:
        """First available variant id from the public /products.json feed."""
        try:
            resp = page.request.get(f"{self.base_url}/products.json?limit=50", timeout=self.nav_timeout_ms)
            if not resp.ok:
                return None
            products = (resp.json() or {}).get("products", [])
        except Exception:
            return None
        for prod in products:
            for v in prod.get("variants", []):
                if v.get("available") and v.get("id"):
                    return int(v["id"])
        # Fall back to any variant if none advertises availability.
        for prod in products:
            for v in prod.get("variants", []):
                if v.get("id"):
                    return int(v["id"])
        return None

    def _open_checkout_with_item(self, page, variant: int) -> None:
        """Seed the cart via permalink; Shopify redirects into guest checkout.
        If it lands on the cart instead, push on to /checkout explicitly."""
        page.goto(f"{self.base_url}/cart/{variant}:1", wait_until="domcontentloaded")
        if "/checkout" not in page.url:
            page.goto(f"{self.base_url}/checkout", wait_until="domcontentloaded")

    def _apply_code(self, page, code: str) -> bool:
        """Fill the discount field and click Apply. Returns False if the field
        never appears (bot wall / unexpected redirect)."""
        from playwright.sync_api import TimeoutError as PWTimeout

        field = page.locator('input[name="reductions"]').first
        try:
            field.wait_for(state="visible", timeout=12000)
        except PWTimeout:
            return False
        field.fill(code)
        # Apply button sits next to the field; try a few shapes.
        for sel in ('button:has-text("Apply")', 'button[type="submit"]:near(input[name="reductions"])'):
            btn = page.locator(sel).first
            try:
                if btn.is_visible():
                    btn.click()
                    break
            except Exception:
                continue
        else:
            field.press("Enter")
        page.wait_for_timeout(3000)  # let the summary re-price / error render
        return True
