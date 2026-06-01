"""
Django settings for the self-hosted mail server.

Configuration is driven by environment variables (see .env.example) so the same
image runs in development and production. Nothing secret is hard-coded.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


# --- Core -------------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# --- Mail server identity ---------------------------------------------------
# Hostname this server announces in SMTP (must have A + PTR records).
MAIL_SERVER_HOSTNAME = os.environ.get("MAIL_HOSTNAME", "mail.example.com")
# Default DKIM selector used when generating keys.
DKIM_SELECTOR = os.environ.get("DKIM_SELECTOR", "mail")
# Maximum accepted message size (bytes).
MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", str(50 * 1024 * 1024)))
# Ports the SMTP service binds.
SMTP_INBOUND_PORT = int(os.environ.get("SMTP_INBOUND_PORT", "25"))
SMTP_SUBMISSION_PORT = int(os.environ.get("SMTP_SUBMISSION_PORT", "587"))
# Optional STARTTLS material for the submission port.
SMTP_TLS_CERT = os.environ.get("SMTP_TLS_CERT", "")
SMTP_TLS_KEY = os.environ.get("SMTP_TLS_KEY", "")
# IMAP / POP3 listener ports.
IMAP_PORT = int(os.environ.get("IMAP_PORT", "143"))
POP3_PORT = int(os.environ.get("POP3_PORT", "110"))

# Greylisting: seconds a brand-new sender triplet is deferred before a retry
# is accepted. Set to 0 to effectively disable.
GREYLIST_DELAY_SECONDS = int(os.environ.get("GREYLIST_DELAY_SECONDS", "60"))

# ClamAV virus scanning (optional). Leave CLAMAV_HOST blank to disable.
CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "")
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))

# --- Applications -----------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "mail",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "mailserver"),
        "USER": os.environ.get("POSTGRES_USER", "mailuser"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# --- Authentication ---------------------------------------------------------
AUTH_USER_MODEL = "mail.Mailbox"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "mailbox"
LOGOUT_REDIRECT_URL = "login"

# --- I18N / TZ --------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TZ", "UTC")
USE_I18N = True
USE_TZ = True

# --- Static / media ---------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security (enabled when not in DEBUG) -----------------------------------
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "mail": {"handlers": ["console"],
                 "level": os.environ.get("LOG_LEVEL", "INFO"),
                 "propagate": False},
    },
}
