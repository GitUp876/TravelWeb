"""Swaps Django's default admin site for one that requires MFA.

Referenced from INSTALLED_APPS in place of ``django.contrib.admin``.
"""

from django.contrib.admin.apps import AdminConfig


class StaffAdminConfig(AdminConfig):
    default_site = "apps.core.admin_site.StaffAdminSite"
