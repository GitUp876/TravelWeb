"""The staff admin.

Built on django-otp's admin site so that a password alone is never enough:
every staff account must present a TOTP code from a device registered to it.
Users without a verified device cannot reach any admin page, including this
one's index.
"""

from django.conf import settings
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
