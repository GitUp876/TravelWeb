"""The passenger list for one departure.

The manifest is the sheet the tour director carries, so it is grouped the way a
coach actually loads: by boarding point, in boarding order, with the travellers
who have no pickup at the end.

Dietary and mobility notes appear here and only here. They are health-adjacent,
so they are kept out of the audit trail and out of the CSV: a file that gets
emailed to a coach company is the last place they belong.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import StringIO

from apps.catalog.models import Departure, DeparturePickup

from .models import Booking, Traveller

# Columns for the CSV. Deliberately no dietary or mobility notes: this file
# leaves the building.
CSV_COLUMNS = [
    "Boarding point",
    "Boarding time",
    "Traveller",
    "Booking",
    "Party lead",
    "Ticket",
    "Room",
    "Emergency contact",
    "Emergency phone",
]


@dataclass
class ManifestGroup:
    """Everyone boarding at one stop."""

    pickup: DeparturePickup | None
    travellers: list[Traveller] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.pickup is None:
            return "No boarding point set"
        return f"{self.pickup.pickup_point.name}, {self.pickup.pickup_point.city}"

    @property
    def boarding_time(self):
        return self.pickup.boarding_time if self.pickup else None

    @property
    def headcount(self) -> int:
        return len(self.travellers)


def manifest_travellers(departure: Departure):
    """Every traveller holding a seat on this departure.

    Confirmed bookings, and pending ones whose hold has not run out: both are
    occupying a seat, and a tour director needs to know about anyone who might
    turn up at the coach.
    """
    return (
        Traveller.objects.filter(
            booking__departure=departure,
            booking__in=Booking.objects.holding_seats(),
        )
        .select_related(
            "booking",
            "booking__guest",
            "price_option",
            "pickup__pickup_point",
        )
        .order_by("pickup__display_order", "pickup__boarding_time", "full_name")
    )


def manifest(departure: Departure) -> dict:
    """The whole sheet: groups in boarding order, plus the counts."""
    travellers = list(manifest_travellers(departure))

    groups: dict[int | None, ManifestGroup] = {}
    for traveller in travellers:
        key = traveller.pickup_id
        if key not in groups:
            groups[key] = ManifestGroup(pickup=traveller.pickup)
        groups[key].travellers.append(traveller)

    # Stops in boarding order; anyone without a stop trails the list.
    ordered = sorted(
        (group for group in groups.values() if group.pickup is not None),
        key=lambda g: (g.pickup.display_order, g.pickup.boarding_time),
    )
    ordered += [group for group in groups.values() if group.pickup is None]

    return {
        "departure": departure,
        "groups": ordered,
        "headcount": len(travellers),
        "capacity": departure.capacity,
        "needs_attention": [t for t in travellers if t.dietary_notes or t.mobility_notes],
    }


def manifest_csv(departure: Departure) -> str:
    """The manifest as a CSV, with the health-adjacent notes left out."""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for group in manifest(departure)["groups"]:
        for traveller in group.travellers:
            writer.writerow(
                [
                    group.label,
                    group.boarding_time.strftime("%H:%M") if group.boarding_time else "",
                    traveller.full_name,
                    traveller.booking.reference,
                    traveller.booking.guest.full_name,
                    traveller.price_option.label,
                    traveller.room_assignment,
                    traveller.emergency_contact_name,
                    traveller.emergency_contact_phone,
                ]
            )
    return buffer.getvalue()
