"""Error alerts reach a person, and carry nothing a guest typed."""

import logging
import sys
from types import SimpleNamespace

import pytest
from django.core import mail
from django.core.cache import cache

from apps.core.alerts import SafeAdminEmailHandler


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


def _record(msg="Booking view failed", request=None, exc_info=None):
    record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, msg, (), exc_info)
    if request is not None:
        record.request = request
    return record


def _raise_with_secret():
    try:
        dietary_notes = "severe nut allergy"  # a local the email must not carry
        raise ValueError("lookup failed")
    except ValueError:
        assert dietary_notes
        return sys.exc_info()


def test_nothing_is_sent_without_admins(settings):
    settings.ADMINS = []
    SafeAdminEmailHandler().emit(_record())
    assert mail.outbox == []


def test_an_error_emails_the_admins(settings):
    settings.ADMINS = [("", "ops@example.com")]
    SafeAdminEmailHandler().emit(_record(exc_info=_raise_with_secret()))
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["ops@example.com"]
    assert "ValueError: lookup failed" in message.body
    assert "test_alerts.py" in message.body


def test_locals_and_request_data_stay_out(settings):
    settings.ADMINS = [("", "ops@example.com")]
    request = SimpleNamespace(
        method="POST",
        path="/booking/eyJyZWYiOiJBQkNERUZHSCJ9:1tS3cr3t/",
        POST={"dietary_notes": "severe nut allergy"},
        resolver_match=SimpleNamespace(route="booking/<str:token>/"),
    )
    SafeAdminEmailHandler().emit(_record(request=request, exc_info=_raise_with_secret()))
    body = mail.outbox[0].body
    assert "POST /booking/<str:token>/" in body
    assert "1tS3cr3t" not in body
    assert "nut allergy" not in body


def test_a_repeating_error_sends_one_email(settings):
    settings.ADMINS = [("", "ops@example.com")]
    handler = SafeAdminEmailHandler()
    for _ in range(5):
        handler.emit(_record())
    assert len(mail.outbox) == 1


def test_djangos_own_request_emailer_is_not_installed():
    """Left in place, Django's AdminEmailHandler would email whole requests."""
    from django.utils.log import AdminEmailHandler

    for name in ("django", "django.request", ""):
        logger = logging.getLogger(name)
        while logger is not None:
            assert not any(type(h) is AdminEmailHandler for h in logger.handlers)
            logger = logger.parent if logger.propagate else None
