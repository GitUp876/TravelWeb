"""The payments-due report: what it counts, and who may see it."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.bookings import reports
from apps.bookings.models import Booking, PaymentPlan, ScheduledPayment, Traveller


@pytest.fixture
def confirmed_booking(departure, guest, price_option):
    def _make(paid=Decimal("0.00"), status=Booking.Status.CONFIRMED):
        booking = Booking.objects.create(
            departure=departure,
            guest=guest,
            status=status,
            total_amount=price_option.amount,
            amount_paid=paid,
        )
        Traveller.objects.create(booking=booking, full_name="Dana", price_option=price_option)
        return booking

    return _make


def _plan(booking, status=PaymentPlan.Status.ACTIVE):
    return PaymentPlan.objects.create(
        booking=booking, status=status, instalment_count=1, deposit_amount=Decimal("25.00")
    )


# --- What the report counts ------------------------------------------------


@pytest.mark.django_db
def test_an_overdue_instalment_is_listed_with_its_total(confirmed_booking):
    booking = confirmed_booking(paid=Decimal("25.00"))
    plan = _plan(booking)
    yesterday = timezone.localdate() - timedelta(days=1)
    ScheduledPayment.objects.create(plan=plan, due_date=yesterday, amount=Decimal("62.00"))

    data = reports.payments_due()
    assert list(data["overdue"])[0].amount == Decimal("62.00")
    assert data["overdue_total"] == Decimal("62.00")


@pytest.mark.django_db
def test_a_failed_instalment_still_needs_chasing(confirmed_booking):
    booking = confirmed_booking(paid=Decimal("25.00"))
    plan = _plan(booking, status=PaymentPlan.Status.FAILED)
    ScheduledPayment.objects.create(
        plan=plan,
        due_date=timezone.localdate() - timedelta(days=3),
        amount=Decimal("62.00"),
        status=ScheduledPayment.Status.FAILED,
    )

    assert reports.payments_due()["overdue_total"] == Decimal("62.00")


@pytest.mark.django_db
def test_a_cancelled_plan_is_not_chased(confirmed_booking):
    booking = confirmed_booking(paid=Decimal("25.00"))
    plan = _plan(booking, status=PaymentPlan.Status.CANCELLED)
    ScheduledPayment.objects.create(
        plan=plan,
        due_date=timezone.localdate() - timedelta(days=1),
        amount=Decimal("62.00"),
        status=ScheduledPayment.Status.CANCELLED,
    )

    assert reports.payments_due()["overdue_total"] == Decimal("0.00")


@pytest.mark.django_db
def test_an_unconfirmed_booking_is_not_chased(confirmed_booking):
    booking = confirmed_booking(status=Booking.Status.PENDING)
    plan = _plan(booking)
    ScheduledPayment.objects.create(
        plan=plan, due_date=timezone.localdate() - timedelta(days=1), amount=Decimal("62.00")
    )

    assert reports.payments_due()["overdue_total"] == Decimal("0.00")


@pytest.mark.django_db
def test_upcoming_respects_the_horizon(confirmed_booking):
    booking = confirmed_booking(paid=Decimal("25.00"))
    plan = _plan(booking)
    today = timezone.localdate()
    ScheduledPayment.objects.create(
        plan=plan, due_date=today + timedelta(days=5), amount=Decimal("10.00")
    )
    ScheduledPayment.objects.create(
        plan=plan, due_date=today + timedelta(days=90), amount=Decimal("20.00")
    )

    near = reports.payments_due(horizon_days=30)
    assert near["upcoming_total"] == Decimal("10.00")
    far = reports.payments_due(horizon_days=120)
    assert far["upcoming_total"] == Decimal("30.00")


@pytest.mark.django_db
def test_a_balance_with_no_plan_is_flagged(confirmed_booking):
    booking = confirmed_booking(paid=Decimal("100.00"))  # total is 149.00

    data = reports.payments_due()
    assert list(data["unplanned"]) == [booking]
    assert data["unplanned_total"] == Decimal("49.00")


@pytest.mark.django_db
def test_a_booking_on_a_live_plan_is_not_flagged_as_loose(confirmed_booking):
    booking = confirmed_booking(paid=Decimal("25.00"))
    _plan(booking)

    assert list(reports.payments_due()["unplanned"]) == []


@pytest.mark.django_db
def test_a_fully_paid_booking_owes_nothing(confirmed_booking):
    confirmed_booking(paid=Decimal("149.00"))

    data = reports.payments_due()
    assert list(data["unplanned"]) == []
    assert data["unplanned_total"] == Decimal("0.00")


def test_the_horizon_from_a_query_string_is_clamped():
    assert reports.clamp_horizon("7") == 7
    assert reports.clamp_horizon("0") == 1
    assert reports.clamp_horizon("99999") == reports.MAX_HORIZON_DAYS
    assert reports.clamp_horizon("drop table") == reports.DEFAULT_HORIZON_DAYS
    assert reports.clamp_horizon(None) == reports.DEFAULT_HORIZON_DAYS


# --- Who may see it --------------------------------------------------------


def _staff_user(**extra):
    return get_user_model().objects.create_user(
        email=extra.pop("email", "staff@example.com"),
        password="not-a-real-password",
        is_staff=True,
        **extra,
    )


def _verify(client, user):
    """Signs in with a confirmed TOTP device, as the admin site demands."""
    device = TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()


@pytest.mark.django_db
def test_the_report_is_closed_to_anyone_not_signed_in(client):
    response = client.get(reverse("admin:payments-due"))
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_a_staff_member_without_the_one_time_code_cannot_open_it(client):
    user = _staff_user()
    client.force_login(user)  # password only, no verified device
    response = client.get(reverse("admin:payments-due"))
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_a_verified_staff_member_without_the_permission_is_refused(client):
    user = _staff_user()
    _verify(client, user)
    assert client.get(reverse("admin:payments-due")).status_code == 403


@pytest.mark.django_db
def test_a_permitted_staff_member_sees_the_figures(client, confirmed_booking):
    booking = confirmed_booking(paid=Decimal("25.00"))
    plan = _plan(booking)
    ScheduledPayment.objects.create(
        plan=plan, due_date=timezone.localdate() - timedelta(days=1), amount=Decimal("62.00")
    )

    user = _staff_user()
    user.user_permissions.add(Permission.objects.get(codename="view_scheduledpayment"))
    _verify(client, user)

    response = client.get(reverse("admin:payments-due"))
    assert response.status_code == 200
    body = response.content.decode()
    assert booking.reference in body
    assert "62.00" in body
