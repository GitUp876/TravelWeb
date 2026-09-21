"""Turning a verified Stripe event into a confirmed booking.

Everything here is idempotent: Stripe retries, and a retry must not create a
second payment row or send a second confirmation email.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.bookings.emails import send_booking_confirmation
from apps.bookings.models import Booking, Payment

logger = logging.getLogger(__name__)


class UnknownBooking(LookupError):
    """The event names a booking this database does not hold."""


def amount_from_minor_units(minor_units: int | None) -> Decimal:
    """Stripe counts in cents; we store dollars."""
    return (Decimal(minor_units or 0) / Decimal(100)).quantize(Decimal("0.01"))


@transaction.atomic
def confirm_paid_booking(session: Any) -> Booking:
    """Records payment for a completed checkout and confirms the booking.

    Called only from a signature-verified webhook. The amount written is the
    amount Stripe says it captured, not anything the browser sent.
    """
    reference = (getattr(session, "client_reference_id", None) or "").strip()
    if not reference:
        raise UnknownBooking("checkout session carried no booking reference")

    try:
        booking = Booking.objects.select_for_update().get(reference=reference)
    except Booking.DoesNotExist as exc:
        raise UnknownBooking(f"no booking with reference {reference}") from exc

    intent_id = str(getattr(session, "payment_intent", "") or "")
    if intent_id and booking.payments.filter(stripe_payment_intent_id=intent_id).exists():
        # A retried delivery of an event we have already handled.
        return booking

    amount = amount_from_minor_units(getattr(session, "amount_total", None))

    # Card brand and last four are not on a checkout session; they arrive with
    # the charge. Phase 3 fills them in when it handles charge events. They are
    # the only card fields this application will ever hold.
    Payment.objects.create(
        booking=booking,
        amount=amount,
        kind=Payment.Kind.FULL,
        method=Payment.Method.CARD,
        stripe_payment_intent_id=intent_id,
    )

    booking.amount_paid = booking.amount_paid + amount
    booking.status = Booking.Status.CONFIRMED
    booking.confirmed_at = booking.confirmed_at or timezone.now()
    booking.hold_expires_at = None  # a confirmed booking holds its seats outright
    booking.save(update_fields=["amount_paid", "status", "confirmed_at", "hold_expires_at"])

    customer_id = str(getattr(session, "customer", "") or "")
    if customer_id and not booking.guest.stripe_customer_id:
        booking.guest.stripe_customer_id = customer_id
        booking.guest.save(update_fields=["stripe_customer_id"])

    transaction.on_commit(lambda: send_booking_confirmation(booking))
    logger.info("Confirmed booking %s from checkout session", booking.reference)
    return booking


@transaction.atomic
def release_expired_checkout(session: Any) -> Booking | None:
    """Frees the seats when a guest abandons a checkout that then expires."""
    reference = (getattr(session, "client_reference_id", None) or "").strip()
    if not reference:
        return None
    booking = Booking.objects.select_for_update().filter(reference=reference).first()
    if booking is None or booking.status != Booking.Status.PENDING:
        return None
    booking.status = Booking.Status.EXPIRED
    booking.hold_expires_at = None
    booking.save(update_fields=["status", "hold_expires_at"])
    logger.info("Released seats for abandoned booking %s", booking.reference)
    return booking
