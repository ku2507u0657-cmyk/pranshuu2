"""
utils/whatsapp.py — WhatsApp invoice sharing helpers.

Builds share messages and wa.me / WhatsApp Web URLs for invoice delivery.
"""

import logging
from urllib.parse import quote

from utils.customer_validation import normalize_phone

logger = logging.getLogger(__name__)

DEFAULT_COUNTRY_CODE = "91"


def format_whatsapp_phone(raw_phone, country_code=DEFAULT_COUNTRY_CODE):
    """
    Normalize a customer phone number for WhatsApp deep links.

    Returns digits-only string with country code (e.g. 919876543210), or empty
    string when the number is missing or invalid.
    """
    digits = normalize_phone(raw_phone)
    if not digits:
        return ""

    if len(digits) == 10 and country_code:
        return f"{country_code}{digits}"

    if len(digits) >= 11:
        return digits

    return ""


def build_invoice_share_message(invoice, business_name):
    """Return the professional WhatsApp message body for an invoice."""
    customer_name = invoice.client.name if invoice.client else "Customer"
    invoice_date = invoice.created_at.strftime("%d %b %Y")
    amount = f"\u20b9{float(invoice.total):,.2f}"

    return (
        f"Hello {customer_name},\n\n"
        f"Thank you for your business.\n\n"
        f"Invoice Number: {invoice.invoice_number}\n"
        f"Invoice Date: {invoice_date}\n"
        f"Amount: {amount}\n\n"
        f"Please find your invoice attached.\n\n"
        f"Regards,\n"
        f"{business_name}"
    )


def build_whatsapp_urls(phone, message):
    """
    Build mobile (wa.me) and desktop (WhatsApp Web) share URLs.

    When phone is empty, URLs open WhatsApp with the pre-filled message only.
    """
    encoded = quote(message, safe="")

    if phone:
        mobile = f"https://wa.me/{phone}?text={encoded}"
        desktop = f"https://web.whatsapp.com/send?phone={phone}&text={encoded}"
    else:
        mobile = f"https://wa.me/?text={encoded}"
        desktop = f"https://web.whatsapp.com/send?text={encoded}"

    return {"mobile": mobile, "desktop": desktop}


def prepare_invoice_whatsapp_share(invoice, app):
    """
    Prepare all data needed to share an invoice on WhatsApp.

    Returns a dict suitable for JSON serialization. Raises WhatsAppShareError
    when PDF generation fails.
    """
    from utils.invoice_branding import resolve_business_branding
    from utils.pdf import build_and_save_invoice_pdf

    brand = resolve_business_branding(invoice.owner_id, app)
    business_name = brand["company_name"]
    message = build_invoice_share_message(invoice, business_name)

    raw_phone = invoice.client.phone if invoice.client else ""
    phone = format_whatsapp_phone(raw_phone)
    has_phone = bool(phone)

    try:
        _, rel_path = build_and_save_invoice_pdf(invoice, app)
        invoice.pdf_path = rel_path
        pdf_ready = True
    except Exception as exc:
        logger.error(
            "PDF generation failed for WhatsApp share %s: %s",
            invoice.invoice_number,
            exc,
        )
        raise WhatsAppShareError(
            "Could not generate invoice PDF. Please try again or download the PDF first."
        ) from exc

    urls = build_whatsapp_urls(phone, message)

    payload = {
        "success": True,
        "has_phone": has_phone,
        "phone": phone,
        "customer_name": invoice.client.name if invoice.client else "",
        "message": message,
        "business_name": business_name,
        "invoice_number": invoice.invoice_number,
        "pdf_ready": pdf_ready,
        "pdf_filename": f"{invoice.invoice_number}.pdf",
        "whatsapp_url_mobile": urls["mobile"],
        "whatsapp_url_desktop": urls["desktop"],
    }

    if not has_phone:
        payload["warning"] = (
            "Customer phone number is not available. "
            "WhatsApp will open so you can choose a contact manually."
        )

    return payload


class WhatsAppShareError(Exception):
    """Raised when invoice WhatsApp sharing cannot be prepared."""
