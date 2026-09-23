"""The business's own pages, words and logo: what guests see, and what they can't."""

import io

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.urls import reverse
from PIL import Image

from apps.bookings.models import Booking
from apps.catalog.models import SitePage, SiteText
from apps.core import images


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def publish(kind, body=None):
    page = SitePage.objects.get(kind=kind)
    page.is_published = True
    if body is not None:
        page.body = body
    page.save()
    return page


def png(size=(300, 300), alpha=128, text_chunk=False) -> bytes:
    image = Image.new("RGBA", size, (14, 74, 85, alpha))
    out = io.BytesIO()
    kwargs = {}
    if text_chunk:
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_text("Author", "someone@example.com")
        kwargs["pnginfo"] = info
    image.save(out, format="PNG", **kwargs)
    return out.getvalue()


# --- Pages -----------------------------------------------------------------


@pytest.mark.django_db
def test_starter_drafts_exist_but_are_hidden_from_guests(client):
    assert set(SitePage.objects.values_list("kind", flat=True)) == {"terms", "privacy", "contact"}
    assert not SitePage.objects.filter(is_published=True).exists()
    assert client.get(reverse("catalog:terms")).status_code == 404
    assert client.get(reverse("catalog:privacy")).status_code == 404
    assert client.get(reverse("catalog:contact")).status_code == 404


@pytest.mark.django_db
def test_a_published_page_is_shown_and_linked_from_the_footer(client):
    home = client.get(reverse("catalog:home")).content.decode()
    assert reverse("catalog:terms") not in home

    publish("terms", "## Paying\n\nDeposits are due at booking.")
    response = client.get(reverse("catalog:terms"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "<h2>Paying</h2>" in body
    assert "<p>Deposits are due at booking.</p>" in body
    assert reverse("catalog:terms") in client.get(reverse("catalog:home")).content.decode()


@pytest.mark.django_db
def test_the_contact_page_works_from_the_phone_number_alone(client, settings):
    settings.SITE_PHONE = "1 (800) 555-0142"
    body = client.get(reverse("catalog:contact")).content.decode()
    assert "1 (800) 555-0142" in body
    assert "Starter draft" not in body


def render_text(text):
    return Template("{% load ui %}{{ text|page_text }}").render(Context({"text": text}))


def test_page_text_cannot_carry_markup_or_script():
    html = render_text("<script>alert(1)</script>\n\n## <img src=x onerror=alert(1)>")
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_page_text_makes_lists_and_safe_links():
    html = render_text("- One\n- Two\n\nSee https://example.com/terms?a=1&b=2 today.")
    assert "<ul><li>One</li><li>Two</li></ul>" in html
    assert 'href="https://example.com/terms?a=1&amp;b=2"' in html
    assert 'rel="nofollow noopener noreferrer"' in html


def test_page_text_heading_can_sit_directly_above_its_text():
    html = render_text("## Paying\n- Deposit at booking\n- Balance later\n\n## Contact\nCall us.")
    assert "<h2>Paying</h2>\n<ul><li>Deposit at booking</li><li>Balance later</li></ul>" in html
    assert "<h2>Contact</h2>\n<p>Call us.</p>" in html


def test_page_text_does_not_link_a_javascript_url():
    html = render_text("javascript:alert(1)")
    assert "href" not in html


# --- Consent to the terms --------------------------------------------------


def _booking_post(client, departure, price_option, pickup, **extra):
    data = {
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
    } | extra
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    return client.post(f"{url}?party=1", data)


@pytest.fixture
def fake_checkout(monkeypatch):
    monkeypatch.setattr(
        "apps.bookings.views.checkout.start_checkout",
        lambda booking, **kwargs: "https://checkout.stripe.test/session/abc",
    )


@pytest.mark.django_db
def test_no_consent_box_until_terms_are_published(
    client, departure, price_option, pickup, fake_checkout
):
    response = _booking_post(client, departure, price_option, pickup)
    assert response.status_code == 302
    assert Booking.objects.get().terms_accepted_at is None


@pytest.mark.django_db
def test_published_terms_must_be_accepted_to_book(
    client, departure, price_option, pickup, fake_checkout
):
    publish("terms")
    url = reverse("bookings:book", kwargs={"pk": departure.pk})
    assert 'name="accept_terms"' in client.get(url).content.decode()

    refused = _booking_post(client, departure, price_option, pickup)
    assert refused.status_code == 200
    assert "accept the booking terms" in refused.content.decode()
    assert not Booking.objects.exists()

    accepted = _booking_post(client, departure, price_option, pickup, accept_terms="on")
    assert accepted.status_code == 302
    assert Booking.objects.get().terms_accepted_at is not None


# --- Wording and logo ------------------------------------------------------


@pytest.mark.django_db
def test_home_page_uses_built_in_wording_until_it_is_changed(client):
    body = client.get(reverse("catalog:home")).content.decode()
    assert "Leave the driving to us." in body

    SiteText.objects.create(home_headline="Travel with friends.", footer_about="Since 1987.")
    body = client.get(reverse("catalog:home")).content.decode()
    assert "Travel with friends." in body
    assert "Enjoy the journey." in body  # the blank accent keeps its default
    assert "Since 1987." in body


@pytest.mark.django_db
def test_there_is_only_ever_one_site_text_row():
    SiteText.objects.create(home_headline="One")
    SiteText(home_headline="Two").save()
    assert SiteText.objects.count() == 1
    assert SiteText.current().home_headline == "Two"


@pytest.mark.django_db
def test_an_uploaded_logo_is_rewritten_as_a_clean_png_and_served(client):
    text = SiteText(logo=SimpleUploadedFile("logo.png", png(text_chunk=True), "image/png"))
    text.full_clean()
    text.save()

    assert images.SERVABLE_NAME.fullmatch(text.logo.name)
    with Image.open(text.logo.path) as stored:
        assert stored.format == "PNG"
        assert stored.mode == "RGBA"
        assert stored.getpixel((10, 10))[3] == 128  # transparency survives
        assert "Author" not in stored.info  # text chunks do not

    home = client.get(reverse("catalog:home")).content.decode()
    assert text.logo.url in home
    served = client.get(text.logo.url)
    assert served.status_code == 200
    assert served["Content-Security-Policy"] == "default-src 'none'; sandbox"


@pytest.mark.django_db
def test_replacing_the_logo_removes_the_old_file(django_capture_on_commit_callbacks):
    text = SiteText(logo=SimpleUploadedFile("a.png", png(), "image/png"))
    text.save()
    old_path = text.logo.path

    text = SiteText.current()
    text.logo = SimpleUploadedFile("b.png", png(), "image/png")
    with django_capture_on_commit_callbacks(execute=True):
        text.save()

    import os

    assert not os.path.exists(old_path)
    assert os.path.exists(text.logo.path)


def test_a_logo_can_be_small_but_not_tiny_and_never_svg():
    from apps.core.tests.test_images import Fresh

    images.validate_logo_upload(Fresh(SimpleUploadedFile("l.png", png((120, 120)), "image/png")))
    with pytest.raises(ValidationError):
        images.validate_logo_upload(Fresh(SimpleUploadedFile("l.png", png((40, 40)), "image/png")))
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(ValidationError):
        images.validate_logo_upload(Fresh(SimpleUploadedFile("l.svg", svg, "image/svg+xml")))


def test_a_brand_path_that_is_not_a_png_is_never_served():
    assert not images.SERVABLE_NAME.fullmatch("brand/" + "a" * 32 + ".svg")
    assert not images.SERVABLE_NAME.fullmatch("brand/../settings.py")
