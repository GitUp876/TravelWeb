"""The plan paths through the real webhook: deposit, instalment success, failure.

The signature check and Stripe calls are faked; everything else runs through
the actual view, so the routing on event type and metadata is exercised end to
end.
"""

from types import SimpleNamespace

import pytest

from apps.bookings.models import Booking, Payment, PaymentPlan, ScheduledPayment, Traveller
from apps.payments import plans

WEBHOOK_URL = "/stripe/webhook/"


def fake_event(event_id, event_type, obj):
    return SimpleNamespace(id=event_id, type=event_type, data=SimpleNamespace(object=obj))


@pytest.fixture
def accept_signature(monkeypatch):
    def _install(event):
        monkeypatch.setattr(
            "apps.payments.views.gateway.construct_event", lambda payload, signature: event
        )

    return _install


def fake_intent(*, intent_id="pi_x", amount=None, customer="cus_1", pm="pm_1"):
    card = SimpleNamespace(brand="visa", last4="4242")
    charge = SimpleNamespace(payment_method_details=SimpleNamespace(card=card))
    return SimpleNamespace(
        id=intent_id,
        amount=amount,
        customer=customer,
        latest_charge=charge,
        payment_method=SimpleNamespace(id=pm, card=card),
    )


@pytest.fixture
def pending_plan_booking(departure, guest, price_option):
    booking = Booking.objects.create(
        departure=departure,
        guest=guest,
        status=Booking.Status.PENDING,
        total_amount=price_option.amount,
    )
    Traveller.objects.create(booking=booking, full_name="Dana", price_option=price_option)
    plan = plans.create_plan_for_booking(booking)
    return booking, plan


def _post(client):
    return client.post(
        WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
    )


@pytest.mark.django_db
def test_a_deposit_checkout_confirms_the_booking_and_saves_the_card(
    client, pending_plan_booking, accept_signature, monkeypatch, django_capture_on_commit_callbacks
):
    booking, plan = pending_plan_booking
    deposit_cents = int(plan.deposit_amount * 100)
    monkeypatch.setattr(
        "apps.payments.services.gateway.retrieve_payment_intent",
        lambda pi_id: fake_intent(intent_id="pi_dep", amount=deposit_cents),
    )
    session = SimpleNamespace(
        client_reference_id=booking.reference,
        payment_intent="pi_dep",
        customer="cus_1",
        amount_total=deposit_cents,
        metadata={"kind": "plan_deposit", "booking_reference": booking.reference},
    )
    accept_signature(fake_event("evt_dep", "checkout.session.completed", session))

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        assert _post(client).status_code == 200

    booking.refresh_from_db()
    plan.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.amount_paid == plan.deposit_amount
    assert booking.guest.stripe_customer_id == "cus_1"
    assert plan.stripe_payment_method_id == "pm_1"
    assert booking.payments.get().kind == Payment.Kind.DEPOSIT
    assert callbacks  # a confirmation email was queued


@pytest.mark.django_db
def test_a_deposits_payment_intent_event_does_not_double_record(
    client, pending_plan_booking, accept_signature
):
    booking, plan = pending_plan_booking
    # Confirm the deposit first, as the checkout event would have.
    plans.confirm_deposit(
        booking=booking,
        payment_intent_id="pi_dep",
        customer_id="cus_1",
        payment_method_id="pm_1",
        amount=plan.deposit_amount,
    )
    intent = fake_intent(intent_id="pi_dep")
    intent.metadata = {"kind": "plan_deposit", "booking_reference": booking.reference}
    accept_signature(fake_event("evt_dep_pi", "payment_intent.succeeded", intent))

    assert _post(client).status_code == 200
    booking.refresh_from_db()
    assert booking.payments.count() == 1  # not recorded a second time as an instalment


@pytest.mark.django_db
def test_an_instalment_intent_succeeding_records_the_instalment(
    client, pending_plan_booking, accept_signature
):
    booking, plan = pending_plan_booking
    plans.confirm_deposit(
        booking=booking,
        payment_intent_id="pi_dep",
        customer_id="cus_1",
        payment_method_id="pm_1",
        amount=plan.deposit_amount,
    )
    sp = plan.instalments.first()
    intent = fake_intent(intent_id="pi_inst_1", amount=int(sp.amount * 100))
    intent.metadata = {"kind": "instalment", "scheduled_payment_id": str(sp.pk)}
    accept_signature(fake_event("evt_inst", "payment_intent.succeeded", intent))

    assert _post(client).status_code == 200
    sp.refresh_from_db()
    booking.refresh_from_db()
    assert sp.status == ScheduledPayment.Status.PAID
    assert booking.payments.filter(kind=Payment.Kind.INSTALMENT).count() == 1
    assert booking.amount_paid == plan.deposit_amount + sp.amount


@pytest.mark.django_db
def test_an_instalment_intent_failing_flags_the_plan(
    client, pending_plan_booking, accept_signature
):
    booking, plan = pending_plan_booking
    sp = plan.instalments.first()
    intent = SimpleNamespace(
        id="pi_inst_fail",
        metadata={"kind": "instalment", "scheduled_payment_id": str(sp.pk)},
    )
    accept_signature(fake_event("evt_fail", "payment_intent.payment_failed", intent))

    assert _post(client).status_code == 200
    sp.refresh_from_db()
    plan.refresh_from_db()
    assert sp.status == ScheduledPayment.Status.FAILED
    assert plan.status == PaymentPlan.Status.FAILED
    assert not booking.payments.filter(kind=Payment.Kind.INSTALMENT).exists()
