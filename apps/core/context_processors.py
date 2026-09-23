import re

from django.conf import settings
from django.http import HttpRequest


def site(request: HttpRequest) -> dict:
    """Site-wide values every template needs, including the category nav."""
    from apps.catalog.presentation import published_pages, site_photos, site_text
    from apps.catalog.views import category_summaries

    photos = site_photos()
    return {
        "site_text": site_text(),
        "site_pages": published_pages(),
        "site_name": settings.SITE_NAME,
        "site_tagline": settings.SITE_TAGLINE,
        "site_phone": settings.SITE_PHONE,
        # Digits and a leading + only, so the tel: link cannot carry anything else.
        "site_phone_href": re.sub(r"[^\d+]", "", settings.SITE_PHONE),
        "site_email": settings.SITE_EMAIL,
        "payments_enabled": settings.PAYMENTS_ENABLED,
        "site_photos": photos,
        "categories": category_summaries(photos),
    }
