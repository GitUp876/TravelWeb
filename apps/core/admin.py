from django.contrib import admin

from .audit import record
from .models import AuditEvent


class AuditedAdmin(admin.ModelAdmin):
    """Writes an audit row for every change a staff member makes."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        changed = {
            field: str(form.cleaned_data.get(field, ""))[:200] for field in form.changed_data
        }
        action = AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE
        record(request, action, obj, changed)

    def delete_model(self, request, obj):
        record(request, AuditEvent.Action.DELETE, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            record(request, AuditEvent.Action.DELETE, obj)
        super().delete_queryset(request, queryset)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "object_type", "object_label")
    list_filter = ("action", "object_type", "created_at")
    search_fields = ("actor_label", "object_label", "object_id")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
