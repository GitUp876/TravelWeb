from django.db import models


class StripeEvent(models.Model):
    """Every webhook Stripe has delivered, recorded once.

    Stripe retries deliveries and may send the same event more than once, so
    the event id is the idempotency key: a second delivery finds the row and
    does nothing. The event body is not stored, because it carries guest
    details we have no reason to keep twice.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    event_id = models.CharField(max_length=80, unique=True)
    event_type = models.CharField(max_length=80, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    detail = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.event_type} {self.event_id}"
