"""Playwright checkout-flow validator harness.

Drives a headless browser through: open product -> add to cart -> open cart ->
find coupon field -> submit code -> read whether a discount applied or an error
showed -> VALID / INVALID / UNVERIFIABLE.

Per-merchant differences are expressed as a `MerchantConfig` (selectors, product
URL, result signals), so each merchant module is a small config, not a rewrite.

SAFETY / OPS NOTES:
- This never completes a purchase; it only applies a code and reads the result.
- Automated checkout actions trip bot detection and touch merchant ToS. Run
  only in an authorized environment, at low volume, with the re-validation
  cadence (not tight loops). Per-merchant risk notes live in each module and in
  docs/validation.md.
- The proxy/session-rotation *hook* (core.proxy) is wired as a plug point. It is
  a no-op by default; wiring a real provider is an explicit, separate decision.
- Playwright is imported lazily so the rest of the app (API, scrapers) runs
  without a browser installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.logging import get_logger
from core.proxy import get_proxy_provider
from models.enums import ValidationResultEnum
from models.schemas import ValidationResult
from validators.base import BaseValidator

log = get_logger("validator.browser")


@dataclass
class MerchantConfig:
    """Everything the harness needs to validate one merchant."""

    merchant_normalized_name: str
    product_url: str                       # a representative in-stock product
    coupon_input_selectors: list[str]      # tried in order (first match wins)
    apply_button_selectors: list[str]
    add_to_cart_selectors: list[str] = field(default_factory=list)
    go_to_cart_selectors: list[str] = field(default_factory=list)
    cart_url: str | None = None            # direct cart URL, if simpler than clicking
    success_selectors: list[str] = field(default_factory=list)
    success_texts: list[str] = field(default_factory=list)   # case-insensitive substrings
    error_selectors: list[str] = field(default_factory=list)
    error_texts: list[str] = field(default_factory=list)
    requires_login: bool = False           # many carts need auth to reach coupon field
    risk_note: str = ""
    nav_timeout_ms: int = 30000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BrowserValidator(BaseValidator):
    """Generic Playwright validator; merchant modules supply the config."""

    def __init__(self, config: MerchantConfig, *, headless: bool = True):
        self.config = config
        self.merchant_normalized_name = config.merchant_normalized_name
        self.headless = headless

    # -- small helpers -----------------------------------------------------
    @staticmethod
    def _first_visible(page, selectors: list[str], timeout_ms: int = 8000):
        """Return the first selector that becomes visible, else None."""
        from playwright.sync_api import TimeoutError as PWTimeout

        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                return loc
            except PWTimeout:
                continue
        return None

    def _detect_outcome(self, page, code: str) -> ValidationResult:
        body_text = (page.inner_text("body") or "").lower()

        # Explicit error first (a rejected code is a definitive INVALID).
        if self._first_visible(page, self.config.error_selectors, 3000) is not None:
            return self._result(ValidationResultEnum.invalid, "error selector matched", body_text)
        for t in self.config.error_texts:
            if t.lower() in body_text:
                return self._result(ValidationResultEnum.invalid, f"error text: {t!r}", body_text)

        # Then success signals.
        if self._first_visible(page, self.config.success_selectors, 3000) is not None:
            return self._result(ValidationResultEnum.valid, "success selector matched", body_text)
        for t in self.config.success_texts:
            if t.lower() in body_text:
                return self._result(ValidationResultEnum.valid, f"success text: {t!r}", body_text)

        # Neither -> we genuinely couldn't tell.
        return self._result(
            ValidationResultEnum.unverifiable,
            "no success/error signal detected (selectors may need tuning)",
            body_text,
        )

    def _result(self, res: ValidationResultEnum, msg: str, body_text: str) -> ValidationResult:
        snap = body_text[:500] if body_text else None
        return ValidationResult(
            result=res,
            error_message=None if res is ValidationResultEnum.valid else msg,
            response_snapshot=snap,
            checked_at=_now(),
        )

    # -- main --------------------------------------------------------------
    def validate(self, code: str, merchant: str) -> ValidationResult:
        cfg = self.config
        lease = get_proxy_provider().acquire()  # no-op by default; plug point
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # playwright not installed
            return self._result(ValidationResultEnum.unverifiable, f"playwright unavailable: {exc}", "")

        log.info("validate.start", merchant=cfg.merchant_normalized_name, code=code,
                 proxy=lease.label)
        try:
            with sync_playwright() as p:
                launch_kwargs = {"headless": self.headless}
                if lease.proxies:  # real provider wired later
                    launch_kwargs["proxy"] = {"server": next(iter(lease.proxies.values()))}
                browser = p.chromium.launch(**launch_kwargs)
                page = browser.new_page()
                page.set_default_timeout(cfg.nav_timeout_ms)
                try:
                    self._run_flow(page, code)
                    outcome = self._detect_outcome(page, code)
                finally:
                    browser.close()
            get_proxy_provider().report_result(lease, success=True)
            log.info("validate.done", merchant=cfg.merchant_normalized_name,
                     code=code, result=outcome.result.value)
            return outcome
        except Exception as exc:  # never raise for an ordinary failure
            get_proxy_provider().report_result(lease, success=False)
            log.warning("validate.error", merchant=cfg.merchant_normalized_name,
                        code=code, error=str(exc))
            return self._result(ValidationResultEnum.unverifiable, f"flow error: {exc}", "")

    def _run_flow(self, page, code: str) -> None:
        cfg = self.config
        page.goto(cfg.product_url, wait_until="domcontentloaded")

        atc = self._first_visible(page, cfg.add_to_cart_selectors)
        if atc is not None:
            atc.click()

        if cfg.cart_url:
            page.goto(cfg.cart_url, wait_until="domcontentloaded")
        else:
            go = self._first_visible(page, cfg.go_to_cart_selectors)
            if go is not None:
                go.click()

        field = self._first_visible(page, cfg.coupon_input_selectors)
        if field is None:
            raise RuntimeError("coupon input field not found")
        field.fill(code)

        apply_btn = self._first_visible(page, cfg.apply_button_selectors)
        if apply_btn is None:
            raise RuntimeError("apply button not found")
        apply_btn.click()
        page.wait_for_timeout(2500)  # let the cart re-price / error render
