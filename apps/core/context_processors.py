from django.conf import settings
from django.http import HttpRequest


def site(request: HttpRequest) -> dict:
    """Site-wide values every template needs, including the category nav."""
    from apps.catalog.views import category_summaries

    return {
        "site_name": settings.SITE_NAME,
        "site_tagline": settings.SITE_TAGLINE,
        "categories": category_summaries(),
    }
