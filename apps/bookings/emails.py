"""Transactional email.

Every message is plain text and carries only what the guest needs. Card
details are never included, because we never hold them.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import Booking
from .tokens import booking_url

logger = logging.getLogger(__name__)


def send_booking_confirmation(booking: Booking) -> None:
    context = {
        "booking": booking,
        "departure": booking.departure,
        "trip": booking.departure.trip,
        "travellers": booking.travellers.select_related("price_option", "pickup__pickup_point"),
        "manage_url": booking_url(booking.reference),
        "site_name": settings.SITE_NAME,
    }
    subject = f"Booking {booking.reference} confirmed — {booking.departure.trip.title}"
    _send(subject, "bookings/email/confirmation.txt", context, booking.guest.email)


def send_booking_link(booking: Booking) -> None:
    """Re-sends the manage-my-booking link to the address on the booking."""
    context = {
        "booking": booking,
        "manage_url": booking_url(booking.reference),
        "site_name": settings.SITE_NAME,
        "valid_days": settings.BOOKING_LINK_MAX_AGE_DAYS,
    }
    subject = f"Your booking {booking.reference}"
    _send(subject, "bookings/email/booking_link.txt", context, booking.guest.email)


def _send(subject: str, template: str, context: dict, recipient: str) -> None:
    body = render_to_string(template, context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
    except Exception:
        # A booking is not undone by an email failure; staff can resend.
        # The address itself is deliberately kept out of the log.
        logger.exception("Could not send %s for a booking", template)
