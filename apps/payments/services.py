"""Turning a verified Stripe event into recorded money.

Everything here is idempotent: Stripe retries, and a retry must not create a
second payment row or send a second confirmation email. The event is only ever
reached from the signature-verified webhook, and every amount written is the
amount Stripe reports, never anything the browser sent.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.bookings.emails import send_booking_confirmation
from apps.bookings.models import Booking, Payment, ScheduledPayment

from . import gateway, plans
from .money import from_minor_units

logger = logging.getLogger(__name__)

# Kept as a module-level alias: some callers and tests import it by this name.
amount_from_minor_units = from_minor_units


class UnknownBooking(LookupError):
    """The event names a booking or instalment this database does not hold."""


def _metadata(obj: Any) -> dict[str, str]:
    """A plain dict of a Stripe object's metadata, whatever its concrete type."""
    meta = getattr(obj, "metadata", None)
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    try:
        return dict(meta)
    except TypeError:
        return {}


# --- Checkout completion ---------------------------------------------------


def handle_checkout_completed(session: Any) -> Booking | None:
    """Routes a completed checkout to the full-payment or deposit path."""
    if _metadata(session).get("kind") == "plan_deposit":
        return confirm_plan_deposit(session)
    return confirm_paid_booking(session)


def _booking_for_session(session: Any) -> Booking:
    reference = (getattr(session, "client_reference_id", None) or "").strip()
    if not reference:
        raise UnknownBooking("checkout session carried no booking reference")
    try:
        return Booking.objects.select_for_update().get(reference=reference)
    except Booking.DoesNotExist as exc:
        raise UnknownBooking(f"no booking with reference {reference}") from exc


@transaction.atomic
def confirm_paid_booking(session: Any) -> Booking:
    """Records a paid-in-full checkout and confirms the booking."""
    booking = _booking_for_session(session)

    intent_id = str(getattr(session, "payment_intent", "") or "")
    if intent_id and booking.payments.filter(stripe_payment_intent_id=intent_id).exists():
        # A retried delivery of an event we have already handled.
        return booking

    amount = from_minor_units(getattr(session, "amount_total", None))

    # Card brand and last four are captured for instalments; a paid-in-full
    # booking has no later charge, so they stay blank here.
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
def confirm_plan_deposit(session: Any) -> Booking:
    """Records the deposit of a payment plan and confirms its booking.

    The saved customer and payment method are read from the deposit's payment
    intent; they are what the instalment charger later needs. If Stripe cannot
    be reached to read them the deposit is still recorded — the guest has paid —
    and the missing card is logged for staff, because the charger refuses to run
    without it rather than charging the wrong card.
    """
    booking = _booking_for_session(session)

    intent_id = str(getattr(session, "payment_intent", "") or "")
    customer_id = str(getattr(session, "customer", "") or "")
    amount = from_minor_units(getattr(session, "amount_total", None))
    method_id = ""
    brand = last4 = ""

    if intent_id:
        try:
            intent = gateway.retrieve_payment_intent(intent_id)
        except Exception:  # noqa: BLE001 — a paid deposit must still be recorded
            logger.exception("Could not read the deposit intent for booking %s", booking.reference)
        else:
            method_id = gateway.payment_method_id(intent)
            brand, last4 = gateway.card_details(intent)
            intent_amount = getattr(intent, "amount", None)
            if intent_amount is not None:
                amount = from_minor_units(intent_amount)
            if not customer_id:
                customer_id = str(getattr(intent, "customer", "") or "")

    plans.confirm_deposit(
        booking=booking,
        payment_intent_id=intent_id,
        customer_id=customer_id,
        payment_method_id=method_id,
        amount=amount,
        card_brand=brand,
        card_last4=last4,
    )
    transaction.on_commit(lambda: send_booking_confirmation(booking))
    return booking


@transaction.atomic
def release_expired_checkout(session: Any) -> Booking | None:
    """Frees the seats and stops the plan when a checkout expires unpaid."""
    reference = (getattr(session, "client_reference_id", None) or "").strip()
    if not reference:
        return None
    booking = Booking.objects.select_for_update().filter(reference=reference).first()
    if booking is None or booking.status != Booking.Status.PENDING:
        return None
    booking.status = Booking.Status.EXPIRED
    booking.hold_expires_at = None
    booking.save(update_fields=["status", "hold_expires_at"])
    plans.cancel_plan(booking)
    logger.info("Released seats for abandoned booking %s", booking.reference)
    return booking


# --- Instalment charges ----------------------------------------------------


def _scheduled_from_metadata(meta: dict[str, str]) -> ScheduledPayment | None:
    sp_id = meta.get("scheduled_payment_id")
    if not sp_id:
        return None
    return ScheduledPayment.objects.select_related("plan__booking__guest").filter(pk=sp_id).first()


def record_instalment_payment(intent: Any) -> None:
    """Handles ``payment_intent.succeeded`` for an instalment charge.

    A backstop to the synchronous charge result and idempotent with it. Deposit
    and paid-in-full intents also raise this event; they are not instalments and
    are left alone here, having been recorded at checkout completion.
    """
    meta = _metadata(intent)
    if meta.get("kind") != "instalment":
        return
    sp = _scheduled_from_metadata(meta)
    if sp is None:
        raise UnknownBooking("payment intent named an instalment we do not hold")
    brand, last4 = gateway.card_details(intent)
    plans.record_instalment(
        scheduled_payment=sp,
        payment_intent_id=str(getattr(intent, "id", "")),
        amount=sp.amount,
        card_brand=brand,
        card_last4=last4,
    )


def fail_instalment_payment(intent: Any) -> None:
    """Handles ``payment_intent.payment_failed`` for an instalment charge."""
    meta = _metadata(intent)
    if meta.get("kind") != "instalment":
        return
    sp = _scheduled_from_metadata(meta)
    if sp is None:
        raise UnknownBooking("payment intent named an instalment we do not hold")
    plans.mark_instalment_failed(sp)
    logger.warning("Instalment %s failed for booking %s", sp.pk, sp.plan.booking.reference)
