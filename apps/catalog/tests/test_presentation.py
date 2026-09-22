"""The redesigned public pages: photos, fallbacks, and the CSP-safe markup."""

import io
import re
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import inlineformset_factory
from django.test import RequestFactory
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from apps.catalog.models import SiteImage, Trip, TripImage
from apps.core.models import AuditEvent


def upload(name="photo.jpg"):
    out = io.BytesIO()
    Image.new("RGB", (1200, 800), (30, 90, 120)).save(out, format="JPEG")
    return SimpleUploadedFile(name, out.getvalue(), "image/jpeg")


def public_pages(client, departure):
    return {
        "home": client.get(reverse("catalog:home")),
        "category": client.get(reverse("catalog:category", args=[departure.trip.category])),
        "trip": client.get(departure.trip.get_absolute_url()),
        "departure": client.get(departure.get_absolute_url()),
        "book": client.get(reverse("bookings:book", args=[departure.pk])),
        "find": client.get(reverse("bookings:find")),
        "missing": client.get("/no-such-page/"),
    }


@pytest.mark.django_db
def test_every_public_page_renders_without_inline_script_or_style(
    client, departure, price_option, pickup
):
    # The content security policy forbids both, so either would silently
    # break the page in a real browser.
    for name, response in public_pages(client, departure).items():
        assert response.status_code == (404 if name == "missing" else 200), name
        body = response.content.decode()
        assert not re.search(r"\sstyle\s*=", body), f"inline style on {name}"
        assert "<style" not in body, f"style element on {name}"
        assert "<script" not in body, f"script on {name}"


@pytest.mark.django_db
def test_a_trip_without_photos_shows_its_category_illustration(client, departure):
    body = client.get(departure.trip.get_absolute_url()).content.decode()
    assert f"img/placeholders/{departure.trip.category}.svg" in body


@pytest.mark.django_db
def test_a_trip_photo_and_gallery_appear_on_the_trip_page(client, departure):
    trip = departure.trip
    trip.hero_image = upload()
    trip.hero_image_alt = "The Breakers from the lawn"
    trip.save()
    photo = TripImage.objects.create(
        trip=trip, image=upload(), alt_text="Marble House ballroom", caption="The ballroom"
    )

    body = client.get(trip.get_absolute_url()).content.decode()
    assert trip.hero_image.url in body
    assert 'alt="The Breakers from the lawn"' in body
    assert photo.image_card.url in body  # gallery thumbnail
    assert photo.image.url in body  # full size in the lightbox
    assert "The ballroom" in body
    assert f"img/placeholders/{trip.category}.svg" not in body

    listing = client.get(reverse("catalog:home")).content.decode()
    assert trip.hero_image_card.url in listing  # cards use the smaller copy


@pytest.mark.django_db
def test_a_category_photo_is_used_for_the_category_and_its_unphotographed_trips(client, departure):
    category = departure.trip.category
    photo = SiteImage.objects.create(slot=category, image=upload(), alt_text="Harbour")
    body = client.get(reverse("catalog:category", args=[category])).content.decode()
    assert photo.image.url in body
    assert photo.image_card.url in body


@pytest.mark.django_db
def test_the_phone_number_link_carries_only_digits(client, settings, db):
    settings.SITE_PHONE = '1 (800) 555-0142"><b>'
    body = client.get(reverse("catalog:home")).content.decode()
    assert 'href="tel:18005550142"' in body
    assert "<b>" not in body


# --- Staff side ------------------------------------------------------------


def _manager(client):
    user = get_user_model().objects.create_user(
        email="manager@example.com", password="not-a-real-password", is_staff=True
    )
    user.is_superuser = True
    user.save()
    device = TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return user


@pytest.mark.django_db
def test_staff_can_upload_a_site_photo_through_the_admin(client):
    _manager(client)
    response = client.post(
        reverse("admin:catalog_siteimage_add"),
        {"slot": SiteImage.Slot.HOME_HERO, "alt_text": "A coach at sunrise", "image": upload()},
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    photo = SiteImage.objects.get()
    assert photo.image.name.endswith(".jpg") and photo.image_card
    assert AuditEvent.objects.filter(
        object_type="catalog.SiteImage", action=AuditEvent.Action.CREATE
    ).exists()


@pytest.mark.django_db
def test_the_admin_refuses_a_file_that_is_not_a_photo(client):
    _manager(client)
    fake = SimpleUploadedFile("photo.jpg", b"<script>alert(1)</script>", "image/jpeg")
    response = client.post(
        reverse("admin:catalog_siteimage_add"),
        {"slot": SiteImage.Slot.HOME_HERO, "alt_text": "x", "image": fake},
    )
    assert response.status_code == 200
    assert not SiteImage.objects.exists()


@pytest.mark.django_db
def test_the_trip_form_shows_the_current_photo(client, trip):
    _manager(client)
    trip.hero_image = upload()
    trip.save()
    body = client.get(reverse("admin:catalog_trip_change", args=[trip.pk])).content.decode()
    assert trip.hero_image_card.url in body
    assert "admin-brand.css" in body


@pytest.mark.django_db
def test_photos_added_inline_are_audited(trip):
    user = get_user_model().objects.create_user(email="m@example.com", password="x" * 16)
    formset_class = inlineformset_factory(Trip, TripImage, fields=("image", "alt_text"), extra=1)
    formset = formset_class(
        data={
            "images-TOTAL_FORMS": "1",
            "images-INITIAL_FORMS": "0",
            "images-0-alt_text": "Harbour",
        },
        files={"images-0-image": upload()},
        instance=trip,
        prefix="images",
    )
    assert formset.is_valid(), formset.errors
    request = RequestFactory().post("/")
    request.user = user
    admin.site._registry[Trip].save_formset(
        request, SimpleNamespace(instance=trip), formset, change=True
    )
    event = AuditEvent.objects.get(object_type="catalog.TripImage")
    assert event.action == AuditEvent.Action.CREATE
    assert event.changes["parent"] == trip.title
