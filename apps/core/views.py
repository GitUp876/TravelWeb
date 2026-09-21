from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse


def healthz(request: HttpRequest) -> HttpResponse:
    """Liveness probe: confirms the process is up and the database answers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - the probe must never leak a traceback
        return JsonResponse({"status": "degraded"}, status=503)
    return JsonResponse({"status": "ok"})
