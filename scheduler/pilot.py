"""One-shot pilot runner for Shopify checkout validation.

Validates the codes of every registered Shopify store (see
`validators.merchants.shopify.SHOPIFY_STORES`) synchronously and prints each
outcome, so you can see real VALID/INVALID/UNVERIFIED results without waiting for
the beat schedule. Scoped to the Shopify pilot so it never touches the untuned
marketplace configs.

Run inside the worker container (which has Chromium):

    docker compose exec worker python -m scheduler.pilot
"""
from __future__ import annotations

from sqlalchemy import select

from models.base import get_sessionmaker
from models.models import Coupon, Merchant
from scheduler.validation import record_validation_result
from validators.merchants.shopify import SHOPIFY_STORES
from validators.registry import get_validator


def main() -> None:
    session = get_sessionmaker()()
    try:
        rows = session.scalars(
            select(Coupon)
            .join(Merchant)
            .where(
                Merchant.normalized_name.in_(list(SHOPIFY_STORES)),
                Coupon.code.is_not(None),
            )
        ).all()
        print(f"codes to validate: {len(rows)}")
        counts: dict[str, int] = {}
        for c in rows:
            validator = get_validator(c.merchant.normalized_name)
            if validator is None:
                continue
            result = validator.validate(c.code, c.merchant.name)
            record_validation_result(session, c, result)
            counts[result.result.value] = counts.get(result.result.value, 0) + 1
            print(f"  {c.merchant.name} {c.code} -> {result.result.value}")
        session.commit()
        print("summary:", counts or "(nothing to validate)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
