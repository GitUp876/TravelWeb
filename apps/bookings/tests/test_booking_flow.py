"""The path a guest walks: choose a party, enter details, get sent to Stripe."""

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.bookings.models import Booking
from apps.catalog.models import Departure, PriceOption


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fake_checkout(monkeypatch):
    """Stands in for Stripe. Records what it was asked for."""
    calls = {}

    def _start(booking, *, success_url, cancel_url):
        calls["booking"] = booking
        calls["success_url"] = success_url
        calls["cancel_url"] = cancel_url
        return "https://checkout.stripe.test/session/abc123"

    monkeypatch.setattr("apps.bookings.views.checkout.start_checkout", _start)
    return calls


def _form_data(price_option, pickup, party=1, **overrides):
    data = {
        "full_name": "Dana Reyes",
        "email": "dana@example.com",
        "phone": "555-0100",
        "address_line1": "12 Elm Street",
        "city": "Hartford",
        "postal_code": "06103",
    }
    for index in range(party):
        data |= {
            f"t{index}-full_name": f"Traveller {index + 1}",
            f"t{index}-price_option": str(price_option.pk),
            f"t{index}-pickup": str(pickup.pk),
            f"t{index}-emergency_contact_name": "Sam Reyes",
            f"t{index}-emergency_contact_phone": "555-0199",
        }
    data |= overrides
    return data


@pytest.mark.django_db
def test_booking_page_renders_one_form_per_traveller(client, departure, price_option, pickup):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    body = client.get(url, {"party": 3}).content.decode()
    assert body.count("Traveller ") >= 3
    assert "t2-full_name" in body


@pytest.mark.django_db
def test_party_size_is_clamped_to_something_sane(client, departure, price_option, pickup):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    body = client.get(url, {"party": 999}).content.decode()
    assert "t10-full_name" not in body
    body = client.get(url, {"party": "not-a-number"}).content.decode()
    assert "t0-full_name" in body


@pytest.mark.django_db
def test_a_valid_booking_holds_seats_and_redirects_to_stripe(
    client, departure, price_option, pickup, fake_checkout
):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    response = client.post(f"{url}?party=2", _form_data(price_option, pickup, party=2))

    assert response.status_code == 302
    assert response["Location"].startswith("https://checkout.stripe.test/")

    booking = Booking.objects.get()
    assert booking.status == Booking.Status.PENDING
    assert booking.travellers.count() == 2
    assert booking.total_amount == Decimal("298.00")
    assert booking.hold_expires_at is not None
    assert departure.seats_taken == 2


@pytest.mark.django_db
def test_the_total_comes_from_our_prices_not_the_form(
    client, departure, price_option, pickup, fake_checkout
):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    data = _form_data(price_option, pickup) | {"total_amount": "1.00", "amount_paid": "999.00"}
    client.post(url, data)

    booking = Booking.objects.get()
    assert booking.total_amount == Decimal("149.00")
    assert booking.amount_paid == Decimal("0.00")


@pytest.mark.django_db
def test_a_price_from_another_departure_is_rejected(
    client, departure, price_option, pickup, trip, fake_checkout
):
    other = Departure.objects.create(
        trip=trip,
        start_date=departure.start_date,
        end_date=departure.end_date,
        status=Departure.Status.OPEN,
        capacity=10,
    )
    cheap = PriceOption.objects.create(departure=other, label="Adult", amount=Decimal("1.00"))
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    response = client.post(url, _form_data(cheap, pickup))

    assert response.status_code == 200  # redisplayed with an error
    assert not Booking.objects.exists()


@pytest.mark.django_db
def test_a_party_larger_than_the_seats_left_is_refused(
    client, departure, price_option, pickup, make_booking, fake_checkout
):
    make_booking(status=Booking.Status.CONFIRMED, travellers=7)  # 1 of 8 left
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    response = client.post(f"{url}?party=2", _form_data(price_option, pickup, party=2))

    assert response.status_code == 200
    assert "seat" in response.content.decode().lower()
    assert Booking.objects.filter(status=Booking.Status.PENDING).count() == 0


@pytest.mark.django_db
def test_a_sold_out_departure_cannot_be_booked(
    client, departure, price_option, pickup, make_booking
):
    make_booking(status=Booking.Status.CONFIRMED, travellers=8)
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    assert client.get(url).status_code == 409


@override_settings(PAYMENTS_ENABLED=False)
@pytest.mark.django_db
def test_booking_is_closed_when_stripe_is_not_configured(client, departure, price_option, pickup):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    response = client.get(url)
    assert response.status_code == 409
    assert "call us" in response.content.decode().lower()


@pytest.mark.django_db
def test_a_failed_checkout_releases_the_hold(client, departure, price_option, pickup, monkeypatch):
    def _explode(booking, *, success_url, cancel_url):
        raise RuntimeError("Stripe is down")

    monkeypatch.setattr("apps.bookings.views.checkout.start_checkout", _explode)
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    response = client.post(url, _form_data(price_option, pickup))

    assert response.status_code == 302
    booking = Booking.objects.get()
    assert booking.status == Booking.Status.EXPIRED
    assert departure.seats_taken == 0


@pytest.mark.django_db
def test_the_checkout_urls_point_back_at_us(client, departure, price_option, pickup, fake_checkout):
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    client.post(url, _form_data(price_option, pickup))

    assert fake_checkout["success_url"].startswith("http://testserver/booking/")
    assert fake_checkout["cancel_url"].endswith("?cancelled=1")


@pytest.mark.django_db
def test_the_departure_page_offers_booking_once_payments_are_on(client, departure, price_option):
    body = client.get(departure.get_absolute_url()).content.decode()
    assert reverse("bookings:book", kwargs={"pk": departure.pk}) in body
