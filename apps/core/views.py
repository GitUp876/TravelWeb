from django.conf import settings
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_safe
from django.views.static import serve

from .images import SERVABLE_NAME


def healthz(request: HttpRequest) -> HttpResponse:
    """Liveness probe: confirms the process is up and the database answers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - the probe must never leak a traceback
        return JsonResponse({"status": "degraded"}, status=503)
    return JsonResponse({"status": "ok"})


@require_safe
def media(request: HttpRequest, path: str) -> HttpResponse:
    """Serves staff-uploaded photos.

    ``django.views.static.serve`` already refuses path traversal; ``SERVABLE_NAME``
    narrows it further to the exact names our upload code produces. The
    response forbids the file from acting as a document in its own right, and
    since names are random and never reused it can be cached for good.
    """
    if not SERVABLE_NAME.fullmatch(path):
        raise Http404("No such file")
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
