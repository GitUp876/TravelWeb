from django.contrib import admin

from apps.core.admin import AuditedAdmin

from .models import (
    Booking,
    Guest,
    Payment,
    PaymentPlan,
    ScheduledPayment,
    Traveller,
    WaitlistEntry,
)


class TravellerInline(admin.TabularInline):
    model = Traveller
    extra = 0
    fields = (
        "full_name",
        "price_option",
        "pickup",
        "dietary_notes",
        "mobility_notes",
        "emergency_contact_name",
        "emergency_contact_phone",
        "room_assignment",
    )


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("received_at", "kind", "method", "amount", "offline_reference", "taken_by")
    readonly_fields = ("received_at",)


@admin.register(Guest)
class GuestAdmin(AuditedAdmin):
    list_display = ("full_name", "email", "phone", "city", "marketing_consent")
    search_fields = ("full_name", "email", "phone")
    list_filter = ("marketing_consent", "country")
    readonly_fields = ("stripe_customer_id",)


@admin.register(Booking)
class BookingAdmin(AuditedAdmin):
    list_display = (
        "reference",
        "departure",
        "guest",
        "status",
        "traveller_count",
        "total_amount",
        "amount_paid",
        "balance",
    )
    list_filter = ("status", "source", "departure__start_date")
    search_fields = ("reference", "guest__full_name", "guest__email")
    autocomplete_fields = ("departure", "guest")
    readonly_fields = ("reference", "balance", "confirmed_at", "cancelled_at")
    inlines = [TravellerInline, PaymentInline]

    @admin.display(description="Balance")
    def balance(self, obj: Booking):
        return obj.balance

    @admin.display(description="Travellers")
    def traveller_count(self, obj: Booking) -> int:
        return obj.traveller_count


class ScheduledPaymentInline(admin.TabularInline):
    model = ScheduledPayment
    extra = 0
    readonly_fields = ("stripe_invoice_id", "attempt_count", "last_attempt_at")


@admin.register(PaymentPlan)
class PaymentPlanAdmin(AuditedAdmin):
    list_display = ("booking", "status", "instalment_count", "deposit_amount")
    list_filter = ("status",)
    search_fields = ("booking__reference",)
    readonly_fields = ("stripe_schedule_id",)
    inlines = [ScheduledPaymentInline]


@admin.register(Payment)
class PaymentAdmin(AuditedAdmin):
    list_display = ("received_at", "booking", "kind", "method", "amount", "card_last4", "taken_by")
    list_filter = ("kind", "method", "received_at")
    search_fields = ("booking__reference", "offline_reference")
    readonly_fields = ("stripe_payment_intent_id", "card_brand", "card_last4")


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(AuditedAdmin):
    list_display = ("full_name", "departure", "party_size", "created_at", "contacted_at")
    list_filter = ("departure__start_date", "contacted_at")
    search_fields = ("full_name", "email", "phone")
