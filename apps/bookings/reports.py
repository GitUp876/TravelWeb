"""What money is owed, and when.

Read-only aggregation for staff: which instalments have fallen overdue, what
falls due in the next few weeks, and which confirmed bookings still owe a
balance with nothing scheduled to collect it. Nothing here writes; every figure
is derived from the booking, plan and instalment rows.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Booking, PaymentPlan, ScheduledPayment

DEFAULT_HORIZON_DAYS = 30
MAX_HORIZON_DAYS = 365

# A plan staff would still chase: running normally, or stalled on a failure.
CHASEABLE_PLAN_STATUSES = (PaymentPlan.Status.ACTIVE, PaymentPlan.Status.FAILED)

MONEY = DecimalField(max_digits=12, decimal_places=2)


def _instalments():
    """Instalments of confirmed bookings, with everything the report displays."""
    return ScheduledPayment.objects.select_related(
        "plan",
        "plan__booking",
        "plan__booking__guest",
        "plan__booking__departure__trip",
    ).filter(plan__booking__status=Booking.Status.CONFIRMED)


def overdue_instalments(as_of: date | None = None):
    """Instalments past their due date that have still not been collected.

    Includes failed ones: a declined card is exactly what staff need to chase.
    """
    as_of = as_of or timezone.localdate()
    return (
        _instalments()
        .filter(
            due_date__lt=as_of,
            status__in=[ScheduledPayment.Status.SCHEDULED, ScheduledPayment.Status.FAILED],
            plan__status__in=CHASEABLE_PLAN_STATUSES,
        )
        .order_by("due_date", "pk")
    )


def upcoming_instalments(as_of: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS):
    """Instalments falling due between today and the end of the horizon."""
    as_of = as_of or timezone.localdate()
    return (
        _instalments()
        .filter(
            due_date__gte=as_of,
            due_date__lte=as_of + timedelta(days=horizon_days),
            status=ScheduledPayment.Status.SCHEDULED,
            plan__status=PaymentPlan.Status.ACTIVE,
        )
        .order_by("due_date", "pk")
    )


def balances_without_a_plan():
    """Confirmed bookings still owing money with no live plan collecting it.

    These are the ones nothing will chase automatically: a paid-in-full booking
    that was only part paid, or one whose plan was cancelled or completed while
    a balance remained.
    """
    return (
        Booking.objects.filter(
            status=Booking.Status.CONFIRMED,
            amount_paid__lt=F("total_amount"),
        )
        .filter(Q(payment_plan__isnull=True) | ~Q(payment_plan__status__in=CHASEABLE_PLAN_STATUSES))
        .select_related("guest", "departure__trip")
        .order_by("departure__start_date", "reference")
    )


def _sum(queryset, expression) -> Decimal:
    return queryset.aggregate(
        total=Coalesce(
            Sum(expression, output_field=MONEY), Value(Decimal("0.00")), output_field=MONEY
        )
    )["total"]


def clamp_horizon(value, default: int = DEFAULT_HORIZON_DAYS) -> int:
    """Keeps a horizon taken from a query string sane."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(days, MAX_HORIZON_DAYS))


def payments_due(as_of: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict:
    """Everything the payments-due report shows, in one call."""
    as_of = as_of or timezone.localdate()
    overdue = overdue_instalments(as_of)
    upcoming = upcoming_instalments(as_of, horizon_days)
    unplanned = balances_without_a_plan()

    return {
        "as_of": as_of,
        "horizon_days": horizon_days,
        "horizon_end": as_of + timedelta(days=horizon_days),
        "overdue": overdue,
        "upcoming": upcoming,
        "unplanned": unplanned,
        "overdue_total": _sum(overdue, "amount"),
        "upcoming_total": _sum(upcoming, "amount"),
        "unplanned_total": _sum(unplanned, F("total_amount") - F("amount_paid")),
    }
