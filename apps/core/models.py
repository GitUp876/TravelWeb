from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Created/updated stamps, inherited by everything that staff can edit."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    """An append-only record of what staff changed.

    Written for every create, edit and delete a staff member makes through the
    admin. Rows are never edited or deleted from the application; the model is
    registered read-only in the admin.
    """

    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        DELETE = "delete", "Deleted"
        LOGIN = "login", "Signed in"
        LOGIN_FAILED = "login_failed", "Failed sign-in"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_label = models.CharField(
        max_length=254,
        blank=True,
        help_text="Who acted, kept verbatim so the trail survives account deletion.",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    object_label = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Field-level before/after values. Never holds payment details.",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.actor_label} {self.action} {self.object_type} {self.object_label}".strip()
