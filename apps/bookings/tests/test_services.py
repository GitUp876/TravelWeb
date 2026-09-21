from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.bookings.models import Booking, Guest
from apps.bookings.services import (
    DepartureNotBookable,
    SeatsUnavailable,
    create_pending_booking,
    release_expired_holds,
)
from apps.catalog.models import Departure


def _traveller(price_option, **extra):
    return {
        "full_name": "Dana Reyes",
        "price_option": price_option,
        "pickup": None,
        "emergency_contact_name": "Sam Reyes",
        "emergency_contact_phone": "555-0199",
    } | extra


@pytest.mark.django_db
def test_a_booking_totals_the_prices_of_its_travellers(departure, price_option):
    booking = create_pending_booking(
        departure=departure,
        guest_data={"full_name": "Dana", "email": "dana@example.com", "phone": "555-0100"},
        travellers_data=[_traveller(price_option), _traveller(price_option)],
    )
    assert booking.total_amount == Decimal("298.00")
    assert booking.status == Booking.Status.PENDING
    assert booking.hold_expires_at > timezone.now()


@pytest.mark.django_db
def test_booking_more_seats_than_are_left_fails(departure, price_option, make_booking):
    make_booking(status=Booking.Status.CONFIRMED, travellers=8)
    with pytest.raises(SeatsUnavailable):
        create_pending_booking(
            departure=departure,
            guest_data={"full_name": "Dana", "email": "dana@example.com", "phone": "1"},
            travellers_data=[_traveller(price_option)],
        )


@pytest.mark.django_db
def test_a_closed_departure_cannot_be_booked(departure, price_option):
    departure.status = Departure.Status.CLOSED
    departure.save()
    with pytest.raises(DepartureNotBookable):
        create_pending_booking(
            departure=departure,
            guest_data={"full_name": "Dana", "email": "dana@example.com", "phone": "1"},
            travellers_data=[_traveller(price_option)],
        )


@pytest.mark.django_db
def test_an_empty_party_is_refused(departure):
    with pytest.raises(SeatsUnavailable):
        create_pending_booking(
            departure=departure,
            guest_data={"full_name": "Dana", "email": "dana@example.com", "phone": "1"},
            travellers_data=[],
        )


@pytest.mark.django_db
def test_booking_again_reuses_the_guest_without_rewriting_their_details(departure, price_option):
    Guest.objects.create(full_name="Dana Reyes", email="dana@example.com", phone="555-0100")
    create_pending_booking(
        departure=departure,
        guest_data={
            "full_name": "Someone Else",
            "email": "DANA@example.com",
            "phone": "555-9999",
            "city": "Hartford",
        },
        travellers_data=[_traveller(price_option)],
    )
    guest = Guest.objects.get()
    # Details already on file win; blanks are filled in.
    assert guest.full_name == "Dana Reyes"
    assert guest.phone == "555-0100"
    assert guest.city == "Hartford"


@pytest.mark.django_db
def test_marketing_consent_can_only_be_turned_on_by_the_guest(departure, price_option):
    Guest.objects.create(full_name="Dana", email="dana@example.com", marketing_consent=True)
    create_pending_booking(
        departure=departure,
        guest_data={
            "full_name": "Dana",
            "email": "dana@example.com",
            "phone": "1",
            "marketing_consent": False,
        },
        travellers_data=[_traveller(price_option)],
    )
    assert Guest.objects.get().marketing_consent is True


@pytest.mark.django_db
def test_expired_holds_are_released(make_booking, departure):
    booking = make_booking(status=Booking.Status.PENDING, travellers=2, hold_minutes=20)
    assert departure.seats_taken == 2

    booking.hold_expires_at = timezone.now() - timedelta(minutes=1)
    booking.save()
    assert release_expired_holds() == 1

    booking.refresh_from_db()
    assert booking.status == Booking.Status.EXPIRED
    assert departure.seats_taken == 0


@pytest.mark.django_db
def test_a_live_hold_is_left_alone(make_booking):
    booking = make_booking(status=Booking.Status.PENDING, travellers=1, hold_minutes=20)
    assert release_expired_holds() == 0
    booking.refresh_from_db()
    assert booking.status == Booking.Status.PENDING
