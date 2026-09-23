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
from django.db.models import Sum
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
    """Routes a completed checkout by what it was opened to collect."""
    kind = _metadata(session).get("kind")
    if kind == "plan_deposit":
        return confirm_plan_deposit(session)
    if kind == "balance":
        return confirm_balance_payment(session)
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
def confirm_balance_payment(session: Any) -> Booking:
    """Records a guest clearing the balance on a booking they already hold.

    The booking is confirmed already, so nothing about its status changes here;
    only the money does. If the booking carries a payment plan, the instalments
    still waiting are stood down, because what they were going to collect has
    just arrived — an instalment left scheduled would charge the guest twice.
    """
    booking = _booking_for_session(session)

    intent_id = str(getattr(session, "payment_intent", "") or "")
    if intent_id and booking.payments.filter(stripe_payment_intent_id=intent_id).exists():
        # A retried delivery of an event we have already handled.
        return booking

    amount = from_minor_units(getattr(session, "amount_total", None))

    Payment.objects.create(
        booking=booking,
        amount=amount,
        kind=Payment.Kind.BALANCE,
        method=Payment.Method.CARD,
        stripe_payment_intent_id=intent_id,
    )
    booking.amount_paid = booking.amount_paid + amount
    booking.save(update_fields=["amount_paid"])

    if booking.status != Booking.Status.CONFIRMED:
        # The booking was cancelled while the guest was at Stripe. The money is
        # real and is recorded as such; putting it right is a refund, which is a
        # staff decision, so this only makes sure they can see it.
        logger.warning(
            "Balance payment arrived for %s booking %s", booking.status, booking.reference
        )
    if booking.balance < 0:
        logger.warning(
            "Booking %s is overpaid by %s after a balance payment",
            booking.reference,
            -booking.balance,
        )
    if booking.balance <= 0:
        plans.settle_plan(booking)

    logger.info("Recorded a balance payment on booking %s", booking.reference)
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


# --- Refunds ---------------------------------------------------------------


@transaction.atomic
def record_refund(charge: Any) -> Booking:
    """Handles ``charge.refunded``: mirrors a refund made in the Stripe dashboard.

    Refunds are issued in Stripe, not here, so without this a refunded booking
    would still show the money as paid. Stripe reports the charge's cumulative
    ``amount_refunded``; the refund rows already recorded against the same
    payment intent are subtracted from it, and only the difference is written.
    That makes a retried event, or two partial refunds arriving out of order,
    record each dollar exactly once. The booking row is locked first, so two
    deliveries for one booking cannot both see the same running total.

    Nothing else about the booking changes. Whether a refund also cancels the
    booking or reduces its price is a staff decision; a confirmed booking left
    owing money after a refund shows on the payments-due report.
    """
    intent_id = str(getattr(charge, "payment_intent", "") or "")
    if not intent_id:
        raise UnknownBooking("refunded charge carried no payment intent")
    original = (
        Payment.objects.filter(stripe_payment_intent_id=intent_id)
        .exclude(kind=Payment.Kind.REFUND)
        .order_by("pk")
        .first()
    )
    if original is None:
        raise UnknownBooking("refunded charge named a payment we do not hold")
    booking = Booking.objects.select_for_update().get(pk=original.booking_id)

    refunded_total = from_minor_units(getattr(charge, "amount_refunded", None))
    recorded = -(
        booking.payments.filter(
            kind=Payment.Kind.REFUND, stripe_payment_intent_id=intent_id
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    new_money = refunded_total - recorded
    if new_money < 0:
        # Stripe reports less refunded than we hold: a refund failed after we
        # recorded it. Rare enough to leave to a person rather than guess.
        logger.error(
            "Stripe reports less refunded on booking %s than is recorded", booking.reference
        )
        return booking
    if new_money == 0:
        return booking

    Payment.objects.create(
        booking=booking,
        amount=-new_money,
        kind=Payment.Kind.REFUND,
        method=Payment.Method.CARD,
        stripe_payment_intent_id=intent_id,
        card_brand=original.card_brand,
        card_last4=original.card_last4,
    )
    # A card refund can never exceed what that card paid, but offline payments
    # share the running total, so floor at zero rather than trip the constraint.
    booking.amount_paid = max(booking.amount_paid - new_money, 0)
    booking.save(update_fields=["amount_paid"])

    if booking.status == Booking.Status.CONFIRMED and booking.balance > 0:
        logger.warning(
            "Booking %s is still confirmed and now owes %s after a refund",
            booking.reference,
            booking.balance,
        )
    logger.info("Recorded a %s refund on booking %s", new_money, booking.reference)
    return booking
