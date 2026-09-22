"""Words and pictures for the public pages that are not stored per trip.

Category blurbs and icons live in code because the categories themselves do.
Photos for fixed places on the site come from ``SiteImage`` when staff have
uploaded one, and fall back to the built-in illustrations otherwise.
"""

from __future__ import annotations

from .models import SiteImage, TripCategory

CATEGORY_DETAILS = {
    TripCategory.DAY_TRIP: {
        "icon": "sun",
        "blurb": "Harbours, museums, gardens and hidden gems, there and back in a day. "
        "We do the driving and the parking.",
    },
    TripCategory.OVERNIGHT: {
        "icon": "moon",
        "blurb": "A few nights away by deluxe motorcoach, with comfortable hotels, good "
        "meals and a tour director from start to finish.",
    },
    TripCategory.THEATRE: {
        "icon": "ticket",
        "blurb": "Broadway and the best of the stage, with your seat on the coach and in "
        "the theatre sorted before you leave home.",
    },
    TripCategory.LUNCHEON: {
        "icon": "utensils",
        "blurb": "A leisurely lunch and a live show. The perfect afternoon out with friends.",
    },
    TripCategory.CRUISE: {
        "icon": "ship",
        "blurb": "Sail away without the airport queues. Coach transfers to the port and "
        "a host who travels with you.",
    },
    TripCategory.FLY: {
        "icon": "plane",
        "blurb": "Further afield, with flights, transfers and a tour director all taken care of.",
    },
}


def site_photos() -> dict[str, dict[str, str]]:
    """Every photo slot, with the uploaded photo when there is one."""
    uploaded = {image.slot: image for image in SiteImage.objects.all()}
    photos = {}
    for slot in SiteImage.Slot.values:
        image = uploaded.get(slot)
        if image:
            photos[slot] = {
                "url": image.image.url,
                "card_url": image.image_card.url if image.image_card else image.image.url,
                "alt": image.alt_text,
            }
        else:
            placeholder = SiteImage.placeholder_url(slot)
            photos[slot] = {"url": placeholder, "card_url": placeholder, "alt": ""}
    return photos
