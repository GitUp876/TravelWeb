"""Creating a booking and holding its seats.

The seat check and the seat claim happen inside one transaction with the
departure row locked, so two guests racing for the last seat cannot both win.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Departure

from .models import Booking, Guest, Payment, Traveller


class SeatsUnavailable(RuntimeError):
    """Not enough seats left for the party that asked."""


class DepartureNotBookable(RuntimeError):
    """The departure is closed, sold out, cancelled or in the past."""


@transaction.atomic
def create_pending_booking(
    *,
    departure: Departure,
    guest_data: dict,
    travellers_data: list[dict],
    source: str = Booking.Source.WEB,
    created_by=None,
    for_staff: bool = False,
) -> Booking:
    """Holds seats for a party and returns the pending booking.

    The total is computed here from each traveller's chosen price option. No
    amount from the request is used, or even read.

    ``for_staff`` is what a phone booking sets: it reaches the seats held back
    from the website and the dates closed to it, which is the whole point of
    holding seats back. It never reaches a cancelled departure or a date that
    has already gone.
    """
    if not travellers_data:
        raise SeatsUnavailable("a booking needs at least one traveller")

    locked = Departure.objects.select_for_update().get(pk=departure.pk)
    # Seats are checked first so a sold-out date says "sold out" rather than
    # the vaguer "closed for booking" that every other failure gets.
    seats_left = locked.seats_available_to_staff if for_staff else locked.seats_available
    if seats_left < len(travellers_data):
        raise SeatsUnavailable(f"{seats_left} seat(s) left, {len(travellers_data)} asked for")
    if not (locked.is_bookable_by_staff if for_staff else locked.is_bookable):
        raise DepartureNotBookable(str(locked))

    guest = _guest_for(guest_data)

    hold = (
        timedelta(days=settings.STAFF_HOLD_DAYS)
        if for_staff
        else timedelta(minutes=settings.SEAT_HOLD_MINUTES)
    )
    booking = Booking.objects.create(
        departure=locked,
        guest=guest,
        status=Booking.Status.PENDING,
        source=source,
        created_by=created_by,
        hold_expires_at=timezone.now() + hold,
    )

    total = Decimal("0.00")
    for row in travellers_data:
        option = row["price_option"]
        if option.departure_id != locked.pk:
            raise DepartureNotBookable("price option belongs to another departure")
        pickup = row.get("pickup")
        if pickup is not None and pickup.departure_id != locked.pk:
            raise DepartureNotBookable("boarding point belongs to another departure")
        Traveller.objects.create(
            booking=booking,
            full_name=row["full_name"],
            price_option=option,
            pickup=pickup,
            dietary_notes=row.get("dietary_notes", ""),
            mobility_notes=row.get("mobility_notes", ""),
            emergency_contact_name=row.get("emergency_contact_name", ""),
            emergency_contact_phone=row.get("emergency_contact_phone", ""),
        )
        total += option.amount

    booking.total_amount = total
    booking.save(update_fields=["total_amount"])
    return booking


def _guest_for(guest_data: dict) -> Guest:
    """One guest row per email address.

    Details already on file are left alone: anyone can start a booking with
    somebody else's address, and that must not let them rewrite what staff see
    about that person. Blank fields are filled in, which is a plain improvement.
    """
    email = guest_data["email"].strip().lower()
    guest, created = Guest.objects.get_or_create(
        email=email, defaults={**guest_data, "email": email}
    )
    if created:
        return guest

    updates = [
        field
        for field, value in guest_data.items()
        if field not in {"email", "marketing_consent"} and value and not getattr(guest, field, "")
    ]
    if guest_data.get("marketing_consent") and not guest.marketing_consent:
        # Consent is only ever turned on by the person themselves, and only up.
        updates.append("marketing_consent")
        guest.marketing_consent = True
    for field in updates:
        if field != "marketing_consent":
            setattr(guest, field, guest_data[field])
    if updates:
        guest.save(update_fields=updates)
    return guest


OFFLINE_METHODS = (Payment.Method.CASH, Payment.Method.CHEQUE, Payment.Method.TRANSFER)


class InvalidPayment(ValueError):
    """The payment staff described cannot be recorded as given."""


@transaction.atomic
def record_offline_payment(
    *,
    booking: Booking,
    amount: Decimal,
    method: str,
    taken_by,
    reference: str = "",
) -> Payment:
    """Records money taken away from Stripe: cash, a cheque, a bank transfer.

    Never a card number. This application has no field for one and must not grow
    one; a guest paying by card pays on Stripe's own page, which is what keeps
    the site in PCI SAQ A.

    Taking money settles the booking: a pending one is confirmed here, because a
    guest who has handed over a cheque should not lose their seat to a hold
    expiring.
    """
    if amount is None or amount <= 0:
        raise InvalidPayment("an offline payment must be a positive amount")
    if method not in OFFLINE_METHODS:
        raise InvalidPayment(f"{method} is not a payment staff can record by hand")

    locked = Booking.objects.select_for_update().get(pk=booking.pk)
    if locked.status in (Booking.Status.CANCELLED, Booking.Status.EXPIRED):
        raise InvalidPayment("that booking is no longer live")

    payment = Payment.objects.create(
        booking=locked,
        amount=amount,
        kind=_offline_kind(locked, amount),
        method=method,
        offline_reference=reference,
        taken_by=taken_by,
    )

    locked.amount_paid = locked.amount_paid + amount
    fields = ["amount_paid"]
    if locked.status == Booking.Status.PENDING:
        locked.status = Booking.Status.CONFIRMED
        locked.confirmed_at = locked.confirmed_at or timezone.now()
        locked.hold_expires_at = None
        fields += ["status", "confirmed_at", "hold_expires_at"]
    locked.save(update_fields=fields)

    if locked.balance <= 0:
        # Money taken by hand can clear the balance just as a card can, and the
        # instalments waiting on it must stand down either way.
        from apps.payments import plans

        plans.settle_plan(locked)
    return payment


def _offline_kind(booking: Booking, amount: Decimal) -> str:
    """What to call the payment, from where it sits against the total."""
    if booking.amount_paid > 0:
        return Payment.Kind.BALANCE
    if amount >= booking.total_amount:
        return Payment.Kind.FULL
    return Payment.Kind.DEPOSIT


def release_expired_holds() -> int:
    """Marks pending bookings whose hold has run out, freeing their seats."""
    expired = Booking.objects.filter(
        status=Booking.Status.PENDING, hold_expires_at__lt=timezone.now()
    )
    return expired.update(status=Booking.Status.EXPIRED, hold_expires_at=None)
