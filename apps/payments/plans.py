"""Payment plans: a deposit today, then equal instalments Stripe charges itself.

The money is split here, from the total already on the booking — never from
anything the browser sent. The card is collected and saved by Stripe's hosted
checkout when the deposit is paid; instalments are then charged off-session
against that saved card, so no card detail ever reaches this application.

Every write is idempotent. The instalment charger uses a stable idempotency key
per instalment, so a crash between charging and recording, or a repeated run of
the scheduled job, can never take a second payment for the same instalment.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.bookings.models import Booking, Payment, PaymentPlan, ScheduledPayment

from . import gateway
from .money import from_minor_units, to_minor_units

logger = logging.getLogger(__name__)

# A monthly plan should never run away to hundreds of instalments; this only
# guards against a bad date, since eligibility already bounds the horizon.
MAX_INSTALMENTS = 36


class PlanNotAvailable(RuntimeError):
    """This booking cannot be put on a payment plan."""


# --- Working out the schedule ---------------------------------------------


def _minus_one_month(d: date) -> date:
    """The same day one month earlier, clamped to that month's last day."""
    year = d.year - 1 if d.month == 1 else d.year
    month = 12 if d.month == 1 else d.month - 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def instalment_due_dates(final_due: date, today: date) -> list[date]:
    """Monthly instalment dates ending on ``final_due``, all after today.

    Worked backwards from the final payment date so the last instalment always
    lands exactly on it; dates that fall on or before today are dropped, because
    an instalment must leave time to notify and charge.
    """
    dates: list[date] = []
    cursor = final_due
    while cursor > today and len(dates) < MAX_INSTALMENTS:
        dates.append(cursor)
        cursor = _minus_one_month(cursor)
    dates.reverse()
    return dates


def split_minor_units(total_minor: int, count: int) -> list[int]:
    """Split an amount into ``count`` parts that sum back exactly.

    The odd cents go on the earliest instalments, so no rounding is ever lost
    and no instalment differs from another by more than a penny.
    """
    if count <= 0:
        raise ValueError("cannot split into zero instalments")
    base, remainder = divmod(total_minor, count)
    return [base + (1 if i < remainder else 0) for i in range(count)]


# --- Building a plan -------------------------------------------------------


@transaction.atomic
def create_plan_for_booking(booking: Booking) -> PaymentPlan:
    """Creates the plan and its scheduled instalments for a pending booking.

    Raises ``PlanNotAvailable`` if the departure does not offer a plan or there
    is nothing left to spread once the deposit is taken.
    """
    departure = booking.departure
    if not departure.payment_plan_available:
        raise PlanNotAvailable("this departure does not offer a payment plan")

    deposit = departure.deposit_amount
    remaining = booking.total_amount - deposit
    if remaining <= 0:
        raise PlanNotAvailable("the deposit already covers the whole booking")

    today = timezone.localdate()
    dates = instalment_due_dates(departure.final_payment_due_date, today)
    if not dates:
        raise PlanNotAvailable("no time is left for an instalment before the due date")

    minor = split_minor_units(to_minor_units(remaining), len(dates))
    amounts = [from_minor_units(m) for m in minor]

    plan = PaymentPlan.objects.create(
        booking=booking,
        deposit_amount=deposit,
        instalment_count=len(dates),
        status=PaymentPlan.Status.ACTIVE,
    )
    ScheduledPayment.objects.bulk_create(
        ScheduledPayment(plan=plan, due_date=due, amount=amount)
        for due, amount in zip(dates, amounts, strict=True)
    )
    return plan


@transaction.atomic
def cancel_plan(booking: Booking) -> None:
    """Stops a plan when its booking never confirmed or is cancelled."""
    plan = _plan_for(booking)
    if plan is None:
        return
    plan.instalments.filter(status=ScheduledPayment.Status.SCHEDULED).update(
        status=ScheduledPayment.Status.CANCELLED
    )
    if plan.status == PaymentPlan.Status.ACTIVE:
        plan.status = PaymentPlan.Status.CANCELLED
        plan.save(update_fields=["status"])


@transaction.atomic
def settle_plan(booking: Booking) -> None:
    """Closes a plan whose balance has been paid off in one go.

    Every instalment still waiting or flagged is cancelled, because the money it
    was going to collect has already arrived. Leaving them scheduled would let
    the off-session charger take it a second time, which is the one mistake a
    guest never forgives.
    """
    plan = _plan_for(booking)
    if plan is None:
        return
    plan.instalments.filter(
        status__in=(ScheduledPayment.Status.SCHEDULED, ScheduledPayment.Status.FAILED)
    ).update(status=ScheduledPayment.Status.CANCELLED)
    if plan.status in (PaymentPlan.Status.ACTIVE, PaymentPlan.Status.FAILED):
        plan.status = PaymentPlan.Status.COMPLETED
        plan.save(update_fields=["status"])
    logger.info("Settled the plan on booking %s: the balance is paid", booking.reference)


def _plan_for(booking: Booking) -> PaymentPlan | None:
    return PaymentPlan.objects.filter(booking=booking).first()


# --- Recording money that moved -------------------------------------------


@transaction.atomic
def confirm_deposit(
    *,
    booking: Booking,
    payment_intent_id: str,
    customer_id: str,
    payment_method_id: str,
    amount: Decimal,
    card_brand: str = "",
    card_last4: str = "",
) -> None:
    """Records the deposit, confirms the booking and arms the plan.

    Idempotent on the payment intent id: a retried webhook does nothing. The
    saved customer and payment method are what the off-session charger later
    uses; without them the charger refuses to run.
    """
    if (
        payment_intent_id
        and booking.payments.filter(stripe_payment_intent_id=payment_intent_id).exists()
    ):
        return

    Payment.objects.create(
        booking=booking,
        amount=amount,
        kind=Payment.Kind.DEPOSIT,
        method=Payment.Method.CARD,
        stripe_payment_intent_id=payment_intent_id,
        card_brand=card_brand,
        card_last4=card_last4,
    )

    booking.amount_paid = booking.amount_paid + amount
    booking.status = Booking.Status.CONFIRMED
    booking.confirmed_at = booking.confirmed_at or timezone.now()
    booking.hold_expires_at = None
    booking.save(update_fields=["amount_paid", "status", "confirmed_at", "hold_expires_at"])

    if customer_id and not booking.guest.stripe_customer_id:
        booking.guest.stripe_customer_id = customer_id
        booking.guest.save(update_fields=["stripe_customer_id"])

    plan = _plan_for(booking)
    if plan is not None:
        fields = []
        if payment_method_id and not plan.stripe_payment_method_id:
            plan.stripe_payment_method_id = payment_method_id
            fields.append("stripe_payment_method_id")
        if plan.status != PaymentPlan.Status.ACTIVE:
            plan.status = PaymentPlan.Status.ACTIVE
            fields.append("status")
        if fields:
            plan.save(update_fields=fields)

    logger.info("Recorded deposit for booking %s", booking.reference)


@transaction.atomic
def record_instalment(
    *,
    scheduled_payment: ScheduledPayment,
    payment_intent_id: str,
    amount: Decimal,
    card_brand: str = "",
    card_last4: str = "",
) -> None:
    """Marks one instalment paid and pays down the booking balance.

    Idempotent on the payment intent id, so the synchronous charge result and a
    later ``payment_intent.succeeded`` webhook for the same charge cannot both
    add money.
    """
    sp = ScheduledPayment.objects.select_for_update().get(pk=scheduled_payment.pk)
    booking = sp.plan.booking

    if (
        payment_intent_id
        and booking.payments.filter(stripe_payment_intent_id=payment_intent_id).exists()
    ):
        if sp.status != ScheduledPayment.Status.PAID:
            sp.status = ScheduledPayment.Status.PAID
            sp.stripe_payment_intent_id = payment_intent_id
            sp.save(update_fields=["status", "stripe_payment_intent_id"])
        return

    Payment.objects.create(
        booking=booking,
        amount=amount,
        kind=Payment.Kind.INSTALMENT,
        method=Payment.Method.CARD,
        stripe_payment_intent_id=payment_intent_id,
        card_brand=card_brand,
        card_last4=card_last4,
    )
    sp.status = ScheduledPayment.Status.PAID
    sp.stripe_payment_intent_id = payment_intent_id
    sp.save(update_fields=["status", "stripe_payment_intent_id"])

    booking.amount_paid = booking.amount_paid + amount
    booking.save(update_fields=["amount_paid"])
    _complete_plan_if_finished(sp.plan)
    logger.info("Recorded instalment %s for booking %s", sp.pk, booking.reference)


@transaction.atomic
def mark_instalment_failed(scheduled_payment: ScheduledPayment) -> None:
    """A declined instalment: flag it and the plan for staff to chase."""
    sp = ScheduledPayment.objects.select_for_update().get(pk=scheduled_payment.pk)
    if sp.status == ScheduledPayment.Status.PAID:
        return
    sp.status = ScheduledPayment.Status.FAILED
    sp.save(update_fields=["status"])
    plan = sp.plan
    if plan.status == PaymentPlan.Status.ACTIVE:
        plan.status = PaymentPlan.Status.FAILED
        plan.save(update_fields=["status"])


def _complete_plan_if_finished(plan: PaymentPlan) -> None:
    statuses = set(plan.instalments.values_list("status", flat=True))
    settled = {
        ScheduledPayment.Status.PAID,
        ScheduledPayment.Status.CANCELLED,
        ScheduledPayment.Status.WRITTEN_OFF,
    }
    finished = ScheduledPayment.Status.PAID in statuses and statuses <= settled
    if finished and plan.status != PaymentPlan.Status.COMPLETED:
        plan.status = PaymentPlan.Status.COMPLETED
        plan.save(update_fields=["status"])


# --- Charging instalments off-session -------------------------------------


def due_instalments(today: date | None = None):
    """Instalments ready to charge: due, still scheduled, on a live plan.

    Confined to confirmed bookings on active plans, so an abandoned or cancelled
    booking whose rows still exist is never charged.
    """
    today = today or timezone.localdate()
    return (
        ScheduledPayment.objects.filter(
            status=ScheduledPayment.Status.SCHEDULED,
            due_date__lte=today,
            plan__status=PaymentPlan.Status.ACTIVE,
            plan__booking__status=Booking.Status.CONFIRMED,
            # Whatever paid the balance off — the guest online, a cheque taken by
            # staff — there is nothing left to collect on this booking.
            plan__booking__amount_paid__lt=F("plan__booking__total_amount"),
        )
        .select_related("plan", "plan__booking", "plan__booking__guest")
        .order_by("due_date", "pk")
    )


def charge_instalment(scheduled_payment: ScheduledPayment) -> bool:
    """Charges one instalment against the saved card. Returns whether it paid.

    Refuses to charge unless the deposit confirmed the booking and a customer
    and payment method were saved, so a plan can never charge a card the guest
    did not present at checkout.
    """
    sp = scheduled_payment
    plan = sp.plan
    booking = plan.booking
    guest = booking.guest

    if (
        booking.status != Booking.Status.CONFIRMED
        or not guest.stripe_customer_id
        or not plan.stripe_payment_method_id
    ):
        logger.warning("Skipping instalment %s: booking not armed for charging", sp.pk)
        return False

    if booking.balance <= 0:
        # The balance was cleared after this row was picked up. Settling the plan
        # is the right answer, not a charge for money that is already in.
        logger.info("Instalment %s is no longer owed; settling the plan", sp.pk)
        settle_plan(booking)
        return False

    sp.attempt_count = sp.attempt_count + 1
    sp.last_attempt_at = timezone.now()
    sp.save(update_fields=["attempt_count", "last_attempt_at"])

    try:
        intent = gateway.charge_saved_card(
            amount_minor=to_minor_units(sp.amount),
            currency=settings.STRIPE_CURRENCY,
            customer_id=guest.stripe_customer_id,
            payment_method_id=plan.stripe_payment_method_id,
            metadata={
                "booking_reference": booking.reference,
                "scheduled_payment_id": str(sp.pk),
                "kind": "instalment",
            },
            # Stable per instalment: a repeated run returns the same intent
            # instead of taking a second payment.
            idempotency_key=f"instalment-{sp.pk}",
        )
    except gateway.CardDeclined:
        mark_instalment_failed(sp)
        logger.warning("Instalment %s declined for booking %s", sp.pk, booking.reference)
        return False
    except gateway.PaymentConfigurationError:
        logger.error("Instalment run attempted while Stripe is unconfigured")
        return False

    if str(getattr(intent, "status", "")) == "succeeded":
        brand, last4 = gateway.card_details(intent)
        record_instalment(
            scheduled_payment=sp,
            payment_intent_id=str(getattr(intent, "id", "")),
            amount=sp.amount,
            card_brand=brand,
            card_last4=last4,
        )
        return True

    # Anything other than an outright success off-session (needs the cardholder,
    # still processing) is left for staff rather than silently retried.
    mark_instalment_failed(sp)
    return False


def charge_due_instalments(today: date | None = None) -> tuple[int, int]:
    """Charges every due instalment. Returns (charged, failed)."""
    charged = failed = 0
    for sp in list(due_instalments(today)):
        if charge_instalment(sp):
            charged += 1
        else:
            failed += 1
    if charged or failed:
        logger.info("Instalment run: %s charged, %s failed", charged, failed)
    return charged, failed
