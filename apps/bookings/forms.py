"""Forms for the public booking flow.

Two rules run through all of it: the browser never supplies a price, and every
choice a guest makes is re-checked against the departure they are booking.
"""

from __future__ import annotations

from django import forms

from apps.catalog.models import Departure

from .models import Guest, Traveller

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


class BookingLookupForm(forms.Form):
    """Asks for the email on a booking and emails the link back.

    Deliberately says the same thing whether or not the address is known, so
    the form cannot be used to discover who has booked.
    """

    reference = forms.CharField(label="Booking reference", max_length=12)
    email = forms.EmailField(label="Email address on the booking")

    def clean_reference(self) -> str:
        return self.cleaned_data["reference"].strip().upper()
