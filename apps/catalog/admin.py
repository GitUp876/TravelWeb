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
    Trip,
    TripImage,
)


class ItineraryDayInline(admin.TabularInline):
    model = ItineraryDay
    extra = 0


class TripImageInline(admin.TabularInline):
    model = TripImage
    extra = 0


class DepartureInline(admin.TabularInline):
    model = Departure
    extra = 0
    fields = ("start_date", "end_date", "status", "capacity", "deposit_amount")
    show_change_link = True
    ordering = ("start_date",)


@admin.register(Trip)
class TripAdmin(AuditedAdmin):
    list_display = ("title", "category", "is_published", "departure_count", "meals_included")
    list_filter = ("category", "is_published")
    search_fields = ("title", "summary", "description")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [ItineraryDayInline, TripImageInline, DepartureInline]
    actions = ["publish", "unpublish"]
    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "is_published")}),
        ("Guest-facing copy", {"fields": ("summary", "description", "hero_image")}),
        ("What's included", {"fields": ("inclusions", "exclusions", "meals_included")}),
        ("Terms", {"fields": ("terms",)}),
    )

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
        colour = "#b3261e" if available == 0 else "#1b5e20"
        return format_html(
            '<span style="color:{}">{} sold · {} left of {}</span>',
            colour,
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
