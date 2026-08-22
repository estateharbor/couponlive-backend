"""BaseValidator interface (Phase 4 fills in per-merchant validators).

Contract: `validate(code, merchant) -> ValidationResult`.

DESIGN NOTE (read before implementing concrete validators): the mechanism by
which a code is checked is deliberately left abstract here. Full headless
checkout-flow automation against major retailers is the highest-risk option
(ToS, bot-detection, legal exposure) and should not be the default without an
explicit decision. Lighter-weight signals — merchant coupon-validation
endpoints where they exist, affiliate-network offer status — are preferred
where available and can satisfy this same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from models.schemas import ValidationResult


class BaseValidator(ABC):
    """Every merchant validator implements this, regardless of mechanism."""

    #: Normalized merchant name this validator handles.
    merchant_normalized_name: str = ""

    @abstractmethod
    def validate(self, code: str, merchant: str) -> ValidationResult:
        """Return VALID / INVALID / UNVERIFIABLE with details.

        Must never raise for an ordinary "couldn't tell" outcome — return
        UNVERIFIABLE with an error_message instead, so one flaky merchant
        doesn't fail the whole validation batch.
        """
        raise NotImplementedError
