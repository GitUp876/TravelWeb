from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking
from apps.catalog.models import Departure, Trip, TripCategory


def test_seats_available_subtracts_held_back_seats(departure):
    assert departure.capacity == 10
    assert departure.seats_for_sale == 8
    assert departure.seats_available == 8


def test_confirmed_bookings_take_seats(departure, make_booking):
    make_booking(status=Booking.Status.CONFIRMED, travellers=3)
    assert departure.seats_taken == 3
    assert departure.seats_available == 5


def test_pending_booking_holds_seats_until_the_hold_expires(departure, make_booking):
    make_booking(status=Booking.Status.PENDING, travellers=2, hold_minutes=20)
    assert departure.seats_taken == 2


def test_expired_hold_releases_its_seats(departure, make_booking):
    make_booking(status=Booking.Status.PENDING, travellers=2, hold_minutes=-1)
    assert departure.seats_taken == 0
    assert departure.seats_available == 8


def test_cancelled_booking_frees_its_seats(departure, make_booking):
    make_booking(status=Booking.Status.CANCELLED, travellers=4)
    assert departure.seats_taken == 0


def test_departure_is_not_bookable_once_sold_out(departure, make_booking):
    make_booking(status=Booking.Status.CONFIRMED, travellers=8)
    assert departure.is_sold_out
    assert departure.is_bookable is False


def test_departure_is_not_bookable_before_the_window_opens(departure):
    departure.booking_opens_at = timezone.now() + timedelta(days=1)
    departure.save()
    assert departure.is_bookable is False


def test_departure_is_not_bookable_after_the_window_closes(departure):
    departure.booking_closes_at = timezone.now() - timedelta(minutes=1)
    departure.save()
    assert departure.is_bookable is False


def test_unpublished_trip_hides_its_departures(departure):
    departure.trip.is_published = False
    departure.trip.save()
    assert departure.is_bookable is False
    assert departure not in Departure.objects.bookable()


def test_bookable_queryset_excludes_draft_and_past(trip, departure):
    today = timezone.localdate()
    Departure.objects.create(
        trip=trip,
        start_date=today + timedelta(days=5),
        end_date=today + timedelta(days=5),
        status=Departure.Status.DRAFT,
        capacity=10,
    )
    Departure.objects.create(
        trip=trip,
        start_date=today - timedelta(days=5),
        end_date=today - timedelta(days=5),
        status=Departure.Status.OPEN,
        capacity=10,
    )
    assert list(Departure.objects.bookable()) == [departure]


def test_payment_plan_offered_only_far_enough_out(departure):
    assert departure.payment_plan_available is True
    departure.plan_cutoff_days = 120  # departure is 90 days away
    assert departure.payment_plan_available is False


def test_payment_plan_needs_a_deposit_and_a_due_date(departure):
    departure.deposit_amount = Decimal("0.00")
    assert departure.payment_plan_available is False
    departure.deposit_amount = Decimal("25.00")
    departure.final_payment_due_date = None
    assert departure.payment_plan_available is False


def test_final_payment_cannot_fall_after_departure(departure):
    departure.final_payment_due_date = departure.start_date + timedelta(days=1)
    with pytest.raises(ValidationError) as exc:
        departure.full_clean()
    assert "final_payment_due_date" in exc.value.message_dict


def test_departure_cannot_end_before_it_starts(departure):
    departure.end_date = departure.start_date - timedelta(days=1)
    with pytest.raises(ValidationError) as exc:
        departure.full_clean()
    assert "end_date" in exc.value.message_dict


def test_holding_back_every_seat_is_rejected(departure):
    departure.seats_held_back = departure.capacity
    with pytest.raises(ValidationError) as exc:
        departure.full_clean()
    assert "seats_held_back" in exc.value.message_dict


def test_duration_counts_both_end_days(trip):
    today = timezone.localdate()
    departure = Departure.objects.create(
        trip=trip,
        start_date=today + timedelta(days=10),
        end_date=today + timedelta(days=12),
        status=Departure.Status.OPEN,
        capacity=20,
    )
    assert departure.duration_days == 3


def test_slugs_stay_unique(db):
    first = Trip.objects.create(
        title="Cape Cod", category=TripCategory.DAY_TRIP, summary="s", description="d"
    )
    second = Trip.objects.create(
        title="Cape Cod", category=TripCategory.DAY_TRIP, summary="s", description="d"
    )
    assert first.slug == "cape-cod"
    assert second.slug == "cape-cod-2"
