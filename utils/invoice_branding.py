"""
Shared business branding resolution for invoice PDF, print, and web views.
"""

import os

from models import BusinessProfile

INVOICE_TEMPLATES = ("modern", "classic", "minimal")
DEFAULT_TEMPLATE = "modern"

DEFAULT_TERMS = (
    "Payment is due by the date specified on this invoice. "
    "Late payments may incur additional charges as per applicable law. "
    "All disputes are subject to the jurisdiction of the courts at the place of business. "
    "Goods once sold will not be taken back unless agreed in writing."
)


def _resolve_logo_path(profile, app):
    """Return absolute filesystem path to company logo, or empty string."""
    upload_root = app.config.get("UPLOAD_FOLDER", "uploads")

    if profile and profile.logo_path:
        full = os.path.join(upload_root, profile.logo_path)
        if os.path.exists(full):
            return full

    env_logo = app.config.get("COMPANY_LOGO", "")
    if env_logo and os.path.exists(env_logo):
        return env_logo

    return ""


def logo_url(profile, app):
    """Return URL path for logo in HTML templates, or None."""
    if profile and profile.logo_path:
        upload_root = app.config.get("UPLOAD_FOLDER", "uploads")
        full = os.path.join(upload_root, profile.logo_path)
        if os.path.exists(full):
            return f"/uploads/{profile.logo_path}"
    return None


def resolve_business_branding(owner_id, app):
    """
    Resolve company branding for an invoice owner.

    Returns a dict used by PDF generation, print view, and settings.
    Profile values take priority; env config is the fallback.
    """
    profile = BusinessProfile.query.filter_by(owner_id=owner_id).first()

    template = DEFAULT_TEMPLATE
    if profile and profile.invoice_template in INVOICE_TEMPLATES:
        template = profile.invoice_template

    terms = DEFAULT_TERMS
    if profile and profile.terms_conditions:
        terms = profile.terms_conditions

    signatory = ""
    if profile:
        signatory = profile.authorized_signatory or profile.owner_name or ""

    return {
        "profile": profile,
        "company_name": (
            profile.business_name if profile and profile.business_name
            else app.config.get("COMPANY_NAME", "InvoiceFlow")
        ),
        "company_address": (
            profile.address if profile and profile.address
            else app.config.get("COMPANY_ADDRESS", "")
        ),
        "company_phone": (
            profile.phone if profile and profile.phone
            else app.config.get("COMPANY_PHONE", "")
        ),
        "company_email": (
            profile.email if profile and profile.email
            else app.config.get("COMPANY_EMAIL", "")
        ),
        "company_gstin": (
            profile.gst_number if profile and profile.gst_number
            else app.config.get("COMPANY_GSTIN", "")
        ),
        "upi_id": (
            profile.upi_id if profile and profile.upi_id
            else app.config.get("UPI_ID", "")
        ),
        "logo_path": _resolve_logo_path(profile, app),
        "logo_url": logo_url(profile, app),
        "template": template,
        "terms_conditions": terms,
        "authorized_signatory": signatory,
    }
