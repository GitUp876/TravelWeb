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


# Shorter demo trips so every category has something on sale.
MORE_TRIPS = [
    {
        "title": "Broadway Matinee: The Great Gatsby",
        "category": TripCategory.THEATRE,
        "summary": "Orchestra seats for the Saturday matinee, with time for lunch in Midtown.",
        "description": "Door-to-door coach travel to Manhattan, free time for lunch near "
        "Times Square and orchestra seats for the 2pm performance.",
        "inclusions": "Motorcoach travel\nOrchestra seat\nTour escort",
        "days_out": 30,
        "length": 1,
        "prices": [("Adult", "189.00")],
    },
    {
        "title": "Big Band Luncheon at the Aqua Turf",
        "category": TripCategory.LUNCHEON,
        "summary": "A three-course lunch and a live big band show in Plantsville.",
        "description": "Swing the afternoon away with a three-course lunch and a live show "
        "by a sixteen-piece big band.",
        "inclusions": "Motorcoach travel\nThree-course lunch\nLive show",
        "meals": 1,
        "days_out": 21,
        "length": 1,
        "prices": [("Adult", "119.00")],
    },
    {
        "title": "Bermuda Cruise from Boston",
        "category": TripCategory.CRUISE,
        "summary": "Seven nights to King's Wharf, with coach transfers to and from the port.",
        "description": "Sail from Boston to Bermuda's pink-sand beaches with three full days "
        "docked at King's Wharf. Coach transfers and a host who travels with the group.",
        "inclusions": "Coach transfers to the port\nSeven nights' cruise\nAll meals on board",
        "exclusions": "Gratuities\nShore excursions",
        "meals": 21,
        "days_out": 150,
        "length": 8,
        "prices": [("Inside cabin", "1695.00"), ("Balcony cabin", "2295.00")],
        "deposit": "250.00",
    },
    {
        "title": "Ireland's Emerald Coast",
        "category": TripCategory.FLY,
        "summary": "Ten days from Dublin to Galway and the Ring of Kerry, flights included.",
        "description": "Fly overnight to Dublin, then travel the west coast by coach with a "
        "local guide: the Cliffs of Moher, Galway, Killarney and the Ring of Kerry.",
        "inclusions": "Return flights from Boston\nNine nights' hotels\nDaily breakfast\n"
        "Five dinners\nLocal guide throughout",
        "exclusions": "Travel insurance\nLunches",
        "meals": 14,
        "days_out": 220,
        "length": 10,
        "prices": [("Double occupancy", "3895.00"), ("Single occupancy", "4695.00")],
        "deposit": "500.00",
    },
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
        for spec in MORE_TRIPS:
            self._simple_trip(points, today, **spec)
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

    def _simple_trip(self, points, today, **spec) -> None:
        trip, _ = Trip.objects.get_or_create(
            title=spec["title"],
            defaults={
                "category": spec["category"],
                "summary": spec["summary"],
                "description": spec["description"],
                "inclusions": spec.get("inclusions", ""),
                "exclusions": spec.get("exclusions", "Gratuities"),
                "meals_included": spec.get("meals", 0),
                "is_published": True,
            },
        )
        start = today + timedelta(days=spec["days_out"])
        deposit = Decimal(spec.get("deposit", "0.00"))
        departure, created = Departure.objects.get_or_create(
            trip=trip,
            start_date=start,
            defaults={
                "end_date": start + timedelta(days=spec["length"] - 1),
                "status": Departure.Status.OPEN,
                "capacity": 48,
                "deposit_amount": deposit,
                "final_payment_due_date": (start - timedelta(days=45)) if deposit else None,
                "cancellation_policy": "Full refund up to 30 days before departure.",
            },
        )
        if created:
            for order, (label, amount) in enumerate(spec["prices"]):
                PriceOption.objects.create(
                    departure=departure, label=label, amount=Decimal(amount), display_order=order
                )
            for order, point in enumerate(points[:2]):
                DeparturePickup.objects.create(
                    departure=departure,
                    pickup_point=point,
                    boarding_time=time(7 + order, 0),
                    display_order=order,
                )
