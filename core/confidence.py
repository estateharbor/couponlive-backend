"""Confidence scoring (pure functions, no DB/IO).

`confidence_score` (0..1) reflects how much we trust that a code currently
works, blending the latest validation outcome with crowd feedback. Recency is
deliberately NOT baked into the stored score — the API applies a freshness
filter (`last_validated_at > now - Nh`) at query time, so we don't have to
recompute every coupon's score as the clock advances.
"""
from __future__ import annotations

from models.enums import ValidationResultEnum

# Prior from the latest automated validation.
_BASE = {
    ValidationResultEnum.valid: 0.85,
    ValidationResultEnum.unverifiable: 0.40,
    ValidationResultEnum.invalid: 0.05,
}


def compute_confidence(
    latest_result: ValidationResultEnum | None,
    feedback_positive: int = 0,
    feedback_total: int = 0,
) -> float:
    """Blend validation prior with crowd feedback.

    Feedback weight grows with volume but is capped at 0.5, so a single
    thumbs-down can't tank a freshly-validated code, but sustained feedback
    can override a stale prior.
    """
    base = 0.3 if latest_result is None else _BASE[latest_result]
    if feedback_total > 0:
        ratio = feedback_positive / feedback_total
        weight = min(feedback_total / (feedback_total + 5), 0.5)
        base = (1 - weight) * base + weight * ratio
    return round(max(0.0, min(1.0, base)), 4)
