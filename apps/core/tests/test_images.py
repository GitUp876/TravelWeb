"""Uploaded photos: what is refused, what is stored, and how it is served."""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from apps.catalog.models import SiteImage, Trip, TripImage
from apps.core import images


def make_image(fmt="JPEG", size=(1200, 800), mode="RGB", exif=None) -> bytes:
    image = Image.new(mode, size, (200, 120, 60) if mode == "RGB" else (200, 120, 60, 128))
    out = io.BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def upload(name="photo.jpg", data=None, content_type="image/jpeg") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data if data is not None else make_image(), content_type)


class Fresh:
    """Stands in for a just-uploaded FieldFile: the validator only checks those."""

    _committed = False

    def __init__(self, file):
        self.file, self.name, self.size = file, file.name, file.size

    def seek(self, *args):
        return self.file.seek(*args)

    def read(self, *args):
        return self.file.read(*args)

    def tell(self):
        return self.file.tell()


def check(file):
    images.validate_image_upload(Fresh(file))


# --- What is refused -------------------------------------------------------


def test_a_normal_photo_is_accepted():
    check(upload())
    check(upload("photo.png", make_image("PNG"), "image/png"))
    check(upload("photo.webp", make_image("WEBP"), "image/webp"))


def test_svg_is_refused_because_it_can_carry_script():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(ValidationError) as exc:
        check(upload("logo.svg", svg, "image/svg+xml"))
    assert exc.value.code == "bad_extension"


def test_a_file_that_only_claims_to_be_a_photo_is_refused():
    with pytest.raises(ValidationError) as exc:
        check(upload("photo.jpg", b"<html><script>alert(1)</script></html>"))
    assert exc.value.code == "unreadable"


def test_the_format_is_judged_by_content_not_by_name():
    gif = io.BytesIO()
    Image.new("RGB", (1200, 800)).save(gif, format="GIF")
    with pytest.raises(ValidationError) as exc:
        check(upload("photo.jpg", gif.getvalue()))
    assert exc.value.code == "bad_format"


@override_settings(IMAGE_UPLOAD_MAX_BYTES=1024)
def test_an_oversized_file_is_refused():
    with pytest.raises(ValidationError) as exc:
        check(upload())
    assert exc.value.code == "too_big"


def test_a_tiny_photo_is_refused_as_too_blurry():
    with pytest.raises(ValidationError) as exc:
        check(upload(data=make_image(size=(300, 200))))
    assert exc.value.code == "too_small"


def test_an_image_that_would_decode_to_an_enormous_bitmap_is_refused(monkeypatch):
    # A decompression bomb is a small file with huge dimensions; the cap is
    # checked from the header, before any pixels are decoded.
    monkeypatch.setattr(images, "MAX_PIXELS", 1000 * 700)
    with pytest.raises(ValidationError) as exc:
        check(upload())
    assert exc.value.code == "too_many_pixels"


def test_an_already_stored_file_is_not_re_read():
    class Stored:
        _committed = True

    images.validate_image_upload(Stored())  # would fail if it tried to open it


# --- What is stored --------------------------------------------------------


def test_the_stored_copy_carries_no_metadata():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x8825] = {2: (41.0, 45.0, 0.0), 4: (72.0, 30.0, 0.0)}  # GPS position
    source = upload(data=make_image(exif=exif.tobytes()))
    with Image.open(source) as original:
        assert original.getexif()  # the upload did carry it

    rendered = images.render_jpeg(source, images.FULL_SIZE)
    with Image.open(io.BytesIO(rendered.read())) as clean:
        assert clean.format == "JPEG"
        assert not clean.getexif()
        assert "exif" not in clean.info


def test_the_stored_copy_is_a_resized_jpeg_with_a_random_name():
    source = upload("My Holiday Snaps.png", make_image("PNG", (3000, 2000), "RGBA"), "image/png")
    rendered = images.render_jpeg(source, images.CARD_SIZE)
    assert rendered.name.endswith(".jpg")
    assert "Holiday" not in rendered.name
    assert len(rendered.name) == 32 + len(".jpg")
    with Image.open(io.BytesIO(rendered.read())) as clean:
        assert clean.format == "JPEG"
        assert clean.mode == "RGB"
        assert max(clean.size) == images.CARD_SIZE


@pytest.mark.django_db
def test_saving_a_trip_photo_stores_a_full_and_a_card_copy(trip):
    trip.hero_image = upload()
    trip.save()
    trip.refresh_from_db()
    assert trip.hero_image.name.startswith("trips/")
    assert trip.hero_image_card.name.startswith("trips/cards/")
    assert images.SERVABLE_NAME.fullmatch(trip.hero_image.name)
    assert images.SERVABLE_NAME.fullmatch(trip.hero_image_card.name)
    assert trip.hero_image.storage.exists(trip.hero_image.name)


@pytest.mark.django_db
def test_replacing_or_clearing_a_photo_deletes_the_old_files(
    trip, django_capture_on_commit_callbacks
):
    trip.hero_image = upload()
    trip.save()
    storage = trip.hero_image.storage
    first = {trip.hero_image.name, trip.hero_image_card.name}

    trip = Trip.objects.get(pk=trip.pk)
    with django_capture_on_commit_callbacks(execute=True):
        trip.hero_image = upload()
        trip.save()
    assert not any(storage.exists(name) for name in first)
    second = {trip.hero_image.name, trip.hero_image_card.name}
    assert all(storage.exists(name) for name in second)

    trip = Trip.objects.get(pk=trip.pk)
    with django_capture_on_commit_callbacks(execute=True):
        trip.hero_image = ""
        trip.save()
    assert not trip.hero_image_card
    assert not any(storage.exists(name) for name in second)


@pytest.mark.django_db
def test_deleting_a_trip_deletes_its_gallery_files(trip, django_capture_on_commit_callbacks):
    photo = TripImage.objects.create(trip=trip, image=upload())
    storage = photo.image.storage
    names = {photo.image.name, photo.image_card.name}
    with django_capture_on_commit_callbacks(execute=True):
        trip.delete()
    assert not any(storage.exists(name) for name in names)


# --- How it is served ------------------------------------------------------


@pytest.mark.django_db
def test_a_stored_photo_is_served_as_an_inert_cacheable_image(client, trip):
    trip.hero_image = upload()
    trip.save()
    response = client.get(trip.hero_image.url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert "immutable" in response["Cache-Control"]
    response.close()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "../config/settings/base.py",
        "trips/../../manage.py",
        "trips/notes.txt",
        "trips/abc.svg",
        "trips/0123456789abcdef0123456789abcdef.png",
        "private/0123456789abcdef0123456789abcdef.jpg",
        "trips/0123456789abcdef0123456789abcdef.jpg",  # right shape, does not exist
    ],
)
def test_nothing_else_under_media_is_served(client, path):
    response = client.get(reverse("media", kwargs={"path": path}))
    assert response.status_code == 404


def test_only_get_and_head_are_allowed(client):
    response = client.post(
        reverse("media", kwargs={"path": "trips/0123456789abcdef0123456789abcdef.jpg"})
    )
    assert response.status_code == 405


@pytest.mark.django_db
def test_a_site_photo_replaces_the_built_in_illustration(client, db):
    home = client.get(reverse("catalog:home")).content.decode()
    assert "img/placeholders/hero.svg" in home

    photo = SiteImage.objects.create(
        slot=SiteImage.Slot.HOME_HERO, image=upload(), alt_text="A coach"
    )
    home = client.get(reverse("catalog:home")).content.decode()
    assert "img/placeholders/hero.svg" not in home
    assert photo.image.url in home
    assert 'alt="A coach"' in home
