"""The staff admin.

Built on django-otp's admin site so that a password alone is never enough:
every staff account must present a TOTP code from a device registered to it.
Users without a verified device cannot reach any admin page, including this
one's index.
"""

from django.conf import settings
from django.urls import path
from django_otp.admin import OTPAdminSite


class StaffAdminSite(OTPAdminSite):
    site_title = "Trip administration"
    enable_nav_sidebar = True

    @property
    def site_header(self) -> str:
        return f"{settings.SITE_NAME} — staff"

    @property
    def index_title(self) -> str:
        return "Day-to-day trip management"

    def get_urls(self):
        """Adds the staff reports, wrapped in the admin's own access check.

        ``admin_view`` is what applies this site's rules to a plain view, which
        here means the verified TOTP device every admin page demands. The view
        imports late because this module is loaded while the app registry is
        still starting.
        """
        from apps.bookings import admin_views

        staff_pages = [
            path(
                "reports/payments-due/",
                self.admin_view(admin_views.payments_due),
                name="payments-due",
            ),
            path(
                "bookings/phone/",
                self.admin_view(admin_views.phone_booking),
                name="phone-booking",
            ),
        ]
        return staff_pages + super().get_urls()
