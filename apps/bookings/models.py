"""Who is going, and what they have paid.

Guests are not Django users: they never get a password, so there is no guest
credential store to breach. A guest reaches their booking through an expiring
signed link sent to the address on the booking (built in phase 2).

No card data is stored here. The payment models hold Stripe identifiers and the
brand and last four digits only, which is what keeps the site in PCI SAQ A.
"""

import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.catalog.models import Departure, DeparturePickup, PriceOption
from apps.core.models import TimeStampedModel

REFERENCE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY3479"  # no characters that misread aloud


def make_reference(length: int = 8) -> str:
    """A booking reference staff can read down a phone line."""
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(length))


class Guest(TimeStampedModel):
    """The person who made the booking and receives everything about it."""

    full_name = models.CharField(max_length=150)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=40, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True, default="US")
    marketing_consent = models.BooleanField(
        default=False, help_text="Opt-in only; never ticked on the guest's behalf."
    )
    stripe_customer_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_guest_email_ci")]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class BookingQuerySet(models.QuerySet):
    def holding_seats(self):
        """Bookings whose travellers currently occupy a seat.

        Confirmed bookings always do. A pending booking holds its seats only
        until its hold expires, after which the seats return to the pool.
        """
        now = timezone.now()
        return self.filter(
            models.Q(status=Booking.Status.CONFIRMED)
            | models.Q(status=Booking.Status.PENDING, hold_expires_at__gt=now)
        )

    def outstanding(self):
        return self.filter(status=Booking.Status.CONFIRMED).exclude(
            amount_paid=models.F("total_amount")
        )


class Booking(TimeStampedModel):
    """One party travelling on one departure."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    class Source(models.TextChoices):
        WEB = "web", "Website"
        PHONE = "phone", "Phone"
        WALK_IN = "walk_in", "Walk-in"

    reference = models.CharField(max_length=12, unique=True, editable=False)
    departure = models.ForeignKey(Departure, on_delete=models.PROTECT, related_name="bookings")
    guest = models.ForeignKey(Guest, on_delete=models.PROTECT, related_name="bookings")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.WEB)

    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    hold_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="While pending, the seats are held until this moment.",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    staff_notes = models.TextField(blank=True, help_text="Never shown to the guest.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bookings_created",
        help_text="Set when a staff member took the booking; blank for web bookings.",
    )

    objects = BookingQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["departure", "status"]),
            models.Index(fields=["status", "hold_expires_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0) & models.Q(amount_paid__gte=0),
                name="booking_amounts_not_negative",
            )
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.guest.full_name}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._unique_reference()
        super().save(*args, **kwargs)

    @staticmethod
    def _unique_reference() -> str:
        for _ in range(10):
            candidate = make_reference()
            if not Booking.objects.filter(reference=candidate).exists():
                return candidate
        raise RuntimeError("Could not allocate a unique booking reference")

    @property
    def balance(self) -> Decimal:
        return self.total_amount - self.amount_paid

    @property
    def is_paid_in_full(self) -> bool:
        return self.balance <= 0

    @property
    def traveller_count(self) -> int:
        return self.travellers.count()

    def recalculate_total(self) -> Decimal:
        """The sum of each traveller's chosen price. Never trusts the browser."""
        total = sum(
            (t.price_option.amount for t in self.travellers.select_related("price_option")),
            Decimal("0.00"),
        )
        self.total_amount = total
        return total


class Traveller(models.Model):
    """One person on the coach. This is the row the manifest prints."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="travellers")
    full_name = models.CharField(max_length=150)
    price_option = models.ForeignKey(PriceOption, on_delete=models.PROTECT, related_name="+")
    pickup = models.ForeignKey(
        DeparturePickup, null=True, blank=True, on_delete=models.SET_NULL, related_name="travellers"
    )
    dietary_notes = models.CharField(
        max_length=255,
        blank=True,
        help_text="Health-adjacent. Shown only on the manifest, never in exports.",
    )
    mobility_notes = models.CharField(max_length=255, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=40, blank=True)
    room_assignment = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["booking", "id"]

    def __str__(self) -> str:
        return self.full_name

    def clean(self) -> None:
        if (
            self.price_option_id
            and self.booking_id
            and (self.price_option.departure_id != self.booking.departure_id)
        ):
            raise ValidationError({"price_option": "That price belongs to another departure."})
        if (
            self.pickup_id
            and self.booking_id
            and (self.pickup.departure_id != self.booking.departure_id)
        ):
            raise ValidationError({"pickup": "That pickup belongs to another departure."})


class WaitlistEntry(TimeStampedModel):
    """Someone to call when a seat frees up."""

    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="waitlist")
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    party_size = models.PositiveSmallIntegerField(default=1)
    notes = models.CharField(max_length=255, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "waitlist entries"

    def __str__(self) -> str:
        return f"{self.full_name} for {self.departure}"


class PaymentPlan(TimeStampedModel):
    """A deposit today, then instalments Stripe charges automatically."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Needs attention"

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="payment_plan")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    instalment_count = models.PositiveSmallIntegerField()
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2)
    stripe_schedule_id = models.CharField(max_length=64, blank=True, db_index=True)

    def __str__(self) -> str:
        return f"Plan for {self.booking.reference}"


class ScheduledPayment(models.Model):
    """One dated instalment of a plan."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        WRITTEN_OFF = "written_off", "Written off"

    plan = models.ForeignKey(PaymentPlan, on_delete=models.CASCADE, related_name="instalments")
    due_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    stripe_invoice_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self) -> str:
        return f"{self.amount} due {self.due_date:%d %b %Y}"


class Payment(TimeStampedModel):
    """Money that actually moved.

    Card details are never stored: only the Stripe identifier and the brand and
    last four digits, so staff can recognise a card the guest describes.
    """

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        INSTALMENT = "instalment", "Instalment"
        BALANCE = "balance", "Balance"
        FULL = "full", "Payment in full"
        REFUND = "refund", "Refund"

    class Method(models.TextChoices):
        CARD = "card", "Card (Stripe)"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"
        TRANSFER = "transfer", "Bank transfer"

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Negative for a refund."
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CARD)
    stripe_payment_intent_id = models.CharField(max_length=64, blank=True, db_index=True)
    offline_reference = models.CharField(
        max_length=100, blank=True, help_text="Cheque number or receipt reference."
    )
    card_brand = models.CharField(max_length=20, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payments_taken",
    )
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.amount} on {self.booking.reference}"

    def clean(self) -> None:
        if len(self.card_last4) not in (0, 4):
            raise ValidationError({"card_last4": "Store the last four digits, or nothing at all."})
        if not self.card_last4.isdigit() and self.card_last4:
            raise ValidationError({"card_last4": "Digits only."})
