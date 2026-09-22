"""Signed links are the guest's only credential.

A guest has no password and no account, so access to a booking is a signed,
expiring link sent to the address on the booking. The token carries the
booking reference and nothing else; it is signed with the site's secret key,
so it cannot be forged or edited, and it stops working on its own.
"""

from __future__ import annotations

from django.conf import settings
from django.core import signing

SALT = "bookings.manage-booking"


def make_token(reference: str) -> str:
    return signing.dumps({"reference": reference}, salt=SALT)


def read_token(token: str) -> str | None:
    """The booking reference inside a valid token, or None.

    Returns None for a tampered, malformed or expired token; the caller must
    treat all three the same way, so a guess reveals nothing.
    """
    max_age = settings.BOOKING_LINK_MAX_AGE_DAYS * 24 * 60 * 60
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    reference = payload.get("reference") if isinstance(payload, dict) else None
    return reference if isinstance(reference, str) and reference else None


def booking_url(reference: str) -> str:
    from django.urls import reverse

    path = reverse("bookings:detail", kwargs={"token": make_token(reference)})
    return f"{settings.SITE_BASE_URL}{path}"
