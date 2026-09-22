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


class CardDeclined(RuntimeError):
    """An off-session charge the bank refused.

    Carries the failed payment intent when Stripe attached one, so staff can be
    pointed at it, but never any card detail.
    """

    def __init__(self, message: str, payment_intent: Any = None) -> None:
        super().__init__(message)
        self.payment_intent = payment_intent


def create_deposit_checkout_session(
    *,
    amount_minor: int,
    currency: str,
    product_name: str,
    product_description: str,
    client_reference_id: str,
    metadata: dict[str, str],
    success_url: str,
    cancel_url: str,
    idempotency_key: str,
    customer_email: str = "",
    customer_id: str = "",
) -> Any:
    """Opens a hosted checkout that charges the deposit and saves the card.

    ``setup_future_usage='off_session'`` is what lets the instalments be charged
    later without the guest present; the card itself is stored by Stripe, never
    here. A returning guest reuses their existing customer so the saved card
    stays in one place.
    """
    kwargs: dict[str, Any] = {}
    if customer_id:
        kwargs["customer"] = customer_id
    else:
        kwargs["customer_creation"] = "always"
        if customer_email:
            kwargs["customer_email"] = customer_email

    return stripe.checkout.Session.create(
        api_key=_api_key(),
        mode="payment",
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount_minor,
                    "product_data": {
                        "name": product_name,
                        "description": product_description,
                    },
                },
            }
        ],
        client_reference_id=client_reference_id,
        metadata=metadata,
        payment_intent_data={"metadata": metadata, "setup_future_usage": "off_session"},
        success_url=success_url,
        cancel_url=cancel_url,
        idempotency_key=idempotency_key,
        **kwargs,
    )


def retrieve_payment_intent(payment_intent_id: str) -> Any:
    """Fetches a payment intent with its charge and payment method expanded."""
    return stripe.PaymentIntent.retrieve(
        payment_intent_id,
        api_key=_api_key(),
        expand=["latest_charge", "payment_method"],
    )


def charge_saved_card(
    *,
    amount_minor: int,
    currency: str,
    customer_id: str,
    payment_method_id: str,
    metadata: dict[str, str],
    idempotency_key: str,
) -> Any:
    """Charges a saved card off-session for one instalment.

    Raises ``CardDeclined`` when the bank refuses, which for an off-session
    charge is a normal outcome (an expired or blocked card), not an error.
    """
    try:
        return stripe.PaymentIntent.create(
            api_key=_api_key(),
            amount=amount_minor,
            currency=currency,
            customer=customer_id,
            payment_method=payment_method_id,
            off_session=True,
            confirm=True,
            metadata=metadata,
            idempotency_key=idempotency_key,
            expand=["latest_charge", "payment_method"],
        )
    except stripe.CardError as exc:  # the card was refused
        raise CardDeclined(str(exc), getattr(exc, "payment_intent", None)) from exc


def card_details(intent: Any) -> tuple[str, str]:
    """The brand and last four of the card behind a payment intent.

    The only card facts this application ever holds, kept so staff can match a
    card a guest describes. Returns empty strings when the shape does not carry
    them rather than guessing.
    """
    card = None
    charge = getattr(intent, "latest_charge", None)
    if charge is not None and not isinstance(charge, str):
        details = getattr(charge, "payment_method_details", None)
        card = getattr(details, "card", None) if details is not None else None
    if card is None:
        method = getattr(intent, "payment_method", None)
        if method is not None and not isinstance(method, str):
            card = getattr(method, "card", None)
    if card is None:
        return "", ""
    return str(getattr(card, "brand", "") or ""), str(getattr(card, "last4", "") or "")


def payment_method_id(intent: Any) -> str:
    """The saved payment method's id from a payment intent, expanded or not."""
    method = getattr(intent, "payment_method", None)
    if method is None:
        return ""
    if isinstance(method, str):
        return method
    return str(getattr(method, "id", "") or "")
