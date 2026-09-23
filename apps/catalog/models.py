"""What is for sale.

The split that matters: a ``Trip`` is described and priced once; a ``Departure``
is one dated running of it, with its own seats, prices and pickup times. Putting
next season's dates up is copying a departure, not rewriting a trip.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.core.images import LOGO_SIZE, render_png, validate_image_upload, validate_logo_upload
from apps.core.models import ProcessedImagesMixin, TimeStampedModel

PHOTO_HELP = (
    "A JPEG, PNG or WebP photo, landscape, at least 1600 pixels wide for the sharpest "
    "result. It is resized automatically and its location data is removed."
)
ALT_HELP = (
    "Describe the photo in a few words for people using screen readers, "
    "e.g. 'The Breakers mansion seen from the lawn'."
)


class TripCategory(models.TextChoices):
    DAY_TRIP = "day_trip", "Day trip"
    OVERNIGHT = "overnight", "Overnight tour"
    THEATRE = "theatre", "Theatre coach"
    LUNCHEON = "luncheon", "Luncheon show"
    CRUISE = "cruise", "Cruise"
    FLY = "fly", "Fly tour"


class PublishedTripManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_published=True)


class Trip(ProcessedImagesMixin, TimeStampedModel):
    """A tour as it is described to guests, independent of any date."""

    IMAGE_FIELDS = {"hero_image": "hero_image_card"}

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    category = models.CharField(max_length=20, choices=TripCategory.choices, db_index=True)
    summary = models.CharField(max_length=300, help_text="One or two lines shown on listings.")
    description = models.TextField(help_text="The full write-up shown on the trip page.")
    inclusions = models.TextField(blank=True, help_text="What the price covers, one item per line.")
    exclusions = models.TextField(
        blank=True, help_text="What it does not cover, one item per line."
    )
    meals_included = models.PositiveSmallIntegerField(default=0)
    terms = models.TextField(blank=True, help_text="Booking terms shown before payment.")
    hero_image = models.ImageField(
        "main photo",
        upload_to="trips/",
        blank=True,
        validators=[validate_image_upload],
        help_text=PHOTO_HELP + " Shown at the top of the trip page and on listings.",
    )
    hero_image_alt = models.CharField(
        "main photo description", max_length=200, blank=True, help_text=ALT_HELP
    )
    hero_image_card = models.ImageField(upload_to="trips/cards/", blank=True, editable=False)
    is_published = models.BooleanField(
        default=False,
        help_text="Unpublished trips are invisible to guests, dates and all.",
    )

    objects = models.Manager()
    published = PublishedTripManager()

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["category", "is_published"])]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.title)[:200] or "trip"
        candidate = base
        suffix = 2
        while Trip.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def get_absolute_url(self) -> str:
        return reverse("catalog:trip-detail", kwargs={"slug": self.slug})

    @property
    def inclusion_lines(self) -> list[str]:
        return [line.strip() for line in self.inclusions.splitlines() if line.strip()]

    @property
    def exclusion_lines(self) -> list[str]:
        return [line.strip() for line in self.exclusions.splitlines() if line.strip()]

    def upcoming_departures(self):
        return self.departures.bookable().order_by("start_date")

    @property
    def placeholder_image_url(self) -> str:
        """The stand-in illustration for this trip's category."""
        return static(f"img/placeholders/{self.category}.svg")

    @property
    def image_alt(self) -> str:
        return self.hero_image_alt or self.title


class ItineraryDay(models.Model):
    """One day of a trip's published itinerary."""

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="itinerary_days")
    day_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["day_number"]
        constraints = [
            models.UniqueConstraint(fields=["trip", "day_number"], name="unique_day_per_trip")
        ]

    def __str__(self) -> str:
        return f"Day {self.day_number}: {self.title}"


class TripImage(ProcessedImagesMixin, models.Model):
    """One photo in a trip's gallery."""

    IMAGE_FIELDS = {"image": "image_card"}

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(
        "photo", upload_to="trips/", validators=[validate_image_upload], help_text=PHOTO_HELP
    )
    image_card = models.ImageField(upload_to="trips/cards/", blank=True, editable=False)
    alt_text = models.CharField("description", max_length=200, blank=True, help_text=ALT_HELP)
    caption = models.CharField(
        max_length=200, blank=True, help_text="Optional line shown under the photo."
    )
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]
        verbose_name = "gallery photo"

    def __str__(self) -> str:
        return self.caption or self.alt_text or f"Photo {self.pk}"

    @property
    def alt(self) -> str:
        return self.alt_text or self.caption or self.trip.title

    @property
    def card_url(self) -> str:
        return (self.image_card or self.image).url


class SiteImage(ProcessedImagesMixin, TimeStampedModel):
    """A photo for a fixed place on the public site: the home page banner, or
    the picture that stands for a trip category.

    Every slot has a built-in illustration, so the site looks finished before
    any of these exist; uploading one simply replaces the illustration.
    """

    class Slot(models.TextChoices):
        HOME_HERO = "home_hero", "Home page banner"
        DAY_TRIP = TripCategory.DAY_TRIP.value, "Category: " + TripCategory.DAY_TRIP.label
        OVERNIGHT = TripCategory.OVERNIGHT.value, "Category: " + TripCategory.OVERNIGHT.label
        THEATRE = TripCategory.THEATRE.value, "Category: " + TripCategory.THEATRE.label
        LUNCHEON = TripCategory.LUNCHEON.value, "Category: " + TripCategory.LUNCHEON.label
        CRUISE = TripCategory.CRUISE.value, "Category: " + TripCategory.CRUISE.label
        FLY = TripCategory.FLY.value, "Category: " + TripCategory.FLY.label

    IMAGE_FIELDS = {"image": "image_card"}

    slot = models.CharField(max_length=20, choices=Slot.choices, unique=True)
    image = models.ImageField(
        "photo", upload_to="site/", validators=[validate_image_upload], help_text=PHOTO_HELP
    )
    image_card = models.ImageField(upload_to="site/cards/", blank=True, editable=False)
    alt_text = models.CharField("description", max_length=200, help_text=ALT_HELP)

    class Meta:
        ordering = ["slot"]
        verbose_name = "site photo"

    def __str__(self) -> str:
        return self.get_slot_display()

    @staticmethod
    def placeholder_url(slot: str) -> str:
        name = "hero" if slot == SiteImage.Slot.HOME_HERO else slot
        return static(f"img/placeholders/{name}.svg")


TEXT_FORMAT_HELP = (
    "Plain text. Leave a blank line between paragraphs. Start a line with '## ' for a "
    "heading, or '- ' for a bullet point. Web addresses become links."
)


class SitePage(TimeStampedModel):
    """A page of words the business owns: its booking terms, privacy policy
    and contact details.

    Each page starts life as an unpublished starter draft and is invisible to
    guests until a Manager has read it, made it their own and ticked
    Published. The text is plain, never HTML, so nothing typed here can put
    markup or script on the public site.
    """

    class Kind(models.TextChoices):
        TERMS = "terms", "Booking terms and conditions"
        PRIVACY = "privacy", "Privacy policy"
        CONTACT = "contact", "Contact us"

    kind = models.CharField(max_length=20, choices=Kind.choices, unique=True)
    title = models.CharField(max_length=120)
    body = models.TextField(help_text=TEXT_FORMAT_HELP)
    is_published = models.BooleanField(
        "published",
        default=False,
        help_text="Tick once the page is accurate for your business. Guests see "
        "nothing until then.",
    )

    class Meta:
        ordering = ["kind"]
        verbose_name = "site page"

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse(f"catalog:{self.kind}")


class SiteText(TimeStampedModel):
    """The business's own logo and the few sentences that carry its voice.

    There is one row, edited in place. Any field left blank falls back to the
    built-in wording, so the site always reads as finished.
    """

    IMAGE_FIELD = "logo"

    home_headline = models.CharField(
        "home page headline",
        max_length=80,
        blank=True,
        help_text="Shown large on the home page banner, e.g. 'Leave the driving to us.'",
    )
    home_headline_accent = models.CharField(
        "headline, second part",
        max_length=80,
        blank=True,
        help_text="Shown after the headline in the accent colour, e.g. 'Enjoy the journey.'",
    )
    home_intro = models.TextField(
        "home page introduction",
        max_length=400,
        blank=True,
        help_text="One or two sentences under the headline.",
    )
    footer_about = models.TextField(
        "about us, in the footer",
        max_length=400,
        blank=True,
        help_text="A sentence or two about the business, shown at the foot of every page.",
    )
    logo = models.ImageField(
        upload_to="brand/",
        blank=True,
        validators=[validate_logo_upload],
        help_text="A square logo or emblem, PNG with a transparent background works best, "
        "at least 96 pixels across. Shown beside the business name.",
    )

    class Meta:
        verbose_name = "site text and logo"
        verbose_name_plural = "site text and logo"

    def __str__(self) -> str:
        return "Site text and logo"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._stored_logo = instance.logo.name if "logo" in field_names else ""
        return instance

    def save(self, *args, **kwargs):
        # One row only: a second one saved by accident overwrites the first,
        # keeping its creation time and tidying away its logo if replaced.
        if self._state.adding:
            existing = SiteText.objects.filter(pk=1).values("created_at", "logo").first()
            if existing:
                self.created_at = existing["created_at"]
                self._stored_logo = existing["logo"]
        self.pk = 1
        if self.logo and not self.logo._committed:
            self.logo = render_png(self.logo, LOGO_SIZE)
        super().save(*args, **kwargs)
        replaced = getattr(self, "_stored_logo", "")
        if replaced and replaced != self.logo.name:
            storage = self.logo.storage
            transaction.on_commit(lambda: storage.delete(replaced))
        self._stored_logo = self.logo.name

    @classmethod
    def current(cls) -> "SiteText | None":
        return cls.objects.filter(pk=1).first()


class PickupPoint(TimeStampedModel):
    """A boarding location, reused across departures."""

    name = models.CharField(max_length=120, unique=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True, help_text="State or province.")
    map_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["city", "name"]

    def __str__(self) -> str:
        return f"{self.name}, {self.city}"


class DepartureQuerySet(models.QuerySet):
    def published(self):
        return self.filter(trip__is_published=True)

    def upcoming(self):
        return self.filter(start_date__gte=timezone.localdate())

    def bookable(self):
        """Departures a guest may actually book right now."""
        now = timezone.now()
        return (
            self.published()
            .upcoming()
            .filter(status=Departure.Status.OPEN)
            .filter(models.Q(booking_opens_at__isnull=True) | models.Q(booking_opens_at__lte=now))
            .filter(models.Q(booking_closes_at__isnull=True) | models.Q(booking_closes_at__gte=now))
        )


class Departure(TimeStampedModel):
    """One dated running of a trip."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open for booking"
        WAITLIST = "waitlist", "Waitlist only"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name="departures")
    start_date = models.DateField(db_index=True)
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    capacity = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)], help_text="Total seats on the coach."
    )
    seats_held_back = models.PositiveSmallIntegerField(
        default=0, help_text="Seats kept off the website for phone bookings and escorts."
    )

    booking_opens_at = models.DateTimeField(null=True, blank=True)
    booking_closes_at = models.DateTimeField(null=True, blank=True)

    deposit_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Charged today when a guest chooses a payment plan.",
    )
    final_payment_due_date = models.DateField(
        null=True,
        blank=True,
        help_text="The last instalment of a payment plan lands on this date.",
    )
    plan_cutoff_days = models.PositiveSmallIntegerField(
        default=60,
        help_text="Payment plans are offered only this many days or more before departure.",
    )
    cancellation_policy = models.TextField(blank=True)
    notes_for_staff = models.TextField(blank=True, help_text="Never shown to guests.")

    objects = DepartureQuerySet.as_manager()

    class Meta:
        ordering = ["start_date"]
        indexes = [models.Index(fields=["status", "start_date"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="departure_ends_after_it_starts",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1), name="departure_has_capacity"
            ),
            models.CheckConstraint(
                condition=models.Q(deposit_amount__gte=0), name="departure_deposit_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.trip.title} — {self.start_date:%d %b %Y}"

    def get_absolute_url(self) -> str:
        return reverse("catalog:departure-detail", kwargs={"pk": self.pk})

    def clean(self) -> None:
        errors = {}
        if self.end_date and self.start_date and self.end_date < self.start_date:
            errors["end_date"] = "A departure cannot end before it starts."
        if (
            self.final_payment_due_date
            and self.start_date
            and (self.final_payment_due_date > self.start_date)
        ):
            errors["final_payment_due_date"] = "Final payment must fall on or before departure."
        if self.seats_held_back and self.capacity and self.seats_held_back >= self.capacity:
            errors["seats_held_back"] = "Holding back every seat leaves nothing to sell."
        if (
            self.booking_opens_at
            and self.booking_closes_at
            and (self.booking_closes_at <= self.booking_opens_at)
        ):
            errors["booking_closes_at"] = "Booking must close after it opens."
        if errors:
            raise ValidationError(errors)

    # --- Seats -------------------------------------------------------------

    @property
    def seats_for_sale(self) -> int:
        """Capacity minus the seats staff keep off the website."""
        return max(self.capacity - self.seats_held_back, 0)

    @property
    def seats_taken(self) -> int:
        """Travellers on confirmed bookings, plus pending bookings still holding."""
        from apps.bookings.models import Booking

        return (
            Booking.objects.filter(departure=self)
            .holding_seats()
            .aggregate(total=models.Count("travellers"))["total"]
            or 0
        )

    @property
    def seats_available(self) -> int:
        return max(self.seats_for_sale - self.seats_taken, 0)

    @property
    def seats_available_to_staff(self) -> int:
        """Every seat still free, including the ones held back from the website.

        Holding seats back is what reserves them for phone bookings and escorts,
        so a staff-taken booking counts against the coach's real capacity.
        """
        return max(self.capacity - self.seats_taken, 0)

    @property
    def is_sold_out(self) -> bool:
        return self.seats_available <= 0

    @property
    def duration_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    # --- Booking windows ---------------------------------------------------

    @property
    def is_bookable(self) -> bool:
        """Whether the website may take a booking for this departure right now."""
        now = timezone.now()
        if self.status != self.Status.OPEN or not self.trip.is_published:
            return False
        if self.start_date < timezone.localdate():
            return False
        if self.booking_opens_at and self.booking_opens_at > now:
            return False
        if self.booking_closes_at and self.booking_closes_at < now:
            return False
        return not self.is_sold_out

    @property
    def is_bookable_by_staff(self) -> bool:
        """Whether staff may take a booking for this date over the phone.

        Wider than ``is_bookable``: a date closed to the website, or one whose
        booking window has passed, is still bookable by a person on the phone.
        A cancelled departure and a date already gone are refused, because those
        are mistakes rather than phone bookings.
        """
        if self.status == self.Status.CANCELLED:
            return False
        if self.start_date < timezone.localdate():
            return False
        return self.seats_available_to_staff > 0

    @property
    def payment_plan_available(self) -> bool:
        """A plan needs room for at least two instalments before the due date."""
        if not self.final_payment_due_date or self.deposit_amount <= 0:
            return False
        cutoff = self.start_date - timedelta(days=self.plan_cutoff_days)
        return timezone.localdate() <= cutoff

    @property
    def lead_price(self):
        """The cheapest per-person price, used on listings."""
        cheapest = self.price_options.order_by("amount").first()
        return cheapest.amount if cheapest else None


class DeparturePickup(models.Model):
    """Where and when this departure boards."""

    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="pickups")
    pickup_point = models.ForeignKey(PickupPoint, on_delete=models.PROTECT, related_name="pickups")
    boarding_time = models.TimeField()
    seats_available = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Leave blank when the stop has no separate limit."
    )
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "boarding_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["departure", "pickup_point"], name="unique_pickup_per_departure"
            )
        ]

    def __str__(self) -> str:
        return f"{self.pickup_point.name} at {self.boarding_time:%H:%M}"


class PriceOption(models.Model):
    """A per-person price for a departure.

    Carries occupancy (single, double, triple) or an age band (adult, child)
    in the same shape, because both are 'a label with a price per person'.
    """

    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="price_options")
    label = models.CharField(max_length=80, help_text="e.g. Double occupancy, Child (under 12)")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_available = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "amount"]
        constraints = [
            models.UniqueConstraint(
                fields=["departure", "label"], name="unique_price_label_per_departure"
            ),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="price_not_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.label}: {self.amount}"
