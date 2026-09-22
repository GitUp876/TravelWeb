"""The only module that talks to Stripe.

Keeping the SDK behind three functions means the rest of the application never
imports stripe, and tests replace these rather than mocking a client library.
"""

from __future__ import annotations

from typing import Any

import stripe
from django.conf import settings


class PaymentConfigurationError(RuntimeError):
    """Raised when Stripe is asked for work it has no credentials to do."""


def _api_key() -> str:
    if not settings.STRIPE_SECRET_KEY:
        raise PaymentConfigurationError("STRIPE_SECRET_KEY is not set")
    return settings.STRIPE_SECRET_KEY


def create_checkout_session(
    *,
    line_items: list[dict[str, Any]],
    customer_email: str,
    client_reference_id: str,
    metadata: dict[str, str],
    success_url: str,
    cancel_url: str,
    idempotency_key: str,
) -> Any:
    """Opens a Stripe-hosted checkout page for one booking.

    The amounts come from our own database, never from the browser. The
    idempotency key is the booking reference, so a guest who double-submits
    gets the same session rather than a second charge.
    """
    return stripe.checkout.Session.create(
        api_key=_api_key(),
        mode="payment",
        line_items=line_items,
        customer_email=customer_email,
        client_reference_id=client_reference_id,
        metadata=metadata,
        payment_intent_data={"metadata": metadata},
        success_url=success_url,
        cancel_url=cancel_url,
        idempotency_key=idempotency_key,
    )


def construct_event(payload: bytes, signature_header: str) -> Any:
    """Verifies a webhook's signature and returns the event.

    Raises if the signature does not match, which is the only thing standing
    between us and anyone who can POST to the webhook URL.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise PaymentConfigurationError("STRIPE_WEBHOOK_SECRET is not set")
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature_header,
        secret=settings.STRIPE_WEBHOOK_SECRET,
    )


def signature_errors() -> tuple[type[Exception], ...]:
    """Exceptions that mean 'this request did not come from Stripe'."""
    return (ValueError, stripe.SignatureVerificationError)
