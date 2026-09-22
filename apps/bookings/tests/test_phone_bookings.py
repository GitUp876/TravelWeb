"""Bookings taken over the phone by staff, and the money taken with them."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.bookings.models import Booking, Payment
from apps.bookings.services import (
    DepartureNotBookable,
    InvalidPayment,
    SeatsUnavailable,
    create_pending_booking,
    record_offline_payment,
)
from apps.catalog.models import Departure


def _traveller(price_option, pickup=None, name="Dana Reyes"):
    return {
        "full_name": name,
        "price_option": price_option,
        "pickup": pickup,
        "emergency_contact_name": "Sam Reyes",
        "emergency_contact_phone": "555-0199",
    }


GUEST = {
    "full_name": "Dana Reyes",
    "email": "dana@example.com",
    "phone": "555-0100",
}


# --- Reaching the held-back seats -----------------------------------------


@pytest.mark.django_db
def test_staff_can_book_into_the_seats_held_back_from_the_website(departure, price_option):
    # 10 seats, 2 held back: the website sees 8, staff see 10.
    assert departure.seats_available == 8
    assert departure.seats_available_to_staff == 10

    booking = create_pending_booking(
        departure=departure,
        guest_data=GUEST,
        travellers_data=[_traveller(price_option) for _ in range(10)],
        source=Booking.Source.PHONE,
        for_staff=True,
    )

    assert booking.travellers.count() == 10
    assert booking.source == Booking.Source.PHONE
    departure.refresh_from_db()
    assert departure.seats_available_to_staff == 0


@pytest.mark.django_db
def test_the_website_still_cannot_reach_the_held_back_seats(departure, price_option):
    with pytest.raises(SeatsUnavailable):
        create_pending_booking(
            departure=departure,
            guest_data=GUEST,
            travellers_data=[_traveller(price_option) for _ in range(9)],
        )


@pytest.mark.django_db
def test_staff_can_book_a_date_the_website_has_closed(departure, price_option):
    departure.status = Departure.Status.CLOSED
    departure.save()

    assert not departure.is_bookable
    assert departure.is_bookable_by_staff

    booking = create_pending_booking(
        departure=departure,
        guest_data=GUEST,
        travellers_data=[_traveller(price_option)],
        source=Booking.Source.PHONE,
        for_staff=True,
    )
    assert booking.pk


@pytest.mark.django_db
def test_staff_cannot_book_a_cancelled_date(departure, price_option):
    departure.status = Departure.Status.CANCELLED
    departure.save()

    with pytest.raises(DepartureNotBookable):
        create_pending_booking(
            departure=departure,
            guest_data=GUEST,
            travellers_data=[_traveller(price_option)],
            source=Booking.Source.PHONE,
            for_staff=True,
        )


@pytest.mark.django_db
@override_settings(STAFF_HOLD_DAYS=7)
def test_a_phone_booking_holds_its_seats_for_days_not_minutes(departure, price_option):
    booking = create_pending_booking(
        departure=departure,
        guest_data=GUEST,
        travellers_data=[_traveller(price_option)],
        source=Booking.Source.PHONE,
        for_staff=True,
    )
    assert booking.hold_expires_at > timezone.now() + timedelta(days=6)


# --- Money taken by hand ---------------------------------------------------


@pytest.mark.django_db
def test_recording_a_cheque_confirms_the_booking(departure, price_option):
    booking = create_pending_booking(
        departure=departure,
        guest_data=GUEST,
        travellers_data=[_traveller(price_option)],
        source=Booking.Source.PHONE,
        for_staff=True,
    )
    staff = get_user_model().objects.create_user(email="s@example.com", is_staff=True)

    payment = record_offline_payment(
        booking=booking,
        amount=Decimal("149.00"),
        method=Payment.Method.CHEQUE,
        taken_by=staff,
        reference="chq 4471",
    )

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.hold_expires_at is None
    assert booking.amount_paid == Decimal("149.00")
    assert payment.kind == Payment.Kind.FULL
    assert payment.taken_by == staff
    assert payment.offline_reference == "chq 4471"


@pytest.mark.django_db
def test_a_part_payment_confirms_the_booking_and_leaves_a_balance(departure, price_option):
    booking = create_pending_booking(
        departure=departure,
        guest_data=GUEST,
        travellers_data=[_traveller(price_option)],
        source=Booking.Source.PHONE,
        for_staff=True,
    )
    staff = get_user_model().objects.create_user(email="s@example.com", is_staff=True)

    payment = record_offline_payment(
        booking=booking, amount=Decimal("50.00"), method=Payment.Method.CASH, taken_by=staff
    )

    booking.refresh_from_db()
    assert payment.kind == Payment.Kind.DEPOSIT
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.balance == Decimal("99.00")


@pytest.mark.django_db
def test_a_card_payment_cannot_be_recorded_by_hand(departure, price_option, make_booking):
    booking = make_booking()
    staff = get_user_model().objects.create_user(email="s@example.com", is_staff=True)

    with pytest.raises(InvalidPayment, match="not a payment staff can record"):
        record_offline_payment(
            booking=booking,
            amount=Decimal("10.00"),
            method=Payment.Method.CARD,
            taken_by=staff,
        )
    assert not Payment.objects.exists()


@pytest.mark.django_db
def test_a_zero_or_negative_payment_is_refused(make_booking):
    booking = make_booking()
    staff = get_user_model().objects.create_user(email="s@example.com", is_staff=True)

    for amount in (Decimal("0.00"), Decimal("-5.00")):
        with pytest.raises(InvalidPayment):
            record_offline_payment(
                booking=booking, amount=amount, method=Payment.Method.CASH, taken_by=staff
            )
    assert not Payment.objects.exists()


@pytest.mark.django_db
def test_a_cancelled_booking_takes_no_more_money(make_booking):
    booking = make_booking(status=Booking.Status.CANCELLED)
    staff = get_user_model().objects.create_user(email="s@example.com", is_staff=True)

    with pytest.raises(InvalidPayment, match="no longer live"):
        record_offline_payment(
            booking=booking, amount=Decimal("10.00"), method=Payment.Method.CASH, taken_by=staff
        )


# --- The staff page --------------------------------------------------------


def _staff_user(**extra):
    return get_user_model().objects.create_user(
        email=extra.pop("email", "staff@example.com"),
        password="not-a-real-password",
        is_staff=True,
        **extra,
    )


def _verify(client, user):
    device = TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()


@pytest.mark.django_db
def test_the_phone_booking_page_is_closed_to_anyone_not_signed_in(client):
    response = client.get(reverse("admin:phone-booking"))
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_a_verified_staff_member_without_the_permission_is_refused(client):
    _verify(client, _staff_user())
    assert client.get(reverse("admin:phone-booking")).status_code == 403


@pytest.mark.django_db
def test_staff_take_a_booking_and_the_money_through_the_page(
    client, departure, price_option, pickup
):
    user = _staff_user()
    user.user_permissions.add(Permission.objects.get(codename="add_booking"))
    _verify(client, user)

    url = reverse("admin:phone-booking")
    response = client.post(
        f"{url}?departure={departure.pk}&party=1",
        {
            "full_name": "Dana Reyes",
            "email": "dana@example.com",
            "phone": "555-0100",
            "address_line1": "12 Elm Street",
            "city": "Hartford",
            "postal_code": "06103",
            "t0-full_name": "Dana Reyes",
            "t0-price_option": str(price_option.pk),
            "t0-pickup": str(pickup.pk),
            "t0-emergency_contact_name": "Sam Reyes",
            "t0-emergency_contact_phone": "555-0199",
            "amount": "149.00",
            "method": Payment.Method.CHEQUE,
            "reference": "chq 4471",
        },
    )

    assert response.status_code == 302
    booking = Booking.objects.get()
    assert booking.source == Booking.Source.PHONE
    assert booking.created_by == user
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.total_amount == Decimal("149.00")  # from our prices, not the post
    payment = booking.payments.get()
    assert payment.method == Payment.Method.CHEQUE
    assert payment.taken_by == user


@pytest.mark.django_db
def test_a_payment_amount_without_a_method_is_rejected(client, departure, price_option, pickup):
    user = _staff_user()
    user.user_permissions.add(Permission.objects.get(codename="add_booking"))
    _verify(client, user)

    url = reverse("admin:phone-booking")
    response = client.post(
        f"{url}?departure={departure.pk}&party=1",
        {
            "full_name": "Dana Reyes",
            "email": "dana@example.com",
            "phone": "555-0100",
            "address_line1": "12 Elm Street",
            "city": "Hartford",
            "postal_code": "06103",
            "t0-full_name": "Dana Reyes",
            "t0-price_option": str(price_option.pk),
            "t0-pickup": str(pickup.pk),
            "t0-emergency_contact_name": "Sam Reyes",
            "t0-emergency_contact_phone": "555-0199",
            "amount": "149.00",
            "method": "",
        },
    )

    assert response.status_code == 200  # re-rendered with the error
    assert not Booking.objects.exists()
