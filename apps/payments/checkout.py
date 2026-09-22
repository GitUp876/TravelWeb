"""Turning a pending booking into a Stripe-hosted checkout page."""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.conf import settings

from apps.bookings.models import Booking

from . import gateway
from .money import to_minor_units

__all__ = [
    "NothingOwed",
    "build_line_items",
    "start_balance_checkout",
    "start_checkout",
    "start_plan_checkout",
    "to_minor_units",
]


class NothingOwed(RuntimeError):
    """This booking has no balance to collect."""


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
    """Creates a pay-in-full checkout session and returns the URL for the guest."""
    session = gateway.create_checkout_session(
        line_items=build_line_items(booking),
        customer_email=booking.guest.email,
        client_reference_id=booking.reference,
        metadata={"booking_reference": booking.reference, "kind": "full"},
        success_url=success_url,
        cancel_url=cancel_url,
        # The booking reference makes a double-submit return the same session
        # instead of opening a second one.
        idempotency_key=f"checkout-{booking.reference}",
    )
    return str(session.url)


def start_plan_checkout(booking: Booking, *, success_url: str, cancel_url: str) -> str:
    """Creates a deposit checkout that also saves the card for the instalments.

    Only the deposit is charged now. The rest of the balance is taken later, on
    the schedule already written against the booking's plan.
    """
    plan = booking.payment_plan
    session = gateway.create_deposit_checkout_session(
        amount_minor=to_minor_units(plan.deposit_amount),
        currency=settings.STRIPE_CURRENCY,
        product_name=f"{booking.departure.trip.title} — deposit",
        product_description=(
            f"Deposit today; the balance follows in {plan.instalment_count} "
            "scheduled instalment(s)."
        ),
        client_reference_id=booking.reference,
        metadata={"booking_reference": booking.reference, "kind": "plan_deposit"},
        success_url=success_url,
        cancel_url=cancel_url,
        idempotency_key=f"deposit-{booking.reference}",
        customer_email="" if booking.guest.stripe_customer_id else booking.guest.email,
        customer_id=booking.guest.stripe_customer_id,
    )
    return str(session.url)


def start_balance_checkout(booking: Booking, *, success_url: str, cancel_url: str) -> str:
    """Creates a checkout for whatever the booking still owes.

    The amount is the balance this database holds, worked out from the total the
    travellers' price options add up to. Nothing about it is read from the
    request, so a guest who edits the form pays exactly what they owe.

    The card is not saved: this is one payment, and a card we will not use again
    is one we should not ask Stripe to keep.
    """
    balance = booking.balance
    if balance <= 0:
        raise NothingOwed(f"booking {booking.reference} owes nothing")

    amount_minor = to_minor_units(balance)
    metadata = {"booking_reference": booking.reference, "kind": "balance"}
    session = gateway.create_checkout_session(
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": settings.STRIPE_CURRENCY,
                    "unit_amount": amount_minor,
                    "product_data": {
                        "name": f"{booking.departure.trip.title} — balance",
                        "description": f"Balance owing on booking {booking.reference}",
                    },
                },
            }
        ],
        customer_email=booking.guest.email,
        client_reference_id=booking.reference,
        metadata=metadata,
        success_url=success_url,
        cancel_url=cancel_url,
        # The amount is in the key, so returning to an unchanged balance reopens
        # the same session, while a balance that has moved on opens a new one.
        idempotency_key=f"balance-{booking.reference}-{amount_minor}",
    )
    return str(session.url)
