from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bookings.models import Booking, Guest, Payment, Traveller, make_reference
from apps.catalog.models import Departure, PriceOption


def test_reference_avoids_characters_that_misread_aloud():
    reference = make_reference()
    assert len(reference) == 8
    assert not set(reference) & set("BIOS0158")


@pytest.mark.django_db
def test_every_booking_gets_a_unique_reference(make_booking):
    first, second = make_booking(), make_booking()
    assert first.reference and second.reference
    assert first.reference != second.reference


@pytest.mark.django_db
def test_total_is_recalculated_from_the_travellers_not_the_browser(make_booking, departure):
    booking = make_booking(travellers=2)
    booking.total_amount = Decimal("1.00")  # as if a form had been tampered with
    assert booking.recalculate_total() == Decimal("298.00")
    assert booking.total_amount == Decimal("298.00")


@pytest.mark.django_db
def test_balance_tracks_what_is_still_owed(make_booking):
    booking = make_booking(travellers=2)
    booking.recalculate_total()
    booking.amount_paid = Decimal("100.00")
    assert booking.balance == Decimal("198.00")
    assert booking.is_paid_in_full is False


@pytest.mark.django_db
def test_guest_emails_are_stored_lowercase(guest):
    assert guest.email == "dana@example.com"


@pytest.mark.django_db
def test_guest_emails_are_unique_regardless_of_case(guest):
    with pytest.raises(IntegrityError), transaction.atomic():
        Guest.objects.create(full_name="Someone Else", email="DANA@example.com")


@pytest.mark.django_db
def test_a_traveller_cannot_take_a_price_from_another_departure(make_booking, trip):
    today = timezone.localdate()
    other = Departure.objects.create(
        trip=trip, start_date=today, end_date=today, capacity=10, status=Departure.Status.OPEN
    )
    other_price = PriceOption.objects.create(
        departure=other, label="Adult", amount=Decimal("10.00")
    )
    booking = make_booking()
    traveller = Traveller(booking=booking, full_name="Ari", price_option=other_price)
    with pytest.raises(ValidationError) as exc:
        traveller.full_clean()
    assert "price_option" in exc.value.message_dict


@pytest.mark.django_db
def test_payment_stores_only_the_last_four_digits(make_booking):
    booking = make_booking()
    payment = Payment(
        booking=booking, amount=Decimal("149.00"), kind=Payment.Kind.FULL, card_last4="4242"
    )
    payment.full_clean()  # valid
    payment.card_last4 = "4242424242424242"
    with pytest.raises(ValidationError) as exc:
        payment.full_clean()
    assert "card_last4" in exc.value.message_dict


@pytest.mark.django_db
def test_outstanding_lists_confirmed_bookings_with_a_balance(make_booking):
    booking = make_booking(status=Booking.Status.CONFIRMED)
    booking.total_amount = Decimal("149.00")
    booking.save()
    assert booking in Booking.objects.outstanding()
    booking.amount_paid = Decimal("149.00")
    booking.save()
    assert booking not in Booking.objects.outstanding()
