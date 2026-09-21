"""The public booking flow.

Deliberately JavaScript-free: the party-size selector is a GET form and the
payment page is Stripe's own. That keeps the content security policy at
'self' with no third-party origins.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.catalog.models import Departure
from apps.core.audit import client_ip
from apps.payments import checkout, gateway

from .emails import send_booking_link
from .forms import MAX_PARTY_SIZE, BookingLookupForm, LeadGuestForm, TravellerForm
from .models import Booking
from .services import DepartureNotBookable, SeatsUnavailable, create_pending_booking
from .tokens import read_token

logger = logging.getLogger(__name__)

LOOKUP_ATTEMPTS = 5
LOOKUP_WINDOW_SECONDS = 15 * 60


def _party_size(request: HttpRequest) -> int:
    try:
        requested = int(request.GET.get("party", 1))
    except (TypeError, ValueError):
        requested = 1
    return max(1, min(requested, MAX_PARTY_SIZE))


def _bookable_departure(pk: int) -> Departure:
    departure = get_object_or_404(
        Departure.objects.select_related("trip").prefetch_related(
            "price_options", "pickups__pickup_point"
        ),
        pk=pk,
    )
    if not departure.trip.is_published or departure.status == Departure.Status.DRAFT:
        raise Http404("No such departure")
    return departure


@require_http_methods(["GET", "POST"])
def book_departure(request: HttpRequest, pk: int) -> HttpResponse:
    departure = _bookable_departure(pk)
    party = _party_size(request)

    if not settings.PAYMENTS_ENABLED or not departure.is_bookable:
        return render(
            request,
            "bookings/unavailable.html",
            {"departure": departure, "payments_enabled": settings.PAYMENTS_ENABLED},
            status=409,
        )

    prefix_range = range(party)
    if request.method == "POST":
        guest_form = LeadGuestForm(request.POST)
        traveller_forms = [
            TravellerForm(request.POST, departure=departure, prefix=f"t{index}")
            for index in prefix_range
        ]
        if guest_form.is_valid() and all(form.is_valid() for form in traveller_forms):
            try:
                booking = create_pending_booking(
                    departure=departure,
                    guest_data=guest_form.cleaned_data,
                    travellers_data=[form.cleaned_data for form in traveller_forms],
                )
            except SeatsUnavailable:
                left = departure.seats_available
                messages.error(
                    request,
                    "This date has just sold out."
                    if left == 0
                    else f"Only {left} seat{'' if left == 1 else 's'} left on this date. "
                    "Please reduce the party size or choose another date.",
                )
            except DepartureNotBookable:
                messages.error(request, "This date has just closed for booking.")
            else:
                return _redirect_to_payment(request, booking)
    else:
        guest_form = LeadGuestForm()
        traveller_forms = [
            TravellerForm(departure=departure, prefix=f"t{index}") for index in prefix_range
        ]

    return render(
        request,
        "bookings/book.html",
        {
            "departure": departure,
            "trip": departure.trip,
            "party": party,
            "party_choices": range(1, min(MAX_PARTY_SIZE, max(departure.seats_available, 1)) + 1),
            "guest_form": guest_form,
            "traveller_forms": traveller_forms,
        },
    )


def _redirect_to_payment(request: HttpRequest, booking: Booking) -> HttpResponse:
    from .tokens import make_token

    success_url = settings.SITE_BASE_URL + reverse(
        "bookings:detail", kwargs={"token": make_token(booking.reference)}
    )
    cancel_url = settings.SITE_BASE_URL + reverse(
        "catalog:departure-detail", kwargs={"pk": booking.departure_id}
    )
    try:
        payment_url = checkout.start_checkout(
            booking, success_url=success_url, cancel_url=f"{cancel_url}?cancelled=1"
        )
    except gateway.PaymentConfigurationError:
        logger.error("Checkout attempted while Stripe is unconfigured")
        _abandon(booking)
        messages.error(request, "Online payment is unavailable right now. Please call us.")
        return redirect("catalog:departure-detail", pk=booking.departure_id)
    except Exception:
        logger.exception("Could not open a checkout session for booking %s", booking.reference)
        _abandon(booking)
        messages.error(request, "We could not reach the payment page. Please try again.")
        return redirect("catalog:departure-detail", pk=booking.departure_id)
    return redirect(payment_url)


def _abandon(booking: Booking) -> None:
    """Releases the hold when we never got the guest as far as paying."""
    with transaction.atomic():
        booking.status = Booking.Status.EXPIRED
        booking.hold_expires_at = None
        booking.save(update_fields=["status", "hold_expires_at"])


def booking_detail(request: HttpRequest, token: str) -> HttpResponse:
    """A guest's view of their own booking, reached by signed link.

    A bad, edited or expired token is a 404: there is nothing to tell apart.
    """
    reference = read_token(token)
    if reference is None:
        raise Http404("This link is not valid")
    booking = get_object_or_404(
        Booking.objects.select_related("departure__trip", "guest").prefetch_related(
            "travellers__price_option", "travellers__pickup__pickup_point", "payments"
        ),
        reference=reference,
    )
    return render(request, "bookings/detail.html", {"booking": booking})


@require_http_methods(["GET", "POST"])
def find_booking(request: HttpRequest) -> HttpResponse:
    """Emails the booking link to the address already on the booking.

    The answer is the same either way, so this cannot be used to find out
    whether an address or a reference exists.
    """
    sent = False
    form = BookingLookupForm(request.POST or None)
    if request.method == "POST":
        key = f"booking-lookup:{client_ip(request) or 'unknown'}"
        attempts = cache.get_or_set(key, 0, LOOKUP_WINDOW_SECONDS)
        if attempts >= LOOKUP_ATTEMPTS:
            messages.error(request, "Too many attempts. Please try again later.")
        elif form.is_valid():
            cache.set(key, attempts + 1, LOOKUP_WINDOW_SECONDS)
            booking = Booking.objects.filter(
                reference=form.cleaned_data["reference"],
                guest__email__iexact=form.cleaned_data["email"],
            ).first()
            if booking is not None:
                send_booking_link(booking)
            sent = True
    return render(request, "bookings/find.html", {"form": form, "sent": sent})
