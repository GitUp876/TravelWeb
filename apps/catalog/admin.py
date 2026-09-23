from django.contrib import admin, messages
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html

from apps.core.admin import AuditedAdmin
from apps.core.audit import record
from apps.core.models import AuditEvent

from .models import (
    Departure,
    DeparturePickup,
    ItineraryDay,
    PickupPoint,
    PriceOption,
    SiteImage,
    SitePage,
    SiteText,
    Trip,
    TripImage,
)


def photo_preview(file, alt: str = "") -> str:
    """A small thumbnail of a stored photo, for the admin forms and lists."""
    if not file:
        return "—"
    return format_html('<img class="admin-photo-preview" src="{}" alt="{}">', file.url, alt)


class ItineraryDayInline(admin.TabularInline):
    model = ItineraryDay
    extra = 0


class TripImageInline(admin.StackedInline):
    model = TripImage
    extra = 0
    fields = (("preview", "image"), ("alt_text", "caption"), "display_order")
    readonly_fields = ("preview",)
    verbose_name_plural = "Photo gallery (shown on the trip page, in this order)"

    @admin.display(description="Current photo")
    def preview(self, obj: TripImage) -> str:
        return photo_preview(obj.image_card, obj.alt)


class DepartureInline(admin.TabularInline):
    model = Departure
    extra = 0
    fields = ("start_date", "end_date", "status", "capacity", "deposit_amount")
    show_change_link = True
    ordering = ("start_date",)


@admin.register(Trip)
class TripAdmin(AuditedAdmin):
    list_display = (
        "thumbnail",
        "title",
        "category",
        "is_published",
        "departure_count",
        "meals_included",
    )
    list_display_links = ("thumbnail", "title")
    list_filter = ("category", "is_published")
    search_fields = ("title", "summary", "description")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [ItineraryDayInline, TripImageInline, DepartureInline]
    actions = ["publish", "unpublish"]
    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "is_published")}),
        ("Guest-facing copy", {"fields": ("summary", "description")}),
        (
            "Main photo",
            {
                "fields": ("hero_preview", "hero_image", "hero_image_alt"),
                "description": "Without a photo the trip shows its category's illustration. "
                "Add more photos in the gallery further down.",
            },
        ),
        ("What's included", {"fields": ("inclusions", "exclusions", "meals_included")}),
        ("Terms", {"fields": ("terms",)}),
    )

    readonly_fields = ("hero_preview",)

    @admin.display(description="Photo")
    def thumbnail(self, obj: Trip) -> str:
        return photo_preview(obj.hero_image_card, obj.image_alt)

    @admin.display(description="Current photo")
    def hero_preview(self, obj: Trip) -> str:
        return photo_preview(obj.hero_image_card, obj.image_alt)

    @admin.display(description="Departures")
    def departure_count(self, obj: Trip) -> int:
        return obj.departures.count()

    @admin.action(description="Publish selected trips")
    def publish(self, request, queryset):
        updated = queryset.update(is_published=True)
        for trip in queryset:
            record(request, AuditEvent.Action.UPDATE, trip, {"is_published": "True"})
        self.message_user(request, f"Published {updated} trip(s).", messages.SUCCESS)

    @admin.action(description="Unpublish selected trips")
    def unpublish(self, request, queryset):
        updated = queryset.update(is_published=False)
        for trip in queryset:
            record(request, AuditEvent.Action.UPDATE, trip, {"is_published": "False"})
        self.message_user(request, f"Unpublished {updated} trip(s).", messages.SUCCESS)


class DeparturePickupInline(admin.TabularInline):
    model = DeparturePickup
    extra = 1
    autocomplete_fields = ("pickup_point",)


class PriceOptionInline(admin.TabularInline):
    model = PriceOption
    extra = 1


@admin.register(Departure)
class DepartureAdmin(AuditedAdmin):
    list_display = (
        "trip",
        "start_date",
        "end_date",
        "status",
        "seats_summary",
        "lead_price",
        "final_payment_due_date",
        "manifest_link",
    )
    list_filter = ("status", "trip__category", "start_date")
    search_fields = ("trip__title",)
    date_hierarchy = "start_date"
    autocomplete_fields = ("trip",)
    inlines = [PriceOptionInline, DeparturePickupInline]
    readonly_fields = ("seats_summary",)
    actions = ["duplicate_for_next_season", "open_for_booking", "close_booking"]
    fieldsets = (
        (None, {"fields": ("trip", "start_date", "end_date", "status")}),
        ("Seats", {"fields": ("capacity", "seats_held_back", "seats_summary")}),
        (
            "Booking window",
            {"fields": ("booking_opens_at", "booking_closes_at")},
        ),
        (
            "Payments",
            {
                "fields": ("deposit_amount", "final_payment_due_date", "plan_cutoff_days"),
                "description": "A payment plan takes the deposit today and finishes on the "
                "final payment date.",
            },
        ),
        ("Policy and notes", {"fields": ("cancellation_policy", "notes_for_staff")}),
    )

    @admin.display(description="Manifest")
    def manifest_link(self, obj: Departure) -> str:
        if not obj.pk:
            return "—"
        return format_html(
            '<a href="{}">Passenger list</a>',
            reverse("admin:departure-manifest", args=[obj.pk]),
        )

    @admin.display(description="Seats")
    def seats_summary(self, obj: Departure) -> str:
        if not obj.pk:
            return "—"
        taken, available = obj.seats_taken, obj.seats_available
        state = "sold-out" if available == 0 else "open"
        return format_html(
            '<span class="seats seats-{}">{} sold · {} left of {}</span>',
            state,
            taken,
            available,
            obj.seats_for_sale,
        )

    @admin.action(description="Duplicate (same trip, blank dates)")
    def duplicate_for_next_season(self, request, queryset):
        created = 0
        with transaction.atomic():
            for departure in queryset:
                prices = list(departure.price_options.all())
                pickups = list(departure.pickups.all())
                departure.pk = None
                departure.status = Departure.Status.DRAFT
                departure.booking_opens_at = None
                departure.booking_closes_at = None
                departure.save()
                for price in prices:
                    price.pk, price.departure = None, departure
                    price.save()
                for pickup in pickups:
                    pickup.pk, pickup.departure = None, departure
                    pickup.save()
                record(request, AuditEvent.Action.CREATE, departure, {"duplicated": "true"})
                created += 1
        self.message_user(
            request,
            f"Created {created} draft departure(s). Set the new dates before publishing.",
            messages.SUCCESS,
        )

    @admin.action(description="Open for booking")
    def open_for_booking(self, request, queryset):
        updated = queryset.update(status=Departure.Status.OPEN)
        self.message_user(request, f"Opened {updated} departure(s).", messages.SUCCESS)

    @admin.action(description="Close booking")
    def close_booking(self, request, queryset):
        updated = queryset.update(status=Departure.Status.CLOSED)
        self.message_user(request, f"Closed {updated} departure(s).", messages.SUCCESS)


@admin.register(PickupPoint)
class PickupPointAdmin(AuditedAdmin):
    list_display = ("name", "city", "region", "is_active")
    list_filter = ("is_active", "region")
    search_fields = ("name", "city", "address")


@admin.register(SiteImage)
class SiteImageAdmin(AuditedAdmin):
    """Photos for the home page banner and the trip-category tiles."""

    list_display = ("thumbnail", "slot", "alt_text", "updated_at")
    list_display_links = ("thumbnail", "slot")
    fields = ("preview", "slot", "image", "alt_text")
    readonly_fields = ("preview",)

    @admin.display(description="Photo")
    def thumbnail(self, obj: SiteImage) -> str:
        return photo_preview(obj.image_card, obj.alt_text)

    @admin.display(description="Current photo")
    def preview(self, obj: SiteImage) -> str:
        return photo_preview(obj.image_card, obj.alt_text)


@admin.register(SitePage)
class SitePageAdmin(AuditedAdmin):
    """Booking terms, privacy policy and contact page.

    The three pages exist from the start as unpublished starter drafts, so
    staff edit rather than create them, and none can be deleted.
    """

    list_display = ("title", "kind", "is_published", "updated_at")
    fields = ("kind", "title", "body", "is_published")
    readonly_fields = ("kind",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SiteText)
class SiteTextAdmin(AuditedAdmin):
    """The business's logo and its own words on the home page and footer."""

    fields = (
        "logo_preview",
        "logo",
        "home_headline",
        "home_headline_accent",
        "home_intro",
        "footer_about",
    )
    readonly_fields = ("logo_preview",)

    @admin.display(description="Current logo")
    def logo_preview(self, obj: SiteText) -> str:
        return photo_preview(obj.logo, "Current logo")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not SiteText.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
