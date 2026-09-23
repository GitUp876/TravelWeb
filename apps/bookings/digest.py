"""The daily "needs attention" email to Managers.

Without it, a declined instalment or a webhook Stripe could not deliver shows up
only on a staff page somebody has to remember to open. The email lists booking
references and amounts, never guest names, addresses or notes: staff look the
booking up in the admin, which is where personal details belong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import MANAGER
from apps.payments.models import StripeEvent

from . import reports
from .models import Booking, PaymentPlan

LOOKBACK = timedelta(days=1)


@dataclass
class Digest:
    failed_plans: list = field(default_factory=list)
    overdue: list = field(default_factory=list)
    overdue_total: Decimal = Decimal("0.00")
    money_on_dead_bookings: list = field(default_factory=list)
    overpaid: list = field(default_factory=list)
    failed_events: list = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return (
            len(self.failed_plans)
            + len(self.overdue)
            + len(self.money_on_dead_bookings)
            + len(self.overpaid)
            + len(self.failed_events)
        )


def build_digest(now=None) -> Digest:
    now = now or timezone.now()
    today = timezone.localdate(now)
    overdue = list(reports.overdue_instalments(today))
    return Digest(
        failed_plans=list(
            PaymentPlan.objects.filter(
                status=PaymentPlan.Status.FAILED, booking__status=Booking.Status.CONFIRMED
            )
            .select_related("booking")
            .order_by("booking__reference")
        ),
        overdue=overdue,
        overdue_total=sum((sp.amount for sp in overdue), Decimal("0.00")),
        # Money still held against a booking nobody is travelling on: each one
        # is a refund decision waiting for a Manager.
        money_on_dead_bookings=list(
            Booking.objects.filter(
                status__in=[Booking.Status.CANCELLED, Booking.Status.EXPIRED],
                amount_paid__gt=0,
            ).order_by("reference")
        ),
        overpaid=list(
            Booking.objects.filter(
                status=Booking.Status.CONFIRMED, amount_paid__gt=F("total_amount")
            ).order_by("reference")
        ),
        failed_events=list(
            StripeEvent.objects.filter(
                status=StripeEvent.Status.FAILED, received_at__gte=now - LOOKBACK
            ).order_by("received_at")
        ),
    )


def recipients() -> list[str]:
    """Active Managers and Owners with an address, else the error-alert list."""
    users = (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(groups__name=MANAGER))
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    addresses = sorted(set(users))
    return addresses or [address for _, address in settings.ADMINS]


def send_digest(*, always: bool = False, now=None) -> int:
    """Sends the digest if anything needs attention. Returns how many it went to."""
    digest = build_digest(now)
    if digest.item_count == 0 and not always:
        return 0
    to = recipients()
    if not to:
        return 0
    context = {
        "digest": digest,
        "site_name": settings.SITE_NAME,
        "report_url": settings.SITE_BASE_URL + reverse("admin:payments-due"),
    }
    count = digest.item_count
    subject = (
        f"{settings.SITE_NAME}: {count} thing{' needs' if count == 1 else 's need'} attention"
        if count
        else f"{settings.SITE_NAME}: nothing needs attention today"
    )
    body = render_to_string("bookings/email/staff_digest.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    return len(to)
