"""Forms for the public booking flow.

Two rules run through all of it: the browser never supplies a price, and every
choice a guest makes is re-checked against the departure they are booking.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.catalog.models import Departure

from .models import Guest, Payment, Traveller
from .services import OFFLINE_METHODS

MAX_PARTY_SIZE = 10


class PartySizeForm(forms.Form):
    """The 'how many of you?' selector, submitted as a GET so it needs no JS."""

    party = forms.IntegerField(min_value=1, max_value=MAX_PARTY_SIZE, initial=1)


class LeadGuestForm(forms.ModelForm):
    """Who we contact about the booking."""

    class Meta:
        model = Guest
        fields = [
            "full_name",
            "email",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "region",
            "postal_code",
            "marketing_consent",
        ]
        labels = {
            "full_name": "Your name",
            "email": "Email address",
            "phone": "Phone number",
            "marketing_consent": "Email me about future trips",
        }
        help_texts = {
            "email": "Your booking confirmation and your booking link go here.",
            "marketing_consent": "Optional, and never ticked for you.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = True
        for name in ("address_line2", "region"):
            self.fields[name].required = False


class TravellerForm(forms.ModelForm):
    """One person on the coach."""

    class Meta:
        model = Traveller
        fields = [
            "full_name",
            "price_option",
            "pickup",
            "dietary_notes",
            "mobility_notes",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]
        labels = {
            "price_option": "Price option",
            "pickup": "Boarding point",
            "dietary_notes": "Dietary requirements",
            "mobility_notes": "Mobility needs",
        }
        help_texts = {
            "dietary_notes": "Shared with the tour director and caterers only.",
            "mobility_notes": "So we can seat and assist you properly.",
        }

    def __init__(self, *args, departure: Departure, **kwargs):
        super().__init__(*args, **kwargs)
        # Narrowing the querysets to this departure is what stops a guest
        # posting the id of a cheaper price from another date.
        self.fields["price_option"].queryset = departure.price_options.filter(is_available=True)
        self.fields["price_option"].empty_label = None
        self.fields["pickup"].queryset = departure.pickups.select_related("pickup_point")
        self.fields["pickup"].required = departure.pickups.exists()
        self.fields["emergency_contact_name"].required = True
        self.fields["emergency_contact_phone"].required = True


class PaymentOptionForm(forms.Form):
    """How the guest wants to pay: in full, or a deposit then instalments.

    The instalment option is only ever offered when the departure allows it;
    the choice is re-checked server-side before a plan is set up, so posting the
    plan value for a date that does not offer one changes nothing.
    """

    FULL = "full"
    PLAN = "plan"

    # Optional: an absent or empty value means pay in full, so the plain
    # pay-in-full flow needs no extra field in its post.
    payment_option = forms.ChoiceField(
        label="Payment",
        widget=forms.RadioSelect,
        initial=FULL,
        required=False,
        choices=[(FULL, "Pay in full now")],
    )

    def __init__(self, *args, plan_available: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if plan_available:
            self.fields["payment_option"].choices = [
                (self.FULL, "Pay in full now"),
                (self.PLAN, "Pay a deposit now, the balance in scheduled instalments"),
            ]


class StaffDepartureChoiceForm(forms.Form):
    """Which date, and for how many, before the traveller forms can be built.

    Offers dates the website has closed or never showed, because that is exactly
    what someone rings up about; a cancelled date and one already gone are left
    out, since booking either is a mistake rather than a phone booking.
    """

    departure = forms.ModelChoiceField(queryset=Departure.objects.none(), label="Date")
    party = forms.IntegerField(
        min_value=1, max_value=MAX_PARTY_SIZE, initial=1, label="How many travelling"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["departure"].queryset = (
            Departure.objects.select_related("trip")
            .exclude(status=Departure.Status.CANCELLED)
            .filter(start_date__gte=timezone.localdate())
            .order_by("start_date")
        )


class OfflinePaymentForm(forms.Form):
    """Money taken by hand: cash, a cheque, a bank transfer.

    There is deliberately no card field. A guest paying by card pays on Stripe's
    own page; taking a card number over the phone and typing it in here would
    move the whole site out of PCI SAQ A.
    """

    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        label="Amount taken",
    )
    method = forms.ChoiceField(
        choices=[("", "No payment taken yet")]
        + [(value, label) for value, label in Payment.Method.choices if value in OFFLINE_METHODS],
        required=False,
        label="How it was paid",
    )
    reference = forms.CharField(
        max_length=100, required=False, label="Cheque number or receipt reference"
    )

    def clean(self):
        cleaned = super().clean()
        amount, method = cleaned.get("amount"), cleaned.get("method")
        if amount and not method:
            raise ValidationError({"method": "Say how the money was taken."})
        if method and not amount:
            raise ValidationError({"amount": "Enter the amount taken."})
        return cleaned


class BookingLookupForm(forms.Form):
    """Asks for the email on a booking and emails the link back.

    Deliberately says the same thing whether or not the address is known, so
    the form cannot be used to discover who has booked.
    """

    reference = forms.CharField(label="Booking reference", max_length=12)
    email = forms.EmailField(label="Email address on the booking")

    def clean_reference(self) -> str:
        return self.cleaned_data["reference"].strip().upper()
