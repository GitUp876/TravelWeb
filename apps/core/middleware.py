"""Response headers that Django does not set for us.

Written by hand rather than pulled from a package: the policy is a dozen lines
and this keeps one fewer dependency in the supply chain.
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

# No third-party scripts, no inline scripts, no framing. Styles and images are
# served from our own origin; `data:` is allowed for images so inline SVG icons
# work without a separate request.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
)

PERMISSIONS_POLICY = ", ".join(
    [
        "accelerometer=()",
        "camera=()",
        "geolocation=()",
        "gyroscope=()",
        "magnetometer=()",
        "microphone=()",
        "payment=()",
        "usb=()",
    ]
)


class SecurityHeadersMiddleware:
    """Adds CSP and Permissions-Policy to every response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return response
