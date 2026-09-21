from django.contrib import admin

from .models import StripeEvent


@admin.register(StripeEvent)
class StripeEventAdmin(admin.ModelAdmin):
    """Read-only: this is a log of what Stripe told us, not something to edit."""

    list_display = ("received_at", "event_type", "status", "event_id", "detail")
    list_filter = ("status", "event_type", "received_at")
    search_fields = ("event_id", "detail")
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
