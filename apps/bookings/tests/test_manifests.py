"""The passenger list: who is on it, how it groups, and what never leaves in it."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.bookings import manifests
from apps.bookings.models import Booking, Traveller
from apps.catalog.models import DeparturePickup, PickupPoint
from apps.core.models import AuditEvent


@pytest.fixture
def second_pickup(departure):
    point = PickupPoint.objects.create(
        name="Vernon Commuter Lot", address="10 Dart Hill Rd", city="Vernon", region="CT"
    )
    return DeparturePickup.objects.create(
        departure=departure, pickup_point=point, boarding_time="06:15", display_order=0
    )


def _traveller(booking, price_option, name, pickup=None, **extra):
    return Traveller.objects.create(
        booking=booking, full_name=name, price_option=price_option, pickup=pickup, **extra
    )


# --- Who appears -----------------------------------------------------------


@pytest.mark.django_db
def test_the_manifest_lists_travellers_holding_seats(departure, make_booking, price_option):
    confirmed = make_booking(status=Booking.Status.CONFIRMED, travellers=2)
    holding = make_booking(status=Booking.Status.PENDING, travellers=1, hold_minutes=20)

    listed = list(manifests.manifest_travellers(departure))
    assert len(listed) == 3
    booking_ids = {traveller.booking_id for traveller in listed}
    assert booking_ids == {confirmed.pk, holding.pk}


@pytest.mark.django_db
def test_an_expired_or_cancelled_booking_is_not_on_the_coach(departure, make_booking):
    make_booking(status=Booking.Status.CONFIRMED, travellers=1)
    make_booking(status=Booking.Status.CANCELLED, travellers=1)
    make_booking(status=Booking.Status.EXPIRED, travellers=1)
    make_booking(status=Booking.Status.PENDING, travellers=1, hold_minutes=-5)

    assert manifests.manifest(departure)["headcount"] == 1


@pytest.mark.django_db
def test_travellers_group_by_boarding_point_in_boarding_order(
    departure, make_booking, price_option, pickup, second_pickup
):
    booking = make_booking(status=Booking.Status.CONFIRMED, travellers=0)
    _traveller(booking, price_option, "Later Stop", pickup=pickup)
    _traveller(booking, price_option, "First Stop", pickup=second_pickup)
    _traveller(booking, price_option, "No Stop")

    groups = manifests.manifest(departure)["groups"]
    assert [group.label for group in groups] == [
        "Vernon Commuter Lot, Vernon",
        "Manchester Park & Ride, Manchester",
        "No boarding point set",
    ]
    assert groups[0].headcount == 1
    assert groups[-1].pickup is None


@pytest.mark.django_db
def test_the_notes_that_need_attention_are_collected(departure, make_booking, price_option):
    booking = make_booking(status=Booking.Status.CONFIRMED, travellers=0)
    _traveller(booking, price_option, "Needs A Chair", mobility_notes="Uses a walker")
    _traveller(booking, price_option, "Coeliac", dietary_notes="Gluten free")
    _traveller(booking, price_option, "No Notes")

    attention = manifests.manifest(departure)["needs_attention"]
    assert {t.full_name for t in attention} == {"Needs A Chair", "Coeliac"}


# --- What never leaves in the file ----------------------------------------


@pytest.mark.django_db
def test_the_csv_leaves_out_the_health_notes(departure, make_booking, price_option, pickup):
    booking = make_booking(status=Booking.Status.CONFIRMED, travellers=0)
    _traveller(
        booking,
        price_option,
        "Coeliac Traveller",
        pickup=pickup,
        dietary_notes="Severe gluten allergy",
        mobility_notes="Uses a walker",
        emergency_contact_name="Sam Reyes",
        emergency_contact_phone="555-0199",
    )

    csv_text = manifests.manifest_csv(departure)

    assert "Coeliac Traveller" in csv_text
    assert "Sam Reyes" in csv_text
    assert "Manchester Park & Ride" in csv_text
    # The whole point: health-adjacent notes are not in a file that gets emailed.
    assert "gluten" not in csv_text.lower()
    assert "walker" not in csv_text.lower()
    assert "Dietary" not in csv_text
    assert "Mobility" not in csv_text


# --- Who may open it -------------------------------------------------------


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
def test_the_manifest_is_closed_to_anyone_not_signed_in(client, departure):
    response = client.get(reverse("admin:departure-manifest", args=[departure.pk]))
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_a_verified_staff_member_without_the_permission_is_refused(client, departure):
    _verify(client, _staff_user())
    url = reverse("admin:departure-manifest", args=[departure.pk])
    assert client.get(url).status_code == 403
    csv_url = reverse("admin:departure-manifest-csv", args=[departure.pk])
    assert client.get(csv_url).status_code == 403


@pytest.mark.django_db
def test_a_permitted_staff_member_sees_the_notes_on_the_page(
    client, departure, make_booking, price_option, pickup
):
    booking = make_booking(status=Booking.Status.CONFIRMED, travellers=0)
    _traveller(
        booking, price_option, "Coeliac Traveller", pickup=pickup, dietary_notes="Gluten free"
    )

    user = _staff_user()
    user.user_permissions.add(Permission.objects.get(codename="view_traveller"))
    _verify(client, user)

    response = client.get(reverse("admin:departure-manifest", args=[departure.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Coeliac Traveller" in body
    assert "Gluten free" in body  # the manifest is where these belong


@pytest.mark.django_db
def test_downloading_the_csv_is_written_to_the_audit_trail(
    client, departure, make_booking, price_option
):
    make_booking(status=Booking.Status.CONFIRMED, travellers=1)
    user = _staff_user()
    user.user_permissions.add(Permission.objects.get(codename="view_traveller"))
    _verify(client, user)

    response = client.get(reverse("admin:departure-manifest-csv", args=[departure.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    event = AuditEvent.objects.get(action=AuditEvent.Action.EXPORT)
    assert event.actor == user
    assert event.changes["export"] == "manifest-csv"
