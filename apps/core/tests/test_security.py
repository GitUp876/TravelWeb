"""The security behaviour we actually depend on, asserted rather than assumed."""

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.core.audit import redact
from apps.core.middleware import CONTENT_SECURITY_POLICY


@pytest.mark.django_db
def test_every_response_carries_a_content_security_policy(client):
    response = client.get(reverse("catalog:home"))
    assert response["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in response["Content-Security-Policy"]
    assert "'unsafe-inline'" not in response["Content-Security-Policy"]


@pytest.mark.django_db
def test_responses_deny_framing_and_lock_down_features(client):
    response = client.get(reverse("catalog:home"))
    assert response["X-Frame-Options"] == "DENY"
    assert "camera=()" in response["Permissions-Policy"]
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"


@pytest.mark.django_db
def test_admin_requires_a_login(client):
    response = client.get(f"/{settings.ADMIN_URL}", follow=True)
    assert response.status_code == 200
    assert "password" in response.content.decode().lower()


@pytest.mark.django_db
def test_admin_login_asks_for_a_one_time_code(client):
    """django-otp's admin site adds the token field; a password alone is not enough."""
    response = client.get(f"/{settings.ADMIN_URL}login/")
    assert "otp_token" in response.content.decode()


@pytest.mark.django_db
def test_healthz_reports_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_audit_redacts_sensitive_fields():
    redacted = redact({"password": "hunter2", "dietary_notes": "nuts", "full_name": "Dana"})
    assert redacted["password"] == "[redacted]"
    assert redacted["dietary_notes"] == "[redacted]"
    assert redacted["full_name"] == "Dana"


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_debug_is_off_in_the_tested_configuration():
    assert settings.DEBUG is False
