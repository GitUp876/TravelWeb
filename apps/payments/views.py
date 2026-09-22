"""The Stripe webhook.

This endpoint is public, so the signature check is the whole security model:
nothing is read out of the body until Stripe's signature over that exact body
verifies against our signing secret.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import gateway, services
from .models import StripeEvent

logger = logging.getLogger(__name__)

HANDLERS = {
    "checkout.session.completed": services.handle_checkout_completed,
    "checkout.session.expired": services.release_expired_checkout,
    "payment_intent.succeeded": services.record_instalment_payment,
    "payment_intent.payment_failed": services.fail_instalment_payment,
}


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    if not signature:
        return HttpResponseBadRequest("missing signature")

    try:
        event = gateway.construct_event(request.body, signature)
    except gateway.PaymentConfigurationError:
        logger.error("Stripe webhook received while payments are unconfigured")
        return HttpResponse(status=503)
    except gateway.signature_errors():
        # Either a malformed body or someone who is not Stripe.
        logger.warning("Rejected a Stripe webhook with an invalid signature")
        return HttpResponseBadRequest("invalid signature")

    event_id = str(getattr(event, "id", "") or "")
    event_type = str(getattr(event, "type", "") or "")
    if not event_id:
        return HttpResponseBadRequest("event carried no id")

    record, created = StripeEvent.objects.get_or_create(
        event_id=event_id, defaults={"event_type": event_type}
    )
    if not created:
        # Stripe retries; we have seen this one already.
        return JsonResponse({"status": "duplicate"})

    handler = HANDLERS.get(event_type)
    if handler is None:
        record.status = StripeEvent.Status.IGNORED
        record.processed_at = timezone.now()
        record.save(update_fields=["status", "processed_at"])
        return JsonResponse({"status": "ignored"})

    try:
        handler(event.data.object)
    except services.UnknownBooking as exc:
        record.status = StripeEvent.Status.FAILED
        record.detail = str(exc)[:255]
        record.processed_at = timezone.now()
        record.save(update_fields=["status", "detail", "processed_at"])
        logger.error("Stripe event %s named a booking we do not hold", event_id)
        # 200 on purpose: retrying will not make the booking exist, and staff
        # find it in the failed events list.
        return JsonResponse({"status": "unknown-booking"})
    except Exception:
        record.status = StripeEvent.Status.FAILED
        record.detail = "handler raised"
        record.save(update_fields=["status", "detail"])
        logger.exception("Stripe event %s failed", event_id)
        # 500 asks Stripe to retry, which is what we want for a transient fault.
        return HttpResponse(status=500)

    record.status = StripeEvent.Status.PROCESSED
    record.processed_at = timezone.now()
    record.save(update_fields=["status", "processed_at"])
    return JsonResponse({"status": "ok"})
