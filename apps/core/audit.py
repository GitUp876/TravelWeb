"""Helpers for writing the audit trail."""

from typing import Any

from django.db import models
from django.http import HttpRequest

from .models import AuditEvent

# Fields that must never be copied into an audit row.
SENSITIVE_FIELDS = {
    "password",
    "stripe_customer_id",
    "stripe_payment_method_id",
    "dietary_notes",
    "mobility_notes",
}


def client_ip(request: HttpRequest) -> str | None:
    """The caller's address, trusting only the proxy header we terminate at."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def record(
    request: HttpRequest | None,
    action: str,
    obj: models.Model | None = None,
    changes: dict[str, Any] | None = None,
) -> AuditEvent:
    user = getattr(request, "user", None) if request else None
    actor = user if user is not None and user.is_authenticated else None
    return AuditEvent.objects.create(
        actor=actor,
        actor_label=str(actor) if actor else "anonymous",
        action=action,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None and obj.pk else "",
        object_label=str(obj)[:255] if obj is not None else "",
        changes=redact(changes or {}),
        ip_address=client_ip(request) if request else None,
    )


def redact(changes: dict[str, Any]) -> dict[str, Any]:
    """Drops anything sensitive before it reaches the audit table."""
    return {
        field: ("[redacted]" if field in SENSITIVE_FIELDS else value)
        for field, value in changes.items()
    }
