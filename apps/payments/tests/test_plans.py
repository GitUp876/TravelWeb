"""Payment plans: the schedule maths, the deposit, and off-session charging.

Stripe is never reached: the gateway functions are replaced with fakes, so
these tests exercise our own money handling, idempotency and guardrails.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.bookings.models import Booking, Payment, PaymentPlan, ScheduledPayment, Traveller
from apps.payments import plans

# --- Fakes -----------------------------------------------------------------


def fake_intent(
    *,
    intent_id="pi_test",
    status="succeeded",
    brand="visa",
    last4="4242",
    amount=None,
    customer="cus_test",
):
    card = SimpleNamespace(brand=brand, last4=last4)
    charge = SimpleNamespace(payment_method_details=SimpleNamespace(card=card))
    return SimpleNamespace(
        id=intent_id,
        status=status,
        latest_charge=charge,
        payment_method=SimpleNamespace(id="pm_test", card=card),
        amount=amount,
        customer=customer,
    )


@pytest.fixture
def plan_booking(departure, guest, price_option):
    """A pending booking priced so a deposit leaves a real balance to spread."""

    def _make(travellers=1, status=Booking.Status.PENDING):
        booking = Booking.objects.create(
            departure=departure,
            guest=guest,
            status=status,
            total_amount=price_option.amount * travellers,
        )
        for index in range(travellers):
            Traveller.objects.create(
                booking=booking, full_name=f"Traveller {index}", price_option=price_option
            )
        return booking

    return _make


# --- Schedule maths --------------------------------------------------------


def test_split_never_loses_a_cent():
    parts = plans.split_minor_units(10000, 3)
    assert sum(parts) == 10000
    assert parts == [3334, 3333, 3333]  # the odd cent rides on the first instalment
    assert max(parts) - min(parts) <= 1


def test_split_handles_an_exact_division():
    assert plans.split_minor_units(12000, 4) == [3000, 3000, 3000, 3000]


def test_split_rejects_zero_instalments():
    with pytest.raises(ValueError, match="zero instalments"):
        plans.split_minor_units(100, 0)


def test_instalment_dates_end_on_the_due_date_and_step_monthly():
    today = date(2026, 1, 15)
    final = date(2026, 4, 20)
    dates = plans.instalment_due_dates(final, today)
    assert dates[-1] == final
    assert dates == [
        date(2026, 1, 20),
        date(2026, 2, 20),
        date(2026, 3, 20),
        date(2026, 4, 20),
    ]
    assert all(d > today for d in dates)


def test_instalment_dates_clamp_to_a_short_month():
    # Stepping back a month from 31 March must not invent 31 February.
    dates = plans.instalment_due_dates(date(2026, 3, 31), date(2026, 1, 1))
    assert date(2026, 2, 28) in dates


def test_instalment_dates_drop_anything_not_after_today():
    dates = plans.instalment_due_dates(date(2026, 1, 20), date(2026, 1, 25))
    assert dates == []


# --- Building a plan -------------------------------------------------------


@pytest.mark.django_db
def test_a_plan_splits_the_balance_after_the_deposit(plan_booking):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)

    instalments = list(plan.instalments.all())
    assert plan.instalment_count == len(instalments)
    assert plan.deposit_amount == booking.departure.deposit_amount
    total_scheduled = sum((i.amount for i in instalments), Decimal("0.00"))
    assert plan.deposit_amount + total_scheduled == booking.total_amount
    assert instalments[-1].due_date == booking.departure.final_payment_due_date


@pytest.mark.django_db
def test_a_plan_is_refused_when_the_departure_does_not_offer_one(plan_booking, departure):
    departure.final_payment_due_date = None
    departure.save()
    booking = plan_booking()
    with pytest.raises(plans.PlanNotAvailable):
        plans.create_plan_for_booking(booking)


@pytest.mark.django_db
def test_a_plan_is_refused_when_the_deposit_covers_everything(plan_booking, departure):
    departure.deposit_amount = Decimal("1000.00")
    departure.save()
    booking = plan_booking()
    with pytest.raises(plans.PlanNotAvailable):
        plans.create_plan_for_booking(booking)


# --- The deposit -----------------------------------------------------------


@pytest.mark.django_db
def test_confirming_a_deposit_confirms_the_booking_and_arms_the_plan(plan_booking):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)

    plans.confirm_deposit(
        booking=booking,
        payment_intent_id="pi_dep",
        customer_id="cus_1",
        payment_method_id="pm_1",
        amount=plan.deposit_amount,
        card_brand="visa",
        card_last4="4242",
    )

    booking.refresh_from_db()
    plan.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.amount_paid == plan.deposit_amount
    assert booking.guest.stripe_customer_id == "cus_1"
    assert plan.stripe_payment_method_id == "pm_1"
    assert plan.status == PaymentPlan.Status.ACTIVE
    deposit = booking.payments.get()
    assert deposit.kind == Payment.Kind.DEPOSIT
    assert deposit.card_last4 == "4242"


@pytest.mark.django_db
def test_a_deposit_is_recorded_only_once(plan_booking):
    booking = plan_booking()
    plans.create_plan_for_booking(booking)
    args = {
        "booking": booking,
        "payment_intent_id": "pi_dep",
        "customer_id": "cus_1",
        "payment_method_id": "pm_1",
        "amount": Decimal("25.00"),
    }
    plans.confirm_deposit(**args)
    plans.confirm_deposit(**args)  # a retried webhook

    booking.refresh_from_db()
    assert booking.payments.count() == 1
    assert booking.amount_paid == Decimal("25.00")


# --- Charging instalments --------------------------------------------------


def _arm(booking, plan):
    booking.status = Booking.Status.CONFIRMED
    booking.amount_paid = plan.deposit_amount
    booking.save()
    booking.guest.stripe_customer_id = "cus_1"
    booking.guest.save()
    plan.stripe_payment_method_id = "pm_1"
    plan.save()


@pytest.mark.django_db
def test_a_due_instalment_is_charged_and_recorded(plan_booking, monkeypatch):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)
    _arm(booking, plan)

    sp = plan.instalments.first()
    sp.due_date = timezone.localdate()
    sp.save()

    captured = {}

    def fake_charge(**kwargs):
        captured.update(kwargs)
        return fake_intent(intent_id="pi_inst_1")

    monkeypatch.setattr(plans.gateway, "charge_saved_card", fake_charge)

    charged, failed = plans.charge_due_instalments()
    assert (charged, failed) == (1, 0)

    sp.refresh_from_db()
    booking.refresh_from_db()
    assert sp.status == ScheduledPayment.Status.PAID
    assert sp.stripe_payment_intent_id == "pi_inst_1"
    payment = booking.payments.get(kind=Payment.Kind.INSTALMENT)
    assert payment.amount == sp.amount
    assert booking.amount_paid == plan.deposit_amount + sp.amount
    # Charged against the saved card, with an idempotency key stable per instalment.
    assert captured["customer_id"] == "cus_1"
    assert captured["payment_method_id"] == "pm_1"
    assert captured["idempotency_key"] == f"instalment-{sp.pk}"


@pytest.mark.django_db
def test_paying_the_last_instalment_completes_the_plan(plan_booking, monkeypatch):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)
    _arm(booking, plan)
    plan.instalments.update(due_date=timezone.localdate())

    counter = {"n": 0}

    def fake_charge(**kwargs):
        counter["n"] += 1
        return fake_intent(intent_id=f"pi_inst_{counter['n']}")

    monkeypatch.setattr(plans.gateway, "charge_saved_card", fake_charge)

    plans.charge_due_instalments()

    plan.refresh_from_db()
    booking.refresh_from_db()
    assert plan.status == PaymentPlan.Status.COMPLETED
    assert booking.amount_paid == booking.total_amount
    assert booking.is_paid_in_full


@pytest.mark.django_db
def test_a_declined_instalment_flags_the_plan_for_staff(plan_booking, monkeypatch):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)
    _arm(booking, plan)
    sp = plan.instalments.first()
    sp.due_date = timezone.localdate()
    sp.save()

    def decline(**kwargs):
        raise plans.gateway.CardDeclined("card_declined")

    monkeypatch.setattr(plans.gateway, "charge_saved_card", decline)

    charged, failed = plans.charge_due_instalments()
    assert (charged, failed) == (0, 1)

    sp.refresh_from_db()
    plan.refresh_from_db()
    assert sp.status == ScheduledPayment.Status.FAILED
    assert sp.attempt_count == 1
    assert plan.status == PaymentPlan.Status.FAILED
    assert not booking.payments.filter(kind=Payment.Kind.INSTALMENT).exists()


@pytest.mark.django_db
def test_an_unconfirmed_booking_is_never_charged(plan_booking, monkeypatch):
    booking = plan_booking()  # still pending: deposit never paid
    plan = plans.create_plan_for_booking(booking)
    plan.instalments.update(due_date=timezone.localdate())

    def explode(**kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("charge attempted on an unconfirmed booking")

    monkeypatch.setattr(plans.gateway, "charge_saved_card", explode)

    charged, failed = plans.charge_due_instalments()
    assert charged == 0
    assert not Payment.objects.filter(kind=Payment.Kind.INSTALMENT).exists()


@pytest.mark.django_db
def test_recording_an_instalment_is_idempotent(plan_booking):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)
    _arm(booking, plan)
    sp = plan.instalments.first()

    args = {"scheduled_payment": sp, "payment_intent_id": "pi_inst_1", "amount": sp.amount}
    plans.record_instalment(**args)
    plans.record_instalment(**args)  # the webhook backstop for the same charge

    booking.refresh_from_db()
    assert booking.payments.filter(kind=Payment.Kind.INSTALMENT).count() == 1
    assert booking.amount_paid == plan.deposit_amount + sp.amount


@pytest.mark.django_db
def test_only_due_instalments_on_live_plans_are_selected(plan_booking):
    booking = plan_booking()
    plan = plans.create_plan_for_booking(booking)
    _arm(booking, plan)

    future = plan.instalments.order_by("due_date").last()
    due = plan.instalments.order_by("due_date").first()
    due.due_date = timezone.localdate() - timedelta(days=1)
    due.save()

    selected = list(plans.due_instalments())
    assert due in selected
    assert future not in selected
