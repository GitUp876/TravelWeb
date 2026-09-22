"""A balance payment coming back through the real webhook.

The signature check is faked; the routing on metadata, the recording and what
it does to a payment plan all run for real.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.bookings.models import Booking, Payment, PaymentPlan, ScheduledPayment, Traveller
from apps.payments import plans

WEBHOOK_URL = "/stripe/webhook/"


def fake_event(event_id, event_type, obj):
    return SimpleNamespace(id=event_id, type=event_type, data=SimpleNamespace(object=obj))


def balance_session(reference, *, amount_cents, intent_id="pi_balance"):
    return SimpleNamespace(
        id="cs_balance",
        client_reference_id=reference,
        payment_intent=intent_id,
        amount_total=amount_cents,
        customer="cus_1",
        metadata={"booking_reference": reference, "kind": "balance"},
    )


@pytest.fixture
def accept_signature(monkeypatch):
    def _install(event):
        monkeypatch.setattr(
            "apps.payments.views.gateway.construct_event", lambda payload, signature: event
        )

    return _install


@pytest.fixture
def booking_on_a_plan(departure, guest, price_option):
    booking = Booking.objects.create(
        departure=departure,
        guest=guest,
        status=Booking.Status.PENDING,
        total_amount=price_option.amount,
    )
    Traveller.objects.create(booking=booking, full_name="Dana", price_option=price_option)
    plan = plans.create_plan_for_booking(booking)
    plans.confirm_deposit(
        booking=booking,
        payment_intent_id="pi_deposit",
        customer_id="cus_1",
        payment_method_id="pm_1",
        amount=plan.deposit_amount,
    )
    booking.refresh_from_db()
    return booking, plan


def _post(client):
    return client.post(
        WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
    )


@pytest.mark.django_db
def test_a_balance_payment_is_recorded_and_settles_the_plan(
    client, booking_on_a_plan, accept_signature
):
    booking, plan = booking_on_a_plan
    balance_cents = int(booking.balance * 100)
    accept_signature(
        fake_event(
            "evt_1",
            "checkout.session.completed",
            balance_session(booking.reference, amount_cents=balance_cents),
        )
    )

    assert _post(client).status_code == 200

    booking.refresh_from_db()
    plan.refresh_from_db()
    assert booking.amount_paid == booking.total_amount
    assert booking.status == Booking.Status.CONFIRMED
    payment = booking.payments.get(kind=Payment.Kind.BALANCE)
    assert payment.amount == booking.total_amount - plan.deposit_amount
    assert payment.method == Payment.Method.CARD
    assert payment.card_last4 == ""  # no card detail is ever stored here
    assert plan.status == PaymentPlan.Status.COMPLETED
    assert not plan.instalments.filter(status=ScheduledPayment.Status.SCHEDULED).exists()


@pytest.mark.django_db
def test_a_redelivered_balance_event_does_not_take_the_money_twice(
    client, booking_on_a_plan, accept_signature
):
    booking, _plan = booking_on_a_plan
    balance_cents = int(booking.balance * 100)
    accept_signature(
        fake_event(
            "evt_2",
            "checkout.session.completed",
            balance_session(booking.reference, amount_cents=balance_cents),
        )
    )

    _post(client)
    _post(client)

    booking.refresh_from_db()
    assert booking.payments.filter(kind=Payment.Kind.BALANCE).count() == 1
    assert booking.amount_paid == booking.total_amount


@pytest.mark.django_db
def test_an_instalment_landing_first_still_leaves_the_booking_reconciled(
    client, booking_on_a_plan, accept_signature, caplog
):
    """The guest was at Stripe when the charger took an instalment.

    The money that arrived is recorded as it arrived — that is the truth — the
    overpayment is logged for staff, and the plan is closed so nothing else is
    ever taken.
    """
    booking, plan = booking_on_a_plan
    balance_cents = int(booking.balance * 100)
    instalment = plan.instalments.first()
    plans.record_instalment(
        scheduled_payment=instalment,
        payment_intent_id="pi_instalment",
        amount=instalment.amount,
    )
    accept_signature(
        fake_event(
            "evt_3",
            "checkout.session.completed",
            balance_session(booking.reference, amount_cents=balance_cents),
        )
    )

    _post(client)

    booking.refresh_from_db()
    plan.refresh_from_db()
    assert booking.amount_paid == booking.total_amount + instalment.amount
    assert "overpaid" in caplog.text
    assert plan.status == PaymentPlan.Status.COMPLETED
    assert not plan.instalments.filter(status=ScheduledPayment.Status.SCHEDULED).exists()
    assert booking not in Booking.objects.outstanding()


@pytest.mark.django_db
def test_a_balance_event_for_an_unknown_booking_records_nothing(client, accept_signature, caplog):
    """Answered, so Stripe stops retrying, but nothing is written from it."""
    accept_signature(
        fake_event(
            "evt_4",
            "checkout.session.completed",
            balance_session("NOSUCHREF", amount_cents=1000),
        )
    )

    _post(client)

    assert Payment.objects.count() == 0
    assert "we do not hold" in caplog.text


@pytest.mark.django_db
def test_offline_money_that_clears_the_balance_settles_the_plan_too(booking_on_a_plan, admin_user):
    from apps.bookings.services import record_offline_payment

    booking, plan = booking_on_a_plan

    record_offline_payment(
        booking=booking,
        amount=booking.balance,
        method=Payment.Method.CHEQUE,
        taken_by=admin_user,
        reference="chq 1201",
    )

    booking.refresh_from_db()
    plan.refresh_from_db()
    assert booking.balance == Decimal("0.00")
    assert plan.status == PaymentPlan.Status.COMPLETED
    assert not plan.instalments.filter(status=ScheduledPayment.Status.SCHEDULED).exists()
