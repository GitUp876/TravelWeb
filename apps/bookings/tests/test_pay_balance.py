"""A guest clearing the balance on their own booking.

The two things that matter here: who is allowed to open a checkout at all, and
that the amount charged is the one this database worked out rather than
anything that arrived in the request.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.bookings.models import Booking, PaymentPlan, ScheduledPayment
from apps.bookings.tokens import make_token
from apps.payments import checkout, gateway, plans


@pytest.fixture
def owing(make_booking, price_option):
    """A confirmed booking with a deposit paid and a balance still to come."""
    booking = make_booking(status=Booking.Status.CONFIRMED, travellers=1)
    booking.total_amount = price_option.amount
    booking.amount_paid = Decimal("25.00")
    booking.save(update_fields=["total_amount", "amount_paid"])
    return booking


@pytest.fixture
def fake_balance_checkout(monkeypatch):
    """Stands in for Stripe, recording what it was asked to charge."""
    calls = {}

    def _create(**kwargs):
        calls.update(kwargs)

        class _Session:
            url = "https://checkout.stripe.test/session/balance"

        return _Session()

    monkeypatch.setattr("apps.payments.checkout.gateway.create_checkout_session", _create)
    return calls


def _pay_url(booking):
    return reverse("bookings:pay-balance", kwargs={"token": make_token(booking.reference)})


def _detail_url(booking):
    return reverse("bookings:detail", kwargs={"token": make_token(booking.reference)})


# --- The page --------------------------------------------------------------


@pytest.mark.django_db
def test_a_booking_with_a_balance_offers_to_take_it(client, owing):
    body = client.get(_detail_url(owing)).content.decode()
    assert "Pay the $124.00 balance now" in body


@pytest.mark.django_db
def test_a_booking_paid_in_full_offers_nothing(client, owing):
    owing.amount_paid = owing.total_amount
    owing.save(update_fields=["amount_paid"])

    body = client.get(_detail_url(owing)).content.decode()
    assert "balance now" not in body


# --- Opening the checkout --------------------------------------------------


@pytest.mark.django_db
def test_paying_the_balance_sends_the_guest_to_stripe(client, owing, fake_balance_checkout):
    response = client.post(_pay_url(owing))

    assert response.status_code == 302
    assert response.url == "https://checkout.stripe.test/session/balance"
    assert fake_balance_checkout["client_reference_id"] == owing.reference
    assert fake_balance_checkout["metadata"]["kind"] == "balance"


@pytest.mark.django_db
def test_the_amount_charged_is_the_balance_we_hold_not_what_was_posted(
    client, owing, fake_balance_checkout
):
    # A guest editing the form to pay a dollar must still be charged the balance.
    client.post(_pay_url(owing), {"amount": "1.00", "unit_amount": "100", "balance": "1"})

    line_item = fake_balance_checkout["line_items"][0]
    assert line_item["price_data"]["unit_amount"] == 12400  # $149.00 less the $25.00 deposit
    assert line_item["quantity"] == 1


@pytest.mark.django_db
def test_the_link_alone_cannot_open_a_checkout(client, owing, fake_balance_checkout):
    assert client.get(_pay_url(owing)).status_code == 405
    assert fake_balance_checkout == {}


@pytest.mark.django_db
def test_a_forged_or_expired_link_is_a_404(client, owing, fake_balance_checkout):
    url = reverse("bookings:pay-balance", kwargs={"token": "not-a-real-token"})
    assert client.post(url).status_code == 404
    assert fake_balance_checkout == {}


# --- Who is refused --------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [Booking.Status.PENDING, Booking.Status.CANCELLED, Booking.Status.EXPIRED],
)
def test_only_a_confirmed_booking_may_be_paid(client, owing, fake_balance_checkout, status):
    owing.status = status
    owing.save(update_fields=["status"])

    response = client.post(_pay_url(owing))

    assert response.status_code == 302
    assert response.url == _detail_url(owing)
    assert fake_balance_checkout == {}


@pytest.mark.django_db
def test_a_booking_that_owes_nothing_is_not_charged(client, owing, fake_balance_checkout):
    owing.amount_paid = owing.total_amount
    owing.save(update_fields=["amount_paid"])

    response = client.post(_pay_url(owing))

    assert response.url == _detail_url(owing)
    assert fake_balance_checkout == {}


@pytest.mark.django_db
def test_nothing_is_offered_while_payments_are_switched_off(
    client, owing, fake_balance_checkout, settings
):
    settings.PAYMENTS_ENABLED = False

    assert "balance now" not in client.get(_detail_url(owing)).content.decode()
    assert client.post(_pay_url(owing)).url == _detail_url(owing)
    assert fake_balance_checkout == {}


@pytest.mark.django_db
def test_an_unreachable_stripe_leaves_the_booking_alone(client, owing, monkeypatch):
    def _boom(**kwargs):
        raise gateway.PaymentConfigurationError("no key")

    monkeypatch.setattr("apps.payments.checkout.gateway.create_checkout_session", _boom)

    response = client.post(_pay_url(owing))

    owing.refresh_from_db()
    assert response.url == _detail_url(owing)
    assert owing.amount_paid == Decimal("25.00")
    assert owing.status == Booking.Status.CONFIRMED


@pytest.mark.django_db
def test_the_helper_refuses_a_booking_that_owes_nothing(owing):
    owing.amount_paid = owing.total_amount

    with pytest.raises(checkout.NothingOwed):
        checkout.start_balance_checkout(owing, success_url="https://x/", cancel_url="https://x/")


# --- What it does to a payment plan ----------------------------------------


@pytest.fixture
def owing_on_a_plan(departure, guest, price_option, make_booking):
    booking = make_booking(status=Booking.Status.CONFIRMED, travellers=1)
    booking.total_amount = price_option.amount
    booking.save(update_fields=["total_amount"])
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


@pytest.mark.django_db
def test_settling_a_plan_stands_its_instalments_down(owing_on_a_plan):
    booking, plan = owing_on_a_plan
    assert plan.instalments.filter(status=ScheduledPayment.Status.SCHEDULED).exists()

    plans.settle_plan(booking)

    plan.refresh_from_db()
    assert plan.status == PaymentPlan.Status.COMPLETED
    assert not plan.instalments.filter(status=ScheduledPayment.Status.SCHEDULED).exists()


@pytest.mark.django_db
def test_a_failed_instalment_is_stood_down_too_once_the_balance_is_paid(owing_on_a_plan):
    booking, plan = owing_on_a_plan
    first = plan.instalments.first()
    plans.mark_instalment_failed(first)

    plans.settle_plan(booking)

    first.refresh_from_db()
    plan.refresh_from_db()
    assert first.status == ScheduledPayment.Status.CANCELLED
    assert plan.status == PaymentPlan.Status.COMPLETED


@pytest.mark.django_db
def test_a_booking_that_owes_nothing_is_never_picked_up_for_charging(owing_on_a_plan):
    booking, plan = owing_on_a_plan
    due = list(plans.due_instalments(plan.instalments.first().due_date))
    assert due  # it is due while money is owed

    booking.amount_paid = booking.total_amount
    booking.save(update_fields=["amount_paid"])

    assert list(plans.due_instalments(plan.instalments.first().due_date)) == []


@pytest.mark.django_db
def test_the_charger_settles_rather_than_charging_a_balance_already_paid(
    owing_on_a_plan, monkeypatch
):
    booking, plan = owing_on_a_plan
    instalment = plan.instalments.first()
    booking.amount_paid = booking.total_amount
    booking.save(update_fields=["amount_paid"])
    instalment.refresh_from_db()

    def _never(**kwargs):
        raise AssertionError("Stripe must not be asked to charge money already paid")

    monkeypatch.setattr("apps.payments.plans.gateway.charge_saved_card", _never)

    assert plans.charge_instalment(instalment) is False
    plan.refresh_from_db()
    assert plan.status == PaymentPlan.Status.COMPLETED
