import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal, dependency-free .env loader (matches this codebase's
    existing preference for stdlib-only implementations over adding a
    package — see scheduler/ai/providers/openai_provider.py). Only sets a
    variable if it isn't already present in the real process environment,
    so a real env var always wins over the file. `.env` is git-ignored —
    see .gitignore — and never committed. Silently does nothing if the
    file doesn't exist; never raises on a malformed line."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / '.env')

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-change-me-before-deploying"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Phase 3 (Authentication and Authorization) follow-up fix — Django's
# CsrfViewMiddleware rejects any unsafe request that carries an Origin
# header not matching request.get_host() (see
# django.middleware.csrf.CsrfViewMiddleware._origin_verified — this check
# is unconditional, not just for HTTPS). The Vite dev server (frontend/
# vite.config.ts, port 5170) proxies /api to this backend (port 8001) with
# changeOrigin:true, which rewrites the Host header the backend sees to
# match ITS OWN port — but the browser's real Origin header still reads
# the page's actual origin (localhost:5170), so the two never match and
# every authenticated mutating request (POST/PATCH/DELETE) gets rejected
# with "Origin checking failed", regardless of role or CSRF token
# correctness. This is NOT a weakening of CSRF protection — it is Django's
# own documented mechanism for exactly this situation (a legitimate
# frontend origin that differs from the backend's own), scoped to named
# origins only. Configurable the same way ALLOWED_HOSTS already is, so a
# real deployment sets its own real origin(s) via the env var instead of
# relying on this dev-only default.
CSRF_TRUSTED_ORIGINS = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5170").split(",")

# ── Applications ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "scheduler",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # TEST-ONLY — see scheduler/test_auth_middleware.py's module docstring.
    # Only ever active when AUTO_AUTH_TEST_USER is True, which is only ever
    # True when TESTING is True below. Must run after AuthenticationMiddleware
    # (needs request.user already resolved from any real session).
    "scheduler.test_auth_middleware.AutoAuthTestMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "seglc_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "seglc_backend.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Phase 3 (Authentication and Authorization) — TESTING is True only when
# running `manage.py test` (or pytest, if ever adopted). AUTO_AUTH_TEST_USER
# gates scheduler.test_auth_middleware.AutoAuthTestMiddleware, which is the
# ONLY thing that reads this flag — see that module's docstring for why it
# exists and why it can never activate outside a test run.
TESTING = "test" in sys.argv or "pytest" in sys.modules
AUTO_AUTH_TEST_USER = TESTING

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_L10N = True
USE_TZ = True

# ── Static files (served by Nginx in production) ──────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ── Upload limits ─────────────────────────────────────────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024   # 50 MB — large schedule files

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
