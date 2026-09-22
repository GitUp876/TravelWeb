"""Staff report pages that live inside the admin.

Reached only through the admin site's own wrapper, so the MFA requirement on
every admin page applies here too: a password alone never opens this. The
permission check is explicit on top of that, because the report shows money.
"""

from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.template.response import TemplateResponse

from . import reports

VIEW_PERMISSION = "bookings.view_scheduledpayment"


def payments_due(request: HttpRequest) -> TemplateResponse:
    """Everything owed: overdue instalments, what is coming, and loose balances."""
    if not request.user.has_perm(VIEW_PERMISSION):
        raise PermissionDenied

    horizon = reports.clamp_horizon(request.GET.get("days"))
    context = {
        **admin.site.each_context(request),
        "title": "Payments due",
        **reports.payments_due(horizon_days=horizon),
    }
    return TemplateResponse(request, "admin/payments_due.html", context)
