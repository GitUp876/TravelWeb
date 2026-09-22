"""Guests have no password, so the signed link is the whole access control."""

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.bookings.tokens import make_token, read_token


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_a_token_round_trips():
    token = make_token("ABCD1234")
    assert read_token(token) == "ABCD1234"


def test_an_edited_token_is_refused():
    token = make_token("ABCD1234")
    assert read_token(token[:-2] + "xy") is None
    assert read_token("nonsense") is None
    assert read_token("") is None


@override_settings(BOOKING_LINK_MAX_AGE_DAYS=0)
def test_an_expired_token_stops_working():
    import time

    token = make_token("ABCD1234")
    time.sleep(1.1)
    assert read_token(token) is None


@pytest.mark.django_db
def test_a_guest_can_open_their_own_booking(client, make_booking):
    booking = make_booking()
    url = reverse("bookings:detail", kwargs={"token": make_token(booking.reference)})
    response = client.get(url)
    assert response.status_code == 200
    assert booking.reference in response.content.decode()


@pytest.mark.django_db
def test_a_bad_link_is_a_404_and_says_nothing(client, make_booking):
    make_booking()
    url = reverse("bookings:detail", kwargs={"token": "forged-token"})
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_a_booking_reference_alone_opens_nothing(client, make_booking):
    booking = make_booking()
    url = reverse("bookings:detail", kwargs={"token": booking.reference})
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_lookup_emails_the_link_when_the_details_match(client, make_booking):
    booking = make_booking()
    response = client.post(
        reverse("bookings:find"),
        {"reference": booking.reference.lower(), "email": booking.guest.email},
    )
    assert response.status_code == 200
    assert len(mail.outbox) == 1
    assert booking.reference in mail.outbox[0].subject
    assert "/booking/" in mail.outbox[0].body


@pytest.mark.django_db
def test_lookup_says_the_same_thing_when_nothing_matches(client, make_booking):
    booking = make_booking()
    matched = client.post(
        reverse("bookings:find"),
        {"reference": booking.reference, "email": booking.guest.email},
    ).content.decode()
    mail.outbox.clear()
    unmatched = client.post(
        reverse("bookings:find"),
        {"reference": "ZZZZZZZZ", "email": "stranger@example.com"},
    ).content.decode()

    assert "on its way" in unmatched
    assert unmatched == matched
    assert mail.outbox == []


@pytest.mark.django_db
def test_lookup_is_rate_limited(client, make_booking):
    booking = make_booking()
    url = reverse("bookings:find")
    payload = {"reference": "ZZZZZZZZ", "email": "stranger@example.com"}
    for _ in range(5):
        client.post(url, payload)

    blocked = client.post(
        url, {"reference": booking.reference, "email": booking.guest.email}
    ).content.decode()
    assert "Too many attempts" in blocked
    assert mail.outbox == []
