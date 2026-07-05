"""
routes/auth.py — Authentication: username/password + Google OAuth2.

Google OAuth flow
-----------------
1.  GET  /auth/google/login        — redirect user to Google consent screen
2.  GET  /auth/google/callback     — Google redirects here with ?code=
3.                                   Exchange code for token, fetch profile
4.                                   Find or create Admin, login_user(), redirect

Password flow (kept as fallback)
---------------------------------
POST /auth/login  →  validate credentials  →  login_user()
"""

import logging
import re
import secrets

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, session,
)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Admin

logger  = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def _get_google_client():
    """
    Return a configured Authlib OAuth registry client for Google.
    Returns None if GOOGLE_CLIENT_ID is not set.
    """
    client_id     = current_app.config.get("GOOGLE_CLIENT_ID",     "")
    client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        return None

    from authlib.integrations.requests_client import OAuth2Session

    client = OAuth2Session(
        client_id     = client_id,
        client_secret = client_secret,
        scope         = "openid email profile",
        redirect_uri  = url_for("auth.google_callback", _external=True),
    )
    return client


def _is_email_allowed(email: str) -> bool:
    """
    If GOOGLE_ALLOWED_EMAILS is set, only listed addresses may log in.
    An empty list means all Google accounts are accepted.
    """
    allowed_raw = current_app.config.get("GOOGLE_ALLOWED_EMAILS", "")
    if not allowed_raw.strip():
        return True   # open — any Google account

    allowed = [e.strip().lower() for e in allowed_raw.split(",") if e.strip()]
    return email.lower() in allowed


def _is_safe_next_url(next_page: str) -> bool:
    return bool(next_page and next_page.startswith("/") and not next_page.startswith("//"))


def _login_next_url() -> str:
    next_page = request.form.get("next") or request.args.get("next") or ""
    return next_page if _is_safe_next_url(next_page) else ""


def _redirect_after_login():
    next_page = _login_next_url()
    if next_page:
        return redirect(next_page)
    return redirect(url_for("main.dashboard"))


def _render_login(form_mode="signin", form_values=None, google_enabled=None):
    if google_enabled is None:
        google_enabled = bool(current_app.config.get("GOOGLE_CLIENT_ID"))

    return render_template(
        "auth/login.html",
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
        google_enabled=google_enabled,
        form_mode=form_mode,
        form_values=form_values or {},
        next_url=_login_next_url(),
    )


def _find_admin_by_email(email: str):
    return Admin.query.filter(func.lower(Admin.email) == email.lower()).first()


def _find_admin_by_login(identifier: str):
    normalized = identifier.strip().lower()
    if not normalized:
        return None

    admin = _find_admin_by_email(normalized)
    if admin:
        return admin

    return Admin.query.filter(func.lower(Admin.username) == normalized).first()


def _unique_username_from_email(email: str) -> str:
    base = email.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9_.-]+", "-", base).strip("._-") or "user"
    base = base[:60]
    username = base
    suffix = 1

    while Admin.query.filter(func.lower(Admin.username) == username.lower()).first():
        ending = f"-{suffix}"
        username = f"{base[:80 - len(ending)]}{ending}"
        suffix += 1

    return username


def _find_or_create_admin(google_id: str, email: str,
                           display_name: str, avatar_url: str) -> Admin:
    """
    Look up an existing admin by google_id or email; create one if new.
    Existing password-only admins are upgraded with their Google data.
    """
    # 1. Match by google_id (most specific)
    admin = Admin.query.filter_by(google_id=google_id).first()
    if admin:
        # Refresh profile data on every login
        admin.display_name = display_name
        admin.avatar_url   = avatar_url
        db.session.commit()
        return admin

    # 2. Match by email — upgrade an existing password-only account
    admin = _find_admin_by_email(email)
    if not admin:
        # Also try matching by username == email (common pattern)
        admin = Admin.query.filter(func.lower(Admin.username) == email.lower()).first()

    if admin:
        admin.google_id    = google_id
        admin.email        = email
        admin.display_name = display_name
        admin.avatar_url   = avatar_url
        db.session.commit()
        logger.info("Linked Google account to existing admin '%s'", admin.username)
        return admin

    # 3. Brand-new admin — create from Google profile
    username = _unique_username_from_email(email)

    admin = Admin(
        username     = username,
        google_id    = google_id,
        email        = email,
        display_name = display_name,
        avatar_url   = avatar_url,
    )
    db.session.add(admin)
    db.session.commit()
    logger.info("Created new admin '%s' via Google OAuth", username)
    return admin


def _sign_in_with_password(google_enabled):
    identifier = request.form.get("login", "").strip()
    password = request.form.get("password", "")
    remember = bool(request.form.get("remember"))
    form_values = {"login": identifier}

    if not identifier or not password:
        flash("Email or username and password are required.", "danger")
        return _render_login("signin", form_values, google_enabled)

    admin = _find_admin_by_login(identifier)
    if admin is None:
        flash("No account was found for those details.", "danger")
        return _render_login("signin", form_values, google_enabled)

    if not admin.has_password:
        flash("That account uses Google sign in. Continue with Google instead.", "warning")
        return _render_login("signin", form_values, google_enabled)

    if not admin.check_password(password):
        flash("Invalid password. Please try again.", "danger")
        return _render_login("signin", form_values, google_enabled)

    login_user(admin, remember=remember)
    flash(f"Welcome back, {admin.display_name or admin.username}!", "success")
    return _redirect_after_login()


def _create_password_account(google_enabled):
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    form_values = {"display_name": display_name, "email": email}

    if not display_name:
        flash("Please enter your name.", "danger")
        return _render_login("signup", form_values, google_enabled)

    if not email or not EMAIL_RE.match(email):
        flash("Please enter a valid email address.", "danger")
        return _render_login("signup", form_values, google_enabled)

    if len(password) < 8:
        flash("Use at least 8 characters for your password.", "danger")
        return _render_login("signup", form_values, google_enabled)

    if password != confirm_password:
        flash("Passwords do not match.", "danger")
        return _render_login("signup", form_values, google_enabled)

    if _find_admin_by_login(email):
        flash("An account already exists for that email. Sign in instead.", "warning")
        return _render_login("signin", {"login": email}, google_enabled)

    username = _unique_username_from_email(email)
    admin = Admin(username=username, email=email, display_name=display_name)
    admin.set_password(password)
    db.session.add(admin)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("An account already exists for those details. Sign in instead.", "warning")
        return _render_login("signin", {"login": email}, google_enabled)

    login_user(admin, remember=True)
    flash(f"Your account is ready. Welcome, {display_name}!", "success")
    return _redirect_after_login()


# ═══════════════════════════════════════════════════════════════
#  Password login and account creation
# ═══════════════════════════════════════════════════════════════

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Password sign in and sign up form."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    google_enabled = bool(current_app.config.get("GOOGLE_CLIENT_ID"))

    if request.method == "POST":
        form_mode = request.form.get("form_mode", "signin")
        if form_mode == "signup":
            return _create_password_account(google_enabled)
        return _sign_in_with_password(google_enabled)

    form_mode = request.args.get("mode", "signin")
    if form_mode not in {"signin", "signup"}:
        form_mode = "signin"

    return _render_login(form_mode, google_enabled=google_enabled)


# ═══════════════════════════════════════════════════════════════
#  Google OAuth — Step 1: redirect to Google
# ═══════════════════════════════════════════════════════════════

@auth_bp.route("/google/login")
def google_login():
    """Redirect the browser to Google's OAuth consent screen."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    client = _get_google_client()
    if not client:
        flash("Google login is not configured on this server.", "warning")
        return redirect(url_for("auth.login"))

    next_page = request.args.get("next", "")
    if _is_safe_next_url(next_page):
        session["next_url"] = next_page

    # Generate a CSRF-protection state token
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state

    authorization_url, _ = client.create_authorization_url(
        "https://accounts.google.com/o/oauth2/v2/auth",
        state             = state,
        access_type       = "online",
        prompt            = "select_account",   # always show account picker
    )
    return redirect(authorization_url)


# ═══════════════════════════════════════════════════════════════
#  Google OAuth — Step 2: handle callback from Google
# ═══════════════════════════════════════════════════════════════

@auth_bp.route("/google/callback")
def google_callback():
    """
    Google redirects here after the user grants (or denies) access.
    Exchange the authorisation code for an access token, fetch the
    user's profile, then find-or-create an Admin record.
    """
    # ── Error from Google ──────────────────────────────────────
    error = request.args.get("error")
    if error:
        flash(f"Google login cancelled or denied: {error}", "warning")
        return redirect(url_for("auth.login"))

    # ── CSRF state check ───────────────────────────────────────
    returned_state = request.args.get("state", "")
    stored_state   = session.pop("oauth_state", None)
    if not stored_state or returned_state != stored_state:
        flash("Security check failed. Please try logging in again.", "danger")
        return redirect(url_for("auth.login"))

    client = _get_google_client()
    if not client:
        flash("Google login is not configured.", "warning")
        return redirect(url_for("auth.login"))

    # ── Exchange code for token ────────────────────────────────
    try:
        token = client.fetch_token(
            "https://oauth2.googleapis.com/token",
            authorization_response = request.url,
            code                   = request.args.get("code"),
        )
    except Exception as exc:
        logger.exception("Token exchange failed: %s", exc)
        flash("Google login failed — could not exchange token. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    # ── Fetch Google user profile ──────────────────────────────
    try:
        resp    = client.get("https://www.googleapis.com/oauth2/v3/userinfo")
        profile = resp.json()
    except Exception as exc:
        logger.exception("Could not fetch Google profile: %s", exc)
        flash("Google login failed — could not fetch profile.", "danger")
        return redirect(url_for("auth.login"))

    google_id    = profile.get("sub")          # unique, stable Google user ID
    email        = profile.get("email", "")
    display_name = profile.get("name",  "")
    avatar_url   = profile.get("picture", "")

    if not google_id or not email:
        flash("Google did not return a valid account. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    # ── Email allowlist check ──────────────────────────────────
    if not _is_email_allowed(email):
        logger.warning("Blocked Google login for non-allowed email: %s", email)
        flash(
            f"{email} is not authorised to access this application. "
            "Contact the administrator.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    # ── Find or create Admin ───────────────────────────────────
    try:
        admin = _find_or_create_admin(google_id, email, display_name, avatar_url)
    except Exception as exc:
        logger.exception("find_or_create_admin failed: %s", exc)
        flash("Login failed — database error. Please try again.", "danger")
        return redirect(url_for("auth.login"))

    # ── Log in ─────────────────────────────────────────────────
    login_user(admin, remember=True)
    flash(f"Welcome, {admin.display_name or admin.username}! Signed in with Google.", "success")

    next_page = request.args.get("next") or session.pop("next_url", None)
    if next_page and next_page.startswith("/"):
        return redirect(next_page)
    return redirect(url_for("main.dashboard"))


# ═══════════════════════════════════════════════════════════════
#  Logout
# ═══════════════════════════════════════════════════════════════

@auth_bp.route("/logout")
@login_required
def logout():
    """Clear session and redirect to login."""
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
