"""Merchant validator registry: normalized_name -> validator factory."""
from __future__ import annotations

from collections.abc import Callable

from validators.base import BaseValidator
from validators.browser import BrowserValidator
from validators.merchants import ajio, amazon_in, flipkart, myntra, nykaa

_CONFIGS = [myntra.CONFIG, amazon_in.CONFIG, flipkart.CONFIG, ajio.CONFIG, nykaa.CONFIG]

VALIDATOR_REGISTRY: dict[str, Callable[[], BaseValidator]] = {
    cfg.merchant_normalized_name: (lambda c=cfg: BrowserValidator(c)) for cfg in _CONFIGS
}


def get_validator(merchant_normalized_name: str) -> BaseValidator | None:
    """Return a validator for the merchant, or None if none is registered."""
    factory = VALIDATOR_REGISTRY.get(merchant_normalized_name)
    return factory() if factory else None


def supported_merchants() -> list[str]:
    return sorted(VALIDATOR_REGISTRY)
