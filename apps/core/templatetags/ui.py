"""Small presentation helpers for the public templates.

Icons are inline SVG built from the fixed table below. Nothing a user or a
member of staff types ever reaches this markup, so it is safe to mark as such;
an unknown name renders nothing rather than raising, so a typo in a template
cannot take a page down.
"""

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# 24×24 stroke icons, drawn for this site.
ICONS = {
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "calendar": (
        '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "map-pin": (
        '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/>'
        '<circle cx="12" cy="9.5" r="2.5"/>'
    ),
    "users": (
        '<circle cx="9" cy="8" r="3.5"/>'
        '<path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/>'
        '<path d="M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c2 .7 3.2 2.5 3.5 5.2"/>'
    ),
    "utensils": '<path d="M7 3v8a2 2 0 0 0 2 2v8M5 3v6M9 3v6M17 21V3c-2.5 1.5-4 4.5-4 8h4"/>',
    "bus": (
        '<rect x="4" y="3" width="16" height="15" rx="3"/>'
        '<path d="M4 11h16M8 21v-3M16 21v-3"/><circle cx="8" cy="14.5" r=".8"/>'
        '<circle cx="16" cy="14.5" r=".8"/>'
    ),
    "shield": (
        '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z"/>'
        '<path d="M8.5 12l2.5 2.5 4.5-5"/>'
    ),
    "wallet": (
        '<path d="M4 7.5V18a2 2 0 0 0 2 2h14V9H6a2 2 0 0 1-2-2 2 2 0 0 1 2-2h11v4"/>'
        '<circle cx="16" cy="14.5" r="1.2"/>'
    ),
    "phone": (
        '<path d="M5 4h3.5l1.8 4.5-2.3 1.4a11 11 0 0 0 6.1 6.1l1.4-2.3L20 15.5V19a2 2 0 0 1-2 '
        '2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>'
    ),
    "mail": ('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 6.5L12 13l8.5-6.5"/>'),
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "alert": '<path d="M12 3.5L22 20H2z"/><path d="M12 10v4.5M12 17.5v.5"/>',
    "hourglass": (
        '<path d="M6 3h12M6 21h12M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9s10 4 10 9"/>'
    ),
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 '
        '19.4L6 18M18 6l1.4-1.4"/>'
    ),
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "ticket": (
        '<path d="M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 '
        '0 0-2-2v-2a2 2 0 0 0 0-4z"/>'
        '<path d="M14.5 6v12" stroke-dasharray="2 2"/>'
    ),
    "ship": '<path d="M3 16l1.5 4h15L21 16H3z"/><path d="M6 16V10h12v6M9 10V6h6v4M12 3v3"/>',
    "plane": (
        '<path d="M21 4.5c-.8-.8-2.2-.7-3 .1l-3.5 3.6L6 6 4.5 7.5l6.8 4-3.4 3.6-2.6-.4L4 '
        "16l3.5 1.5L9 21l1.3-1.3-.4-2.6 3.6-3.4 4 6.8 1.5-1.5-2.2-8.5 "
        '3.6-3.5c.8-.8.9-2.2.1-3z"/>'
    ),
    "compass": '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    "star": '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8-5.2-2.8-5.2 2.8 1-5.8L3.5 9.7l5.9-.8z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "lock": (
        '<rect x="5" y="10.5" width="14" height="10" rx="2"/>'
        '<path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>'
    ),
    "image": (
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<circle cx="8.5" cy="9.5" r="1.8"/><path d="M21 16l-5.5-5.5L5 20"/>'
    ),
    "route": (
        '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/>'
        '<path d="M8.5 18H16a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h7.5"/>'
    ),
    "heart": (
        '<path d="M12 20s-7.5-4.5-7.5-10A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 7.5 3c0 5.5-7.5 '
        '10-7.5 10z"/>'
    ),
}


@register.simple_tag
def icon(name: str, css_class: str = "icon") -> str:
    """``{% icon "calendar" %}``: an inline SVG icon, hidden from screen readers."""
    paths = ICONS.get(name)
    if paths is None:
        return ""
    return format_html(
        '<svg class="{}" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true" focusable="false">{}</svg>',
        css_class,
        # Fixed markup from the ICONS table above; no caller-supplied text.
        mark_safe(paths),  # noqa: S308  # nosec B308 B703
    )


@register.simple_tag(takes_context=True)
def trip_photo(context, trip, size: str = "card") -> dict:
    """The picture for a trip: its own photo, else its category's, else the
    built-in illustration. ``{% trip_photo trip "full" as photo %}``."""
    field = trip.hero_image if size == "full" else trip.hero_image_card
    if field:
        return {"url": field.url, "alt": trip.image_alt, "is_photo": True}
    category = (context.get("site_photos") or {}).get(trip.category)
    if category:
        url = category["url"] if size == "full" else category["card_url"]
        return {"url": url, "alt": "", "is_photo": False}
    return {"url": trip.placeholder_image_url, "alt": "", "is_photo": False}


@register.filter
def get_item(mapping, key):
    """``{{ site_photos|get_item:slot }}``: a dict lookup with a variable key."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None
