from django.conf import settings
from django.db import models, transaction

from .images import CARD_SIZE, FULL_SIZE, render_jpeg


class TimeStampedModel(models.Model):
    """Created/updated stamps, inherited by everything that staff can edit."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ProcessedImagesMixin(models.Model):
    """Re-encodes uploaded photos on save and tidies away the files they replace.

    ``IMAGE_FIELDS`` maps each uploaded image field to the field holding its
    smaller card-sized copy. A fresh upload is replaced by a clean JPEG (see
    ``apps.core.images``) and the card copy is rebuilt from it. When a photo is
    replaced or cleared, the old files are deleted once the transaction
    commits, so the media disk does not fill with orphans.
    """

    IMAGE_FIELDS: dict[str, str] = {}

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        for source, card in self.IMAGE_FIELDS.items():
            upload = getattr(self, source)
            if upload and not upload._committed:
                setattr(self, card, render_jpeg(upload, CARD_SIZE))
                setattr(self, source, render_jpeg(upload, FULL_SIZE))
            elif not upload:
                setattr(self, card, "")
        super().save(*args, **kwargs)
        self.delete_image_files(getattr(self, "_stored_image_names", set()) - self._image_names())
        self._stored_image_names = self._image_names()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._stored_image_names = instance._image_names()
        return instance

    def _image_names(self) -> set[str]:
        names = set()
        for source, card in self.IMAGE_FIELDS.items():
            for field in (source, card):
                if field in self.get_deferred_fields():
                    continue
                file = getattr(self, field)
                if file:
                    names.add(file.name)
        return names

    def delete_image_files(self, names: set[str]) -> None:
        if not names:
            return
        storage = self._meta.get_field(next(iter(self.IMAGE_FIELDS))).storage

        def _delete():
            for name in names:
                storage.delete(name)

        transaction.on_commit(_delete)


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
        # Personal details leaving the building is worth a row of its own.
        EXPORT = "export", "Exported"

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
