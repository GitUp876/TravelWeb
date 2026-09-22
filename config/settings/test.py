"""Settings for the test suite: fast, hermetic, no external services."""

import tempfile
from pathlib import Path

from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
SECRET_KEY = "test-only-key"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
AXES_ENABLED = False

# Payments are "configured" in tests, but the gateway itself is always faked:
# no test ever reaches Stripe.
STRIPE_SECRET_KEY = "sk_test_not_a_real_key"
STRIPE_WEBHOOK_SECRET = "whsec_not_a_real_secret"
PAYMENTS_ENABLED = True
SITE_BASE_URL = "http://testserver"

# Whitenoise has no collected static directory during tests.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405

# Uploaded photos go to a throwaway directory, never the working tree.
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="tours-test-media-"))
SITE_PHONE = ""
SITE_EMAIL = ""
