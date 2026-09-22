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

    def save_formset(self, request, form, formset, change):
        """Audits the rows edited inline too: prices, pickups, photos."""
        super().save_formset(request, form, formset, change)
        parent = {"parent": str(form.instance)[:200]}
        for obj in formset.new_objects:
            record(request, AuditEvent.Action.CREATE, obj, parent)
        for obj, fields in formset.changed_objects:
            changed = {field: str(getattr(obj, field, ""))[:200] for field in fields}
            record(request, AuditEvent.Action.UPDATE, obj, {**parent, **changed})
        for obj in formset.deleted_objects:
            record(request, AuditEvent.Action.DELETE, obj, parent)

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
