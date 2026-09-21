"""Fills an empty database with a few realistic trips so the site can be seen.

Development and staging only. It refuses to run when DEBUG is off unless
--force is passed, so it can never be run by accident against live data.
"""

from datetime import date, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import (
    Departure,
    DeparturePickup,
    ItineraryDay,
    PickupPoint,
    PriceOption,
    Trip,
    TripCategory,
)

PICKUPS = [
    ("Manchester Park & Ride", "1 Tolland Turnpike", "Manchester", "CT"),
    ("New Britain Transit Centre", "50 Main Street", "New Britain", "CT"),
    ("Waterbury Green", "20 East Main Street", "Waterbury", "CT"),
]


class Command(BaseCommand):
    help = "Creates demo trips, departures, pickup points and prices."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed a non-debug environment without --force")

        points = [
            PickupPoint.objects.get_or_create(
                name=name,
                defaults={"address": address, "city": city, "region": region},
            )[0]
            for name, address, city, region in PICKUPS
        ]

        today = timezone.localdate()
        self._day_trip(points, today)
        self._overnight(points, today)
        self.stdout.write(self.style.SUCCESS("Demo data ready. Visit / to see it."))

    def _day_trip(self, points: list[PickupPoint], today: date) -> None:
        trip, _ = Trip.objects.get_or_create(
            title="Newport Mansions and Harbour Cruise",
            defaults={
                "category": TripCategory.DAY_TRIP,
                "summary": ("Two Gilded Age mansions, lunch on the water and a narrated cruise."),
                "description": (
                    "A full day in Newport with an escorted tour of The Breakers and Marble "
                    "House, lunch overlooking the harbour, and an afternoon cruise past the "
                    "bridge and the naval station."
                ),
                "inclusions": "Motorcoach travel\nBoth mansion admissions\nLunch\nHarbour cruise",
                "exclusions": "Gratuities\nPersonal spending",
                "meals_included": 1,
                "is_published": True,
            },
        )
        departure, created = Departure.objects.get_or_create(
            trip=trip,
            start_date=today + timedelta(days=45),
            defaults={
                "end_date": today + timedelta(days=45),
                "status": Departure.Status.OPEN,
                "capacity": 52,
                "seats_held_back": 2,
                "deposit_amount": Decimal("25.00"),
                "final_payment_due_date": today + timedelta(days=31),
                "plan_cutoff_days": 30,
                "cancellation_policy": "Full refund up to 14 days before departure.",
            },
        )
        if created:
            PriceOption.objects.create(departure=departure, label="Adult", amount=Decimal("149.00"))
            PriceOption.objects.create(
                departure=departure,
                label="Child (under 12)",
                amount=Decimal("119.00"),
                display_order=1,
            )
            for order, (point, boarding) in enumerate(
                zip(points, [time(7, 0), time(7, 30), time(8, 0)], strict=True)
            ):
                DeparturePickup.objects.create(
                    departure=departure,
                    pickup_point=point,
                    boarding_time=boarding,
                    display_order=order,
                )

    def _overnight(self, points: list[PickupPoint], today: date) -> None:
        trip, _ = Trip.objects.get_or_create(
            title="Southern Charm: Charleston, Savannah and Jekyll Island",
            defaults={
                "category": TripCategory.OVERNIGHT,
                "summary": "Seven days through the Low Country, with nine meals and guided tours.",
                "description": (
                    "Seven days through Charleston, Savannah and Jekyll Island, staying in "
                    "first-class hotels with a tour director throughout."
                ),
                "inclusions": (
                    "Motorcoach travel\nSix nights' accommodation\nNine meals\nGuided tours"
                ),
                "exclusions": "Gratuities\nTravel insurance",
                "meals_included": 9,
                "is_published": True,
            },
        )
        for day_number, title in enumerate(
            [
                "Travel south",
                "Charleston",
                "Charleston",
                "Savannah",
                "Savannah",
                "Jekyll Island",
                "Homeward",
            ],
            start=1,
        ):
            ItineraryDay.objects.get_or_create(
                trip=trip, day_number=day_number, defaults={"title": title}
            )

        departure, created = Departure.objects.get_or_create(
            trip=trip,
            start_date=today + timedelta(days=200),
            defaults={
                "end_date": today + timedelta(days=206),
                "status": Departure.Status.OPEN,
                "capacity": 44,
                "seats_held_back": 4,
                "deposit_amount": Decimal("200.00"),
                "final_payment_due_date": today + timedelta(days=140),
                "plan_cutoff_days": 60,
                "cancellation_policy": "Deposit refundable up to 90 days before departure.",
            },
        )
        if created:
            PriceOption.objects.create(
                departure=departure, label="Double occupancy", amount=Decimal("2395.00")
            )
            PriceOption.objects.create(
                departure=departure,
                label="Single occupancy",
                amount=Decimal("3095.00"),
                display_order=1,
            )
            PriceOption.objects.create(
                departure=departure,
                label="Triple occupancy",
                amount=Decimal("2245.00"),
                display_order=2,
            )
            for order, (point, boarding) in enumerate(
                zip(points, [time(6, 30), time(7, 0), time(7, 45)], strict=True)
            ):
                DeparturePickup.objects.create(
                    departure=departure,
                    pickup_point=point,
                    boarding_time=boarding,
                    display_order=order,
                )
