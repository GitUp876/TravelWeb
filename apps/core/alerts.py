"""Telling a person when something has gone wrong.

Django's own ``AdminEmailHandler`` mails the full request with every error:
headers, cookies, query string and POST body. Here that would put guests'
details, dietary and mobility notes, and signed booking links (which are
credentials) into an inbox. This handler sends only what someone needs to start
looking: which view failed, the exception type, and the traceback's file and
line positions.

Alerts are throttled per message so a fault that repeats on every request sends
one email, not hundreds. The throttle lives in the process's cache, so each
worker may send its own copy; that is a few emails, which is fine.
"""

from __future__ import annotations

import logging
import traceback

from django.conf import settings
from django.core.cache import cache
from django.core.mail import mail_admins

THROTTLE_SECONDS = 15 * 60
MESSAGE_LIMIT = 300


def _where(record: logging.LogRecord) -> str:
    """The route that failed, never the path itself.

    A path can carry a guest's signed booking token, so only the URL pattern
    it matched (``booking/<str:token>/``) is reported.
    """
    request = getattr(record, "request", None)
    if request is None:
        return record.name
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", "") if match is not None else ""
    method = getattr(request, "method", "") or ""
    return f"{method} /{route}".strip() if route else f"{method} (unmatched URL)".strip()


def _stack(exc_info) -> str:
    """File, line and function for each frame, plus the exception type.

    No local variables, and the exception's own message is cut short: a
    database error can quote the row it choked on.
    """
    if not exc_info or exc_info[0] is None:
        return ""
    exc_type, exc, tb = exc_info
    frames = traceback.extract_tb(tb)
    lines = [f'  File "{f.filename}", line {f.lineno}, in {f.name}' for f in frames]
    message = str(exc)[:MESSAGE_LIMIT]
    lines.append(f"{exc_type.__name__}: {message}")
    return "\n".join(lines)


class SafeAdminEmailHandler(logging.Handler):
    """Emails ``settings.ADMINS`` about errors, without request data."""

    def emit(self, record: logging.LogRecord) -> None:
        if not settings.ADMINS:
            return
        # One email per distinct message template, per window.
        key = f"alert:{record.name}:{hash(str(record.msg))}"
        if not cache.add(key, 1, THROTTLE_SECONDS):
            return
        try:
            where = _where(record)
            summary = str(record.getMessage())[:MESSAGE_LIMIT]
            body = "\n\n".join(
                part
                for part in (
                    f"{record.levelname} in {where}",
                    summary,
                    _stack(record.exc_info),
                    "Full details are in the application logs.",
                )
                if part
            )
            subject = f"{record.levelname}: {summary}".splitlines()[0][:120]
            mail_admins(subject, body, fail_silently=True)
        except Exception:  # noqa: BLE001 - an alert must never break the request
            self.handleError(record)
