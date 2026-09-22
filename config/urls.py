from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz, media

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path(settings.ADMIN_URL, admin.site.urls),
    path(f"{settings.MEDIA_URL.lstrip('/')}<path:path>", media, name="media"),
    path("", include("apps.payments.urls")),
    path("", include("apps.bookings.urls")),
    path("", include("apps.catalog.urls")),
]
