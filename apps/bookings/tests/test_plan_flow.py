"""Choosing a payment plan on the public booking form."""

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.bookings.models import Booking, PaymentPlan


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fake_plan_checkout(monkeypatch):
    calls = {}

    def _start(booking, *, success_url, cancel_url):
        calls["booking"] = booking
        return "https://checkout.stripe.test/session/deposit"

    monkeypatch.setattr("apps.bookings.views.checkout.start_plan_checkout", _start)
    return calls


def _form_data(price_option, pickup, **overrides):
    data = {
        "full_name": "Dana Reyes",
        "email": "dana@example.com",
        "phone": "555-0100",
        "address_line1": "12 Elm Street",
        "city": "Hartford",
        "postal_code": "06103",
        "t0-full_name": "Traveller 1",
        "t0-price_option": str(price_option.pk),
        "t0-pickup": str(pickup.pk),
        "t0-emergency_contact_name": "Sam Reyes",
        "t0-emergency_contact_phone": "555-0199",
    }
    data |= overrides
    return data


@pytest.mark.django_db
def test_the_plan_option_is_offered_when_the_departure_allows_it(
    client, departure, price_option, pickup
):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    body = client.get(url).content.decode()
    assert "payment_option" in body
    assert "deposit" in body.lower()


@pytest.mark.django_db
def test_choosing_a_plan_creates_the_schedule_and_goes_to_the_deposit_checkout(
    client, departure, price_option, pickup, fake_plan_checkout
):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    response = client.post(url, _form_data(price_option, pickup, payment_option="plan"))

    assert response.status_code == 302
    assert response.url == "https://checkout.stripe.test/session/deposit"
    booking = Booking.objects.get()
    plan = PaymentPlan.objects.get(booking=booking)
    assert plan.deposit_amount == departure.deposit_amount
    total_scheduled = sum(i.amount for i in plan.instalments.all())
    assert plan.deposit_amount + total_scheduled == booking.total_amount


@pytest.mark.django_db
def test_the_plan_option_is_hidden_and_ignored_when_not_eligible(
    client, departure, price_option, pickup, monkeypatch
):
    departure.final_payment_due_date = None  # no plan possible
    departure.save()

    def _full(booking, *, success_url, cancel_url):
        return "https://checkout.stripe.test/session/full"

    monkeypatch.setattr("apps.bookings.views.checkout.start_checkout", _full)

    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    body = client.get(url).content.decode()
    assert "Pay a deposit now" not in body

    # Even if a plan value is posted, no plan is created.
    response = client.post(url, _form_data(price_option, pickup, payment_option="plan"))
    assert response.status_code == 302
    assert not PaymentPlan.objects.exists()
