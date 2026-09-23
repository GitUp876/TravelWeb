"""The daily email that tells Managers what needs a person."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.roles import MANAGER, STAFF
from apps.bookings.digest import build_digest, recipients, send_digest
from apps.bookings.models import Booking, PaymentPlan, ScheduledPayment, Traveller
from apps.payments.models import StripeEvent


@pytest.fixture
def booking(departure, guest, price_option):
    def _make(paid=Decimal("0.00"), status=Booking.Status.CONFIRMED):
        made = Booking.objects.create(
            departure=departure,
            guest=guest,
            status=status,
            total_amount=price_option.amount,
            amount_paid=paid,
        )
        Traveller.objects.create(
            booking=made,
            full_name="Dana",
            price_option=price_option,
            dietary_notes="severe nut allergy",
        )
        return made

    return _make


@pytest.fixture
def manager(db):
    user = get_user_model().objects.create_user("manager@example.com", "x" * 16, is_staff=True)
    user.groups.add(Group.objects.get_or_create(name=MANAGER)[0])
    return user


def _failed_plan(booking):
    plan = PaymentPlan.objects.create(
        booking=booking,
        status=PaymentPlan.Status.FAILED,
        instalment_count=1,
        deposit_amount=Decimal("25.00"),
    )
    ScheduledPayment.objects.create(
        plan=plan,
        due_date=timezone.localdate() - timedelta(days=2),
        amount=Decimal("124.00"),
        status=ScheduledPayment.Status.FAILED,
    )
    return plan


@pytest.mark.django_db
def test_a_quiet_day_sends_nothing(manager):
    assert send_digest() == 0
    assert mail.outbox == []


@pytest.mark.django_db
def test_a_declined_plan_is_reported_by_reference_only(booking, manager):
    held = booking(paid=Decimal("25.00"))
    _failed_plan(held)

    assert send_digest() == 1
    message = mail.outbox[0]
    assert message.to == ["manager@example.com"]
    assert "need attention" in message.subject
    assert held.reference in message.body
    assert "$124.00" in message.body
    # References and amounts only: no names, addresses or health notes.
    assert held.guest.full_name not in message.body
    assert held.guest.email not in message.body
    assert "nut allergy" not in message.body


@pytest.mark.django_db
def test_money_on_a_cancelled_booking_is_a_refund_decision(booking):
    cancelled = booking(paid=Decimal("149.00"), status=Booking.Status.CANCELLED)
    digest = build_digest()
    assert digest.money_on_dead_bookings == [cancelled]


@pytest.mark.django_db
def test_an_overpaid_booking_is_listed(booking):
    overpaid = booking(paid=Decimal("200.00"))
    assert build_digest().overpaid == [overpaid]


@pytest.mark.django_db
def test_a_failed_stripe_event_from_today_is_listed_and_an_old_one_is_not():
    recent = StripeEvent.objects.create(
        event_id="evt_new", event_type="checkout.session.completed", status="failed"
    )
    old = StripeEvent.objects.create(event_id="evt_old", event_type="x", status="failed")
    StripeEvent.objects.filter(pk=old.pk).update(received_at=timezone.now() - timedelta(days=3))
    assert build_digest().failed_events == [recent]


@pytest.mark.django_db
def test_it_goes_to_managers_and_owners_not_staff(manager):
    User = get_user_model()
    User.objects.create_superuser("owner@example.com", "x" * 16)
    clerk = User.objects.create_user("clerk@example.com", "x" * 16, is_staff=True)
    clerk.groups.add(Group.objects.get_or_create(name=STAFF)[0])
    User.objects.create_user("gone@example.com", "x" * 16, is_active=False).groups.add(
        Group.objects.get(name=MANAGER)
    )
    assert recipients() == ["manager@example.com", "owner@example.com"]


@pytest.mark.django_db
def test_with_no_managers_it_falls_back_to_the_alert_list(settings):
    settings.ADMINS = [("", "ops@example.com")]
    assert recipients() == ["ops@example.com"]


@pytest.mark.django_db
def test_the_command_can_send_a_test_email(manager):
    call_command("send_staff_digest", "--always")
    assert len(mail.outbox) == 1
    assert "nothing needs attention" in mail.outbox[0].subject
