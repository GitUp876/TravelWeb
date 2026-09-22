"""The webhook is public, so its signature check is the security boundary."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core import mail

from apps.bookings.models import Booking, Payment
from apps.payments import gateway
from apps.payments.models import StripeEvent

WEBHOOK_URL = "/stripe/webhook/"


def fake_event(event_id: str, event_type: str, obj: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(id=event_id, type=event_type, data=SimpleNamespace(object=obj))


def checkout_session(reference: str, *, amount_total: int = 29800) -> SimpleNamespace:
    return SimpleNamespace(
        client_reference_id=reference,
        payment_intent="pi_test_123",
        amount_total=amount_total,
        customer="cus_test_123",
    )


@pytest.fixture
def accept_signature(monkeypatch):
    """Makes construct_event return whatever the test hands it."""

    def _install(event):
        monkeypatch.setattr(
            "apps.payments.views.gateway.construct_event", lambda payload, signature: event
        )

    return _install


@pytest.mark.django_db
def test_a_request_without_a_signature_is_rejected(client):
    response = client.post(WEBHOOK_URL, data=b"{}", content_type="application/json")
    assert response.status_code == 400
    assert not StripeEvent.objects.exists()


@pytest.mark.django_db
def test_a_bad_signature_is_rejected(client, monkeypatch):
    def _reject(payload, signature):
        raise ValueError("bad signature")

    monkeypatch.setattr("apps.payments.views.gateway.construct_event", _reject)
    response = client.post(
        WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=x"
    )
    assert response.status_code == 400
    assert not StripeEvent.objects.exists()


@pytest.mark.django_db
def test_the_webhook_only_answers_post(client):
    assert client.get(WEBHOOK_URL).status_code == 405


@pytest.mark.django_db
def test_a_completed_checkout_confirms_the_booking(
    client, make_booking, accept_signature, django_capture_on_commit_callbacks
):
    booking = make_booking(status=Booking.Status.PENDING, travellers=2, hold_minutes=20)
    booking.total_amount = Decimal("298.00")
    booking.save()
    accept_signature(
        fake_event("evt_1", "checkout.session.completed", checkout_session(booking.reference))
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
        )
    assert response.status_code == 200

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.amount_paid == Decimal("298.00")
    assert booking.confirmed_at is not None
    assert booking.hold_expires_at is None

    payment = Payment.objects.get()
    assert payment.stripe_payment_intent_id == "pi_test_123"
    assert payment.card_last4 == ""  # we never receive or store a card number
    assert len(mail.outbox) == 1
    assert booking.reference in mail.outbox[0].subject


@pytest.mark.django_db
def test_a_replayed_event_does_not_charge_twice(
    client, make_booking, accept_signature, django_capture_on_commit_callbacks
):
    booking = make_booking(status=Booking.Status.PENDING, travellers=1, hold_minutes=20)
    event = fake_event("evt_dup", "checkout.session.completed", checkout_session(booking.reference))
    accept_signature(event)

    with django_capture_on_commit_callbacks(execute=True):
        first = client.post(
            WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
        )
        second = client.post(
            WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
        )

    assert first.json()["status"] == "ok"
    assert second.json()["status"] == "duplicate"
    assert Payment.objects.count() == 1
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_a_second_event_for_the_same_payment_is_ignored(client, make_booking, accept_signature):
    booking = make_booking(status=Booking.Status.PENDING, travellers=1, hold_minutes=20)
    session = checkout_session(booking.reference)

    accept_signature(fake_event("evt_a", "checkout.session.completed", session))
    client.post(WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="s")
    accept_signature(fake_event("evt_b", "checkout.session.completed", session))
    client.post(WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="s")

    assert Payment.objects.count() == 1


@pytest.mark.django_db
def test_an_unknown_event_type_is_recorded_and_ignored(client, accept_signature):
    accept_signature(fake_event("evt_x", "invoice.paid", SimpleNamespace()))
    response = client.post(
        WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
    )
    assert response.json()["status"] == "ignored"
    assert StripeEvent.objects.get().status == StripeEvent.Status.IGNORED


@pytest.mark.django_db
def test_an_event_for_a_booking_we_do_not_hold_is_flagged(client, accept_signature):
    accept_signature(
        fake_event("evt_ghost", "checkout.session.completed", checkout_session("NOSUCHREF"))
    )
    response = client.post(
        WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
    )
    # 200 on purpose: a retry cannot make the booking appear.
    assert response.status_code == 200
    assert StripeEvent.objects.get().status == StripeEvent.Status.FAILED


@pytest.mark.django_db
def test_an_expired_checkout_releases_the_seats(client, make_booking, accept_signature):
    booking = make_booking(status=Booking.Status.PENDING, travellers=3, hold_minutes=20)
    departure = booking.departure
    assert departure.seats_taken == 3

    accept_signature(
        fake_event("evt_exp", "checkout.session.expired", checkout_session(booking.reference))
    )
    client.post(WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="s")

    booking.refresh_from_db()
    assert booking.status == Booking.Status.EXPIRED
    assert departure.seats_taken == 0


def test_signature_errors_name_the_stripe_exception():
    errors = gateway.signature_errors()
    assert ValueError in errors
    assert any(error.__name__ == "SignatureVerificationError" for error in errors)
