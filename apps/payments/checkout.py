"""Turning a pending booking into a Stripe-hosted checkout page."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from django.conf import settings

from apps.bookings.models import Booking

from . import gateway


def to_minor_units(amount: Decimal) -> int:
    """Dollars to cents, rounded the way money is."""
    return int((amount * 100).to_integral_value())


def build_line_items(booking: Booking) -> list[dict[str, Any]]:
    """One line per price option, quantity being how many chose it.

    Amounts come from the price options attached to the departure, which is
    the only place a price is ever read from.
    """
    travellers = booking.travellers.select_related("price_option")
    counts = Counter(traveller.price_option_id for traveller in travellers)
    options = {traveller.price_option_id: traveller.price_option for traveller in travellers}

    items = []
    for option_id, quantity in counts.items():
        option = options[option_id]
        items.append(
            {
                "quantity": quantity,
                "price_data": {
                    "currency": settings.STRIPE_CURRENCY,
                    "unit_amount": to_minor_units(option.amount),
                    "product_data": {
                        "name": f"{booking.departure.trip.title} — {option.label}",
                        "description": booking.departure.start_date.strftime("Departs %d %B %Y"),
                    },
                },
            }
        )
    return items


def start_checkout(booking: Booking, *, success_url: str, cancel_url: str) -> str:
    """Creates the checkout session and returns the URL to send the guest to."""
    session = gateway.create_checkout_session(
        line_items=build_line_items(booking),
        customer_email=booking.guest.email,
        client_reference_id=booking.reference,
        metadata={"booking_reference": booking.reference},
        success_url=success_url,
        cancel_url=cancel_url,
        # The booking reference makes a double-submit return the same session
        # instead of opening a second one.
        idempotency_key=f"checkout-{booking.reference}",
    )
    return str(session.url)
