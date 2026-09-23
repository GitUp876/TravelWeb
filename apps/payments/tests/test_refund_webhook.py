"""Refunds made in the Stripe dashboard, mirrored through the real webhook.

The signature check is faked; the matching, the arithmetic and what it does to
the booking run for real.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.bookings.models import Booking, Payment
from apps.payments import services
from apps.payments.models import StripeEvent

WEBHOOK_URL = "/stripe/webhook/"


def refunded_charge(intent_id="pi_paid", *, amount_refunded):
    return SimpleNamespace(
        id="ch_1", payment_intent=intent_id, amount=14900, amount_refunded=amount_refunded
    )


def fake_event(event_id, obj):
    return SimpleNamespace(id=event_id, type="charge.refunded", data=SimpleNamespace(object=obj))


@pytest.fixture
def deliver(client, monkeypatch):
    """Posts one event to the webhook as though Stripe had signed it."""

    def _deliver(event):
        monkeypatch.setattr(
            "apps.payments.views.gateway.construct_event", lambda payload, signature: event
        )
        return client.post(
            WEBHOOK_URL, data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig"
        )

    return _deliver


@pytest.fixture
def paid_booking(make_booking):
    booking = make_booking(status=Booking.Status.CONFIRMED)
    booking.total_amount = Decimal("149.00")
    booking.amount_paid = Decimal("149.00")
    booking.save()
    Payment.objects.create(
        booking=booking,
        amount=Decimal("149.00"),
        kind=Payment.Kind.FULL,
        stripe_payment_intent_id="pi_paid",
        card_brand="visa",
        card_last4="4242",
    )
    return booking


def _refunds(booking):
    return list(booking.payments.filter(kind=Payment.Kind.REFUND).order_by("pk"))


@pytest.mark.django_db
def test_a_full_refund_is_recorded_and_pays_the_money_back(deliver, paid_booking):
    response = deliver(fake_event("evt_r1", refunded_charge(amount_refunded=14900)))
    assert response.status_code == 200

    paid_booking.refresh_from_db()
    assert paid_booking.amount_paid == Decimal("0.00")
    [refund] = _refunds(paid_booking)
    assert refund.amount == Decimal("-149.00")
    assert refund.method == Payment.Method.CARD
    assert (refund.card_brand, refund.card_last4) == ("visa", "4242")
    assert StripeEvent.objects.get(event_id="evt_r1").status == StripeEvent.Status.PROCESSED


@pytest.mark.django_db
def test_two_partial_refunds_record_only_what_is_new(deliver, paid_booking):
    deliver(fake_event("evt_r1", refunded_charge(amount_refunded=4000)))
    deliver(fake_event("evt_r2", refunded_charge(amount_refunded=10000)))

    paid_booking.refresh_from_db()
    assert [r.amount for r in _refunds(paid_booking)] == [Decimal("-40.00"), Decimal("-60.00")]
    assert paid_booking.amount_paid == Decimal("49.00")


@pytest.mark.django_db
def test_a_redelivered_event_changes_nothing(deliver, paid_booking):
    event = fake_event("evt_r1", refunded_charge(amount_refunded=4000))
    deliver(event)
    response = deliver(event)
    assert response.json() == {"status": "duplicate"}
    assert len(_refunds(paid_booking)) == 1


@pytest.mark.django_db
def test_events_arriving_out_of_order_never_count_a_dollar_twice(paid_booking):
    # The later, larger total lands first; the earlier one then adds nothing.
    services.record_refund(refunded_charge(amount_refunded=10000))
    services.record_refund(refunded_charge(amount_refunded=4000))
    paid_booking.refresh_from_db()
    assert [r.amount for r in _refunds(paid_booking)] == [Decimal("-100.00")]
    assert paid_booking.amount_paid == Decimal("49.00")


@pytest.mark.django_db
def test_a_refund_for_a_payment_we_never_took_is_logged_not_retried(deliver, paid_booking):
    response = deliver(fake_event("evt_r1", refunded_charge("pi_elsewhere", amount_refunded=100)))
    assert response.json() == {"status": "unknown-booking"}
    assert StripeEvent.objects.get(event_id="evt_r1").status == StripeEvent.Status.FAILED
    assert _refunds(paid_booking) == []


@pytest.mark.django_db
def test_a_refund_on_a_cancelled_booking_clears_the_money_held(deliver, paid_booking):
    paid_booking.status = Booking.Status.CANCELLED
    paid_booking.save()
    deliver(fake_event("evt_r1", refunded_charge(amount_refunded=14900)))
    paid_booking.refresh_from_db()
    assert paid_booking.status == Booking.Status.CANCELLED
    assert paid_booking.amount_paid == Decimal("0.00")


@pytest.mark.django_db
def test_the_booking_status_is_left_to_staff(deliver, paid_booking):
    deliver(fake_event("evt_r1", refunded_charge(amount_refunded=4000)))
    paid_booking.refresh_from_db()
    assert paid_booking.status == Booking.Status.CONFIRMED
    assert paid_booking.balance == Decimal("40.00")


@pytest.mark.django_db
def test_offline_money_on_the_same_booking_is_never_driven_negative(paid_booking):
    # Staff recorded the booking as paid by cheque and then zeroed it by hand;
    # the card refund must still be recorded without breaking the constraint.
    Booking.objects.filter(pk=paid_booking.pk).update(amount_paid=Decimal("20.00"))
    services.record_refund(refunded_charge(amount_refunded=14900))
    paid_booking.refresh_from_db()
    assert paid_booking.amount_paid == Decimal("0.00")
    assert _refunds(paid_booking)[0].amount == Decimal("-149.00")
