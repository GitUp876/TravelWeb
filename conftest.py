"""Shared fixtures. Deliberately plain: builders, not a factory library."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.bookings.models import Booking, Guest, Traveller
from apps.catalog.models import (
    Departure,
    DeparturePickup,
    PickupPoint,
    PriceOption,
    Trip,
    TripCategory,
)


@pytest.fixture
def trip(db) -> Trip:
    return Trip.objects.create(
        title="Newport Mansions",
        category=TripCategory.DAY_TRIP,
        summary="A day in Newport.",
        description="Two mansions and a harbour cruise.",
        is_published=True,
    )


@pytest.fixture
def departure(trip) -> Departure:
    today = timezone.localdate()
    return Departure.objects.create(
        trip=trip,
        start_date=today + timedelta(days=90),
        end_date=today + timedelta(days=90),
        status=Departure.Status.OPEN,
        capacity=10,
        seats_held_back=2,
        deposit_amount=Decimal("25.00"),
        final_payment_due_date=today + timedelta(days=60),
        plan_cutoff_days=30,
    )


@pytest.fixture
def price_option(departure) -> PriceOption:
    return PriceOption.objects.create(departure=departure, label="Adult", amount=Decimal("149.00"))


@pytest.fixture
def pickup(departure) -> DeparturePickup:
    point = PickupPoint.objects.create(
        name="Manchester Park & Ride", address="1 Tolland Turnpike", city="Manchester", region="CT"
    )
    return DeparturePickup.objects.create(
        departure=departure, pickup_point=point, boarding_time="07:00"
    )


@pytest.fixture
def guest(db) -> Guest:
    return Guest.objects.create(full_name="Dana Reyes", email="Dana@Example.com")


@pytest.fixture
def make_booking(departure, guest, price_option):
    def _make(status=Booking.Status.CONFIRMED, travellers=1, hold_minutes=None):
        booking = Booking.objects.create(
            departure=departure,
            guest=guest,
            status=status,
            hold_expires_at=(
                timezone.now() + timedelta(minutes=hold_minutes)
                if hold_minutes is not None
                else None
            ),
        )
        for index in range(travellers):
            Traveller.objects.create(
                booking=booking, full_name=f"Traveller {index}", price_option=price_option
            )
        return booking

    return _make
