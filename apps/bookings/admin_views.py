"""Staff report pages that live inside the admin.

Reached only through the admin site's own wrapper, so the MFA requirement on
every admin page applies here too: a password alone never opens this. The
permission check is explicit on top of that, because the report shows money.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from apps.core.audit import record
from apps.core.models import AuditEvent

from . import reports
from .forms import (
    LeadGuestForm,
    OfflinePaymentForm,
    StaffDepartureChoiceForm,
    TravellerForm,
)
from .models import Booking
from .services import (
    DepartureNotBookable,
    InvalidPayment,
    SeatsUnavailable,
    create_pending_booking,
    record_offline_payment,
)

VIEW_PERMISSION = "bookings.view_scheduledpayment"
BOOK_PERMISSION = "bookings.add_booking"


def payments_due(request: HttpRequest) -> TemplateResponse:
    """Everything owed: overdue instalments, what is coming, and loose balances."""
    if not request.user.has_perm(VIEW_PERMISSION):
        raise PermissionDenied

    horizon = reports.clamp_horizon(request.GET.get("days"))
    context = {
        **admin.site.each_context(request),
        "title": "Payments due",
        **reports.payments_due(horizon_days=horizon),
    }
    return TemplateResponse(request, "admin/payments_due.html", context)


def phone_booking(request: HttpRequest) -> HttpResponse:
    """Takes a booking over the phone, on the guest's behalf.

    Reaches the seats held back from the website and the dates closed to it,
    which is what those seats are held back for. The total is worked out from
    the departure's own prices exactly as the public flow does; nothing about
    the money is taken from what was typed in.
    """
    if not request.user.has_perm(BOOK_PERMISSION):
        raise PermissionDenied

    picker = StaffDepartureChoiceForm(request.GET or None)
    chosen = bool(request.GET) and picker.is_valid()
    context = {
        **admin.site.each_context(request),
        "title": "Take a phone booking",
        "picker": picker,
        "chosen": chosen,
    }
    if not chosen:
        return TemplateResponse(request, "admin/phone_booking.html", context)

    departure = picker.cleaned_data["departure"]
    party = picker.cleaned_data["party"]

    if request.method == "POST":
        guest_form = LeadGuestForm(request.POST)
        traveller_forms = [
            TravellerForm(request.POST, departure=departure, prefix=f"t{index}")
            for index in range(party)
        ]
        payment_form = OfflinePaymentForm(request.POST)
        if (
            guest_form.is_valid()
            and all(form.is_valid() for form in traveller_forms)
            and payment_form.is_valid()
        ):
            response = _create_phone_booking(
                request, departure, guest_form, traveller_forms, payment_form
            )
            if response is not None:
                return response
    else:
        guest_form = LeadGuestForm()
        traveller_forms = [
            TravellerForm(departure=departure, prefix=f"t{index}") for index in range(party)
        ]
        payment_form = OfflinePaymentForm()

    context |= {
        "departure": departure,
        "party": party,
        "guest_form": guest_form,
        "traveller_forms": traveller_forms,
        "payment_form": payment_form,
        "seats_left": departure.seats_available_to_staff,
        "held_back": departure.seats_held_back,
    }
    return TemplateResponse(request, "admin/phone_booking.html", context)


def _create_phone_booking(request, departure, guest_form, traveller_forms, payment_form):
    """Creates the booking and records any money taken with it.

    Returns a redirect once the booking exists, or None to re-render the form
    with whatever went wrong shown on it.
    """
    try:
        booking = create_pending_booking(
            departure=departure,
            guest_data=guest_form.cleaned_data,
            travellers_data=[form.cleaned_data for form in traveller_forms],
            source=Booking.Source.PHONE,
            created_by=request.user,
            for_staff=True,
        )
    except SeatsUnavailable as exc:
        messages.error(request, f"Not enough seats: {exc}")
        return None
    except DepartureNotBookable:
        messages.error(request, "That date cannot be booked.")
        return None

    record(
        request,
        AuditEvent.Action.CREATE,
        booking,
        {"source": Booking.Source.PHONE, "travellers": str(len(traveller_forms))},
    )

    amount = payment_form.cleaned_data.get("amount")
    if amount:
        try:
            payment = record_offline_payment(
                booking=booking,
                amount=amount,
                method=payment_form.cleaned_data["method"],
                taken_by=request.user,
                reference=payment_form.cleaned_data.get("reference", ""),
            )
        except InvalidPayment as exc:
            messages.warning(
                request, f"Booking {booking.reference} was created, but the payment was not: {exc}"
            )
        else:
            record(
                request,
                AuditEvent.Action.CREATE,
                payment,
                {"amount": str(amount), "method": payment_form.cleaned_data["method"]},
            )
            messages.success(
                request, f"Booking {booking.reference} created and ${amount} recorded."
            )
            return redirect("admin:bookings_booking_change", booking.pk)

    messages.success(
        request,
        f"Booking {booking.reference} created. It holds its seats until payment is recorded.",
    )
    return redirect("admin:bookings_booking_change", booking.pk)
