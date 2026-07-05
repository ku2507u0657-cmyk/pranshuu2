"""
config.py — Environment-based configuration for InvoiceFlow
Supports SQLite (development) and PostgreSQL (production).
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    # ── Flask ──────────────────────────────────────────────────
    SECRET_KEY   = os.environ.get("SECRET_KEY")
    APP_NAME     = os.environ.get("APP_NAME",     "InvoiceFlow")
    COMPANY_NAME = os.environ.get("COMPANY_NAME", "Your Company")
    COMPANY_ADDRESS = os.environ.get("COMPANY_ADDRESS", "")
    COMPANY_PHONE   = os.environ.get("COMPANY_PHONE",   "")
    COMPANY_EMAIL   = os.environ.get("COMPANY_EMAIL",   "")
    COMPANY_GSTIN   = os.environ.get("COMPANY_GSTIN",   "")
    COMPANY_LOGO    = os.environ.get("COMPANY_LOGO",    "")   # path to logo file
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=int(os.environ.get("REMEMBER_COOKIE_DAYS", 30)))
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", 3600))
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # ── Database ───────────────────────────────────────────────
    # Set DATABASE_URL to postgresql://... in .env for production.
    # Render / Railway automatically set DATABASE_URL on deploy.
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    _db_url = os.environ.get("DATABASE_URL", "sqlite:///invoice_app.db")
    # Render gives postgres:// but SQLAlchemy needs postgresql://
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url

    # ── PDF Storage ────────────────────────────────────────────
    # Where generated invoice PDFs are saved on disk.
    PDF_FOLDER = os.environ.get("PDF_FOLDER", os.path.join(os.path.dirname(__file__), "invoices"))

    # ── Uploads (company logos, etc.) ──────────────────────────
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER",
        os.path.join(os.path.dirname(__file__), "uploads"),
    )
    MAX_LOGO_SIZE_MB = int(os.environ.get("MAX_LOGO_SIZE_MB", 2))
    MAX_LOGO_PIXELS = int(os.environ.get("MAX_LOGO_PIXELS", 4_000_000))
    MAX_CONTENT_LENGTH = MAX_LOGO_SIZE_MB * 1024 * 1024

    # ── UPI Payment ────────────────────────────────────────────
    UPI_ID          = os.environ.get("UPI_ID",          "")   # e.g. yourname@upi
    UPI_PAYEE_NAME  = os.environ.get("UPI_PAYEE_NAME",  os.environ.get("COMPANY_NAME", "InvoiceFlow"))

    # ── SMTP / Email ───────────────────────────────────────────
    MAIL_ENABLED          = os.environ.get("MAIL_ENABLED", "True").lower() == "true"
    MAIL_SERVER           = os.environ.get("MAIL_SERVER",   "smtp.gmail.com")
    MAIL_PORT             = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS          = os.environ.get("MAIL_USE_TLS",  "True").lower() == "true"
    MAIL_USERNAME         = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD         = os.environ.get("MAIL_PASSWORD")
    MAIL_FROM_NAME        = os.environ.get("MAIL_FROM_NAME",    APP_NAME)
    MAIL_FROM_ADDRESS     = os.environ.get("MAIL_FROM_ADDRESS",
                                           MAIL_USERNAME or "noreply@invoiceflow.app")
    MAIL_FALLBACK_RECIPIENT = os.environ.get("MAIL_FALLBACK_RECIPIENT")

    # ── Google OAuth2 ──────────────────────────────────────────
    # Obtain from https://console.cloud.google.com/
    #   APIs & Services → Credentials → OAuth 2.0 Client IDs
    # Authorised redirect URI must include:
    #   http://localhost:5000/auth/google/callback  (dev)
    #   https://yourdomain.com/auth/google/callback (prod)
    GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID",     "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI  = os.environ.get("GOOGLE_REDIRECT_URI",  "")

    # Comma-separated list of allowed Google email addresses.
    # Leave empty to allow ANY Google account (not recommended for production).
    GOOGLE_ALLOWED_EMAILS = os.environ.get("GOOGLE_ALLOWED_EMAILS", "")

    # ── APScheduler ────────────────────────────────────────────
    SCHEDULER_ENABLED   = os.environ.get("SCHEDULER_ENABLED", "True").lower() == "true"
    REMINDER_HOUR       = int(os.environ.get("REMINDER_HOUR",   9))
    REMINDER_MINUTE     = int(os.environ.get("REMINDER_MINUTE", 0))
    SCHEDULER_TIMEZONE  = os.environ.get("SCHEDULER_TIMEZONE",  "Asia/Kolkata")
    REMINDER_GRACE_DAYS = int(os.environ.get("REMINDER_GRACE_DAYS", 0))

    # ── Recurring Invoices ─────────────────────────────────────
    # Day of month (1-28) on which recurring invoices are auto-generated.
    RECURRING_DAY = int(os.environ.get("RECURRING_DAY", 1))


class DevelopmentConfig(BaseConfig):
    DEBUG             = True
    TESTING           = False
    SECRET_KEY        = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
    MAIL_ENABLED      = os.environ.get("MAIL_ENABLED",      "False").lower() == "true"
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "False").lower() == "true"
    FORCE_HTTPS       = False


class TestingConfig(BaseConfig):
    DEBUG             = True
    TESTING           = True
    SECRET_KEY        = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED  = False
    MAIL_ENABLED      = False
    SCHEDULER_ENABLED = False
    FORCE_HTTPS       = False


class ProductionConfig(BaseConfig):
    DEBUG   = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "True").lower() == "true"


config_map = {
    "development": DevelopmentConfig,
    "testing":     TestingConfig,
    "production":  ProductionConfig,
}


def _is_weak_secret(value):
    normalized = (value or "").strip().lower()
    return (
        not normalized
        or len(normalized) < 32
        or normalized.startswith("change-this")
        or normalized.startswith("dev-only")
        or "secret-key" in normalized
    )


def get_config():
    env = os.environ.get("FLASK_ENV", "development").lower()
    config_cls = config_map.get(env, DevelopmentConfig)
    if config_cls is ProductionConfig and _is_weak_secret(config_cls.SECRET_KEY):
        raise RuntimeError("A strong SECRET_KEY must be set when FLASK_ENV=production")
    return config_cls
