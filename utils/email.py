"""
utils/email.py — Email delivery via Brevo HTTP API for InvoiceFlow.

Public API
----------
    send_invoice_email(invoice, app)               -> None
    send_reminder_email(invoice, app, days_overdue) -> None
    send_bill_email(bill, app)                     -> None
"""

import logging
import base64
import os
import requests
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)


class EmailError(Exception):
    """Raised when email delivery fails."""


# ─────────────────────────────────────────────────────────────
#  Public: send original invoice
# ─────────────────────────────────────────────────────────────

def send_invoice_email(invoice, app) -> None:
    cfg          = app.config
    
    # ─── NEW: Query the database for the user's business profile ───
    from models import BusinessProfile
    profile = BusinessProfile.query.filter_by(owner_id=invoice.owner_id).first()
    
    # Fallback to config if profile doesn't exist yet
    company_name = profile.business_name if profile and profile.business_name else cfg.get("COMPANY_NAME", cfg.get("APP_NAME", "InvoiceFlow"))
    from_address = profile.email if profile and profile.email else cfg.get("MAIL_FROM_ADDRESS", cfg.get("MAIL_USERNAME", ""))
    # ───────────────────────────────────────────────────────────────

    _guard_enabled(cfg)
    recipient = _resolve_recipient(invoice, cfg)

    # Build PDF bytes
    pdf_bytes = _safe_pdf(invoice, app)

    subject    = f"Invoice {invoice.invoice_number} from {company_name}"
    html_body  = _render_template(app, "emails/invoice_email.html",
                                  invoice=invoice, company_name=company_name)
    plain_body = _plain_invoice(invoice, company_name)

    _brevo_api_send(
        subject=subject,
        from_name=cfg.get("MAIL_FROM_NAME", company_name),
        from_address=from_address,
        recipient_email=recipient,
        recipient_name=invoice.client.name,
        html_body=html_body,
        plain_body=plain_body,
        pdf_bytes=pdf_bytes,
        pdf_filename=f"{invoice.invoice_number}.pdf",
    )
    logger.info("Invoice email sent via Brevo API: %s -> %s", invoice.invoice_number, recipient)


# ─────────────────────────────────────────────────────────────
#  Public: send reminder
# ─────────────────────────────────────────────────────────────

def send_reminder_email(invoice, app, days_overdue: int = 0) -> None:
    cfg          = app.config
    company_name = cfg.get("COMPANY_NAME", cfg.get("APP_NAME", "InvoiceFlow"))

    _guard_enabled(cfg)
    recipient = _resolve_recipient(invoice, cfg)
    pdf_bytes = _safe_pdf(invoice, app)

    day_str = f"{days_overdue} day{'s' if days_overdue != 1 else ''}"
    subject = f"Payment Reminder: {invoice.invoice_number} is {day_str} overdue"

    html_body  = _render_template(app, "emails/reminder_email.html",
                                  invoice=invoice, company_name=company_name,
                                  days_overdue=days_overdue)
    plain_body = _plain_reminder(invoice, company_name, days_overdue)

    _brevo_api_send(
        subject=subject,
        from_name=cfg.get("MAIL_FROM_NAME", company_name),
        from_address=cfg.get("MAIL_FROM_ADDRESS", cfg.get("MAIL_USERNAME", "")),
        recipient_email=recipient,
        recipient_name=invoice.client.name,
        html_body=html_body,
        plain_body=plain_body,
        pdf_bytes=pdf_bytes,
        pdf_filename=f"{invoice.invoice_number}.pdf",
    )
    logger.info("Reminder sent via Brevo API: %s -> %s (%d days overdue)",
                invoice.invoice_number, recipient, days_overdue)


# ─────────────────────────────────────────────────────────────
#  Public: send itemized bill email
# ─────────────────────────────────────────────────────────────

def send_bill_email(bill, app) -> None:
    cfg          = app.config
    
    # ─── NEW: Query the database for the user's business profile ───
    from models import BusinessProfile
    profile = BusinessProfile.query.filter_by(owner_id=bill.owner_id).first()
    
    company_name = profile.business_name if profile and profile.business_name else cfg.get("COMPANY_NAME", cfg.get("APP_NAME", "InvoiceFlow"))
    from_address = profile.email if profile and profile.email else cfg.get("MAIL_FROM_ADDRESS", cfg.get("MAIL_USERNAME", ""))
    # ───────────────────────────────────────────────────────────────

    _guard_enabled(cfg)
    recipient = bill.client.email or cfg.get("MAIL_FALLBACK_RECIPIENT")
    if not recipient:
        raise EmailError(
            f"No recipient for {bill.bill_number}: "
            "client has no email and MAIL_FALLBACK_RECIPIENT is not set."
        )

    # Build PDF bytes
    pdf_bytes = _safe_pdf_bill(bill, app)

    subject    = f"Bill {bill.bill_number} from {company_name}"
    plain_body = _plain_bill_body(bill, company_name)

    try:
        html_body = _render_template(app, "emails/bill_email.html",
                                     bill=bill, company_name=company_name)
    except Exception as exc:
        logger.error("Bill email template render failed: %s", exc)
        escaped = plain_body.replace("\n", "<br/>")
        html_body = f"<html><body style='font-family:sans-serif;font-size:14px'>{escaped}</body></html>"

    _brevo_api_send(
        subject=subject,
        from_name=cfg.get("MAIL_FROM_NAME", company_name),
        from_address=from_address,
        recipient_email=recipient,
        recipient_name=bill.client.name,
        html_body=html_body,
        plain_body=plain_body,
        pdf_bytes=pdf_bytes,
        pdf_filename=f"{bill.bill_number}.pdf",
    )
    logger.info("Bill email sent via Brevo API: %s -> %s", bill.bill_number, recipient)

# ─────────────────────────────────────────────────────────────
#  Private helpers
# ─────────────────────────────────────────────────────────────

def _guard_enabled(cfg):
    if not cfg.get("MAIL_ENABLED", False):
        raise EmailError("Email sending is disabled (MAIL_ENABLED=False).")


def _resolve_recipient(invoice, cfg):
    r = invoice.client.email or cfg.get("MAIL_FALLBACK_RECIPIENT")
    if not r:
        raise EmailError(
            f"No recipient for {invoice.invoice_number}: "
            "client has no email and MAIL_FALLBACK_RECIPIENT is not set."
        )
    return r


def _safe_pdf(invoice, app):
    try:
        from utils.pdf import build_invoice_pdf_bytes
        return build_invoice_pdf_bytes(invoice, app)
    except Exception as exc:
        logger.error("PDF build failed for email attachment: %s", exc)
        return b""


def _safe_pdf_bill(bill, app):
    try:
        from utils.bill_pdf import build_bill_pdf_bytes
        return build_bill_pdf_bytes(bill, app)
    except Exception as exc:
        logger.error("Bill PDF build failed for email: %s", exc)
        return b""


def _render_template(app, path, **ctx):
    with app.app_context():
        return app.jinja_env.get_template(path).render(**ctx)


def _brevo_api_send(subject, from_name, from_address, recipient_email, recipient_name, html_body, plain_body, pdf_bytes, pdf_filename):
    """
    Sends email via Brevo's HTTP API (bypassing Render's SMTP block).
    """
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        raise EmailError("BREVO_API_KEY environment variable is not set on Render.")

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    payload = {
        "sender": {"name": from_name, "email": from_address},
        "to": [{"email": recipient_email, "name": recipient_name}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": plain_body
    }

    # Attach PDF if it exists
    if pdf_bytes:
        encoded_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        payload["attachment"] = [
            {
                "content": encoded_pdf,
                "name": pdf_filename
            }
        ]

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status() # Raise exception for 4XX/5XX errors
    except RequestException as exc:
        err_detail = exc.response.text if exc.response else str(exc)
        raise EmailError(f"Brevo API Error: {err_detail}") from exc


# ─────────────────────────────────────────────────────────────
#  Plain-text bodies
# ─────────────────────────────────────────────────────────────

def _plain_invoice(invoice, company_name):
    return (
        f"Dear {invoice.client.name},\n\n"
        f"Please find attached invoice {invoice.invoice_number} from {company_name}.\n\n"
        f"  Amount (excl. GST):  {invoice.amount_display}\n"
        f"  GST (18%):           {invoice.gst_display}\n"
        f"  Total Payable:       {invoice.total_display}\n"
        f"  Due Date:            {invoice.due_date.strftime('%d %B %Y')}\n\n"
        f"Please reference {invoice.invoice_number} when making payment.\n\n"
        f"Thank you,\n{company_name}"
    )


def _plain_reminder(invoice, company_name, days_overdue):
    ds = f"{days_overdue} day{'s' if days_overdue != 1 else ''}"
    return (
        f"Dear {invoice.client.name},\n\n"
        f"Invoice {invoice.invoice_number} from {company_name} is {ds} overdue.\n\n"
        f"  Invoice:    {invoice.invoice_number}\n"
        f"  Due Date:   {invoice.due_date.strftime('%d %B %Y')}\n"
        f"  Total Due:  {invoice.total_display}\n\n"
        f"Please arrange payment immediately. Quote {invoice.invoice_number} as reference.\n\n"
        f"Regards,\n{company_name}"
    )


def _plain_bill_body(bill, company_name):
    items_text = "\n".join(
        f"  {i}. {item.item_name} x{float(item.quantity):g} @ "
        f"\u20b9{float(item.rate):,.2f} = \u20b9{float(item.item_total):,.2f}"
        for i, item in enumerate(bill.items, 1)
    )
    return (
        f"Dear {bill.client.name},\n\n"
        f"Please find your bill {bill.bill_number} from {company_name}.\n\n"
        f"Items:\n{items_text}\n\n"
        f"  Subtotal:    {bill.subtotal_display}\n"
        f"  Total GST:   {bill.total_gst_display}\n"
        f"  Grand Total: {bill.grand_total_display}\n\n"
        f"Please reference {bill.bill_number} when making payment.\n\n"
        f"Thank you,\n{company_name}"
    )