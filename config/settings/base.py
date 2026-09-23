"""Settings shared by every environment.

Anything that differs between a laptop and production lives in dev.py / prod.py.
Nothing secret is ever hard-coded here; secrets come from the environment.
"""

from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parents[2]


def env(name: str, default: str | None = None) -> str | None:
    import os

    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in (env(name, default) or "").split(",") if item.strip()]


# --- Core ------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-development-key-do-not-use")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "apps.core.admin_config.StaffAdminConfig",  # replaces django.contrib.admin
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "axes",
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.bookings",
    "apps.payments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

# --- Database --------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.config(
        default=env("DATABASE_URL", "sqlite:///db.sqlite3"),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication --------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

# AxesStandaloneBackend must come first so lockouts are enforced before any
# password is checked.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "admin:login"

# Staff sessions end after inactivity and are not kept across browser restarts.
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # the template tag needs to read it
CSRF_COOKIE_SAMESITE = "Lax"

# --- Brute-force protection (django-axes) ----------------------------------

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ACCESS_FAILURE_LOG = True

# --- Internationalisation --------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "America/New_York")
USE_I18N = True
USE_TZ = True

# --- Static and media ------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
# Staff-uploaded photos. In production point this at a persistent disk (the
# container's own filesystem is wiped on every deploy).
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT") or BASE_DIR / "media")

# Largest photo staff may upload. Every accepted photo is re-encoded smaller.
IMAGE_UPLOAD_MAX_BYTES = int(env("IMAGE_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Email -----------------------------------------------------------------

EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "bookings@example.com")

# --- Site ------------------------------------------------------------------

SITE_NAME = env("SITE_NAME", "Group Tours")
SITE_TAGLINE = env("SITE_TAGLINE", "Escorted trips, day tours and getaways")
ADMIN_URL = env("DJANGO_ADMIN_URL", "staff/")
# Shown in the header and footer, and wherever a page says "call us". Leave
# blank to hide them.
SITE_PHONE = env("SITE_PHONE", "")
SITE_EMAIL = env("SITE_EMAIL", "")

# How long a booking may hold seats before they are released back to the pool.
SEAT_HOLD_MINUTES = int(env("SEAT_HOLD_MINUTES", "20"))

# A phone booking is not someone sitting at a checkout page: staff need days to
# collect a cheque, not minutes. An unpaid one still releases its seats in the
# end, so a forgotten booking cannot hold a seat for ever.
STAFF_HOLD_DAYS = int(env("STAFF_HOLD_DAYS", "7"))

# --- Payments (Stripe) -----------------------------------------------------
# Card details never reach this application: the guest types them into Stripe's
# own hosted checkout page. We hold identifiers only.

STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", "")
STRIPE_CURRENCY = env("STRIPE_CURRENCY", "usd")

# Booking is offered only when both halves of the Stripe configuration are
# present. Half-configured is treated as off, so a guest never reaches a
# checkout that cannot be confirmed.
PAYMENTS_ENABLED = bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET)

# Absolute URLs for emails and Stripe redirects. Emails are sent from webhook
# handling, where there is no request to build a URL from.
SITE_BASE_URL = (env("SITE_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

# How long a "manage my booking" link stays valid. Guests have no password, so
# this link is the credential: short-lived, single-purpose and re-sendable.
BOOKING_LINK_MAX_AGE_DAYS = int(env("BOOKING_LINK_MAX_AGE_DAYS", "30"))

# --- Alerts ----------------------------------------------------------------
# Who hears about errors, as a comma-separated list of addresses. Empty means
# nobody is emailed and errors only reach the logs.

ADMINS = [("", address) for address in env_list("DJANGO_ADMINS")]
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL") or DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = f"[{SITE_NAME}] "

# --- Logging ---------------------------------------------------------------
# Personal data must never reach the logs, so no request bodies are logged.
# The "django" logger is configured here on purpose: left alone, it keeps
# Django's default AdminEmailHandler, which would email whole requests.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "alert_email": {"class": "apps.core.alerts.SafeAdminEmailHandler", "level": "ERROR"},
    },
    "root": {
        "handlers": ["console", "alert_email"],
        "level": env("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {"handlers": ["console", "alert_email"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "axes": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
