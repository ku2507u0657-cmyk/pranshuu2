"""
utils/pdf.py — Professional invoice PDF generation using ReportLab.

Delegates to template-specific renderers in pdf_templates.py.
"""

import logging
import os

from utils.pdf_templates import render_invoice_pdf

logger = logging.getLogger(__name__)


def build_invoice_pdf_bytes(invoice, app) -> bytes:
    """Build PDF and return raw bytes (does NOT save to disk)."""
    return render_invoice_pdf(invoice, app)


def build_and_save_invoice_pdf(invoice, app):
    """
    Build PDF, save it to PDF_FOLDER, and return (bytes, relative_path).

    Returns
    -------
    tuple[bytes, str]  (pdf_bytes, relative_path_from_project_root)
    """
    pdf_bytes = render_invoice_pdf(invoice, app)

    pdf_folder = app.config.get("PDF_FOLDER", "invoices")
    os.makedirs(pdf_folder, exist_ok=True)

    filename  = f"{invoice.invoice_number}.pdf"
    full_path = os.path.join(pdf_folder, filename)
    rel_path  = os.path.join("invoices", filename)

    try:
        with open(full_path, "wb") as fh:
            fh.write(pdf_bytes)
        logger.info("PDF saved: %s", full_path)
    except OSError as exc:
        logger.error("Could not save PDF to %s: %s", full_path, exc)

    return pdf_bytes, rel_path
