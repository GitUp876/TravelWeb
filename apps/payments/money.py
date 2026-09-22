"""Converting between the dollars we store and the minor units Stripe counts in.

One place for this so that checkout, the webhook and the instalment scheduler
all round money the same way and never disagree by a cent.
"""

from __future__ import annotations

from decimal import Decimal

CENTS = Decimal("0.01")


def to_minor_units(amount: Decimal) -> int:
    """Dollars to cents, rounded the way money is."""
    return int((amount * 100).to_integral_value())


def from_minor_units(minor_units: int | None) -> Decimal:
    """Stripe counts in cents; we store dollars."""
    return (Decimal(minor_units or 0) / Decimal(100)).quantize(CENTS)
