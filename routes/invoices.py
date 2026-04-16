"""
routes/invoices.py — Invoice management (scoped per owner/user).
Includes: list, view, create, mark-paid, delete, download PDF, CSV export, resend email.
"""

import logging
import os
from datetime import date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, jsonify, send_file,
)
from flask_login import login_required, current_user
from extensions import db
from models import Invoice, InvoiceStatus, Client

logger = logging.getLogger(__name__)
invoices_bp = Blueprint("invoices", __name__, url_prefix="/invoices")


def _owned_invoice(invoice_id):
    return Invoice.query.filter_by(id=invoice_id, owner_id=current_user.id).first_or_404()


@invoices_bp.route("/")
@login_required
def list_invoices():
    status_filter = request.args.get("status", "").strip()
    search        = request.args.get("q",      "").strip()
    page          = request.args.get("page", 1, type=int)

    query = (Invoice.query
             .filter_by(owner_id=current_user.id)
             .join(Client)
             .order_by(Invoice.created_at.desc()))

    if status_filter and status_filter in InvoiceStatus.ALL:
        query = query.filter(Invoice.status == status_filter)

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(Invoice.invoice_number.ilike(like), Client.name.ilike(like))
        )

    pagination = query.paginate(page=page, per_page=15, error_out=False)

    base = Invoice.query.filter_by(owner_id=current_user.id)
    counts = {
        "all":    base.count(),
        "unpaid": base.filter_by(status=InvoiceStatus.UNPAID).count(),
        "paid":   base.filter_by(status=InvoiceStatus.PAID).count(),
        "overdue": sum(1 for inv in
                       base.filter_by(status=InvoiceStatus.UNPAID).all()
                       if inv.is_overdue),
    }

    return render_template("invoices/list.html",
        invoices      = pagination.items,
        pagination    = pagination,
        status_filter = status_filter,
        search        = search,
        counts        = counts,
        app_name      = current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@invoices_bp.route("/<int:invoice_id>")
@login_required
def view_invoice(invoice_id):
    invoice = _owned_invoice(invoice_id)

    qr_b64 = ""
    try:
        import base64
        from utils.qr import build_upi_qr_for_invoice
        qr_bytes = build_upi_qr_for_invoice(invoice, current_app._get_current_object())
        if qr_bytes:
            qr_b64 = base64.b64encode(qr_bytes).decode()
    except Exception:
        pass

    return render_template("invoices/view.html",
        invoice         = invoice,
        qr_b64          = qr_b64,
        upi_id          = current_app.config.get("UPI_ID", ""),
        app_name        = current_app.config.get("APP_NAME", "InvoiceFlow"),
        company_name    = current_app.config.get("COMPANY_NAME", ""),
        company_address = current_app.config.get("COMPANY_ADDRESS", ""),
        company_phone   = current_app.config.get("COMPANY_PHONE", ""),
        company_email   = current_app.config.get("COMPANY_EMAIL", ""),
        company_gstin   = current_app.config.get("COMPANY_GSTIN", ""),
    )


@invoices_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_invoice():
    clients = (Client.query
               .filter_by(owner_id=current_user.id, is_active=True)
               .order_by(Client.name.asc()).all())

    if request.method == "POST":
        client_id  = request.form.get("client_id", "").strip()
        amount_raw = request.form.get("amount",    "").strip()
        due_date_s = request.form.get("due_date",  "").strip()
        notes      = request.form.get("notes",     "").strip()
        gst_rate_s = request.form.get("gst_rate",  "18").strip()

        errors = []
        client = None

        if not client_id:
            errors.append("Please select a client.")
        else:
            client = Client.query.filter_by(id=int(client_id),
                                             owner_id=current_user.id).first()
            if not client:
                errors.append("Client not found.")

        if not amount_raw:
            errors.append("Amount is required.")
        else:
            try:
                amount = float(amount_raw)
                if amount <= 0:
                    errors.append("Amount must be greater than zero.")
            except ValueError:
                errors.append("Amount must be a valid number.")

        if not due_date_s:
            errors.append("Due date is required.")
        else:
            try:
                due_date = date.fromisoformat(due_date_s)
            except ValueError:
                errors.append("Due date format is invalid.")

        try:
            gst_rate = float(gst_rate_s)
            if gst_rate < 0 or gst_rate > 100:
                errors.append("GST rate must be between 0 and 100.")
        except ValueError:
            gst_rate = 18.0

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template("invoices/create.html",
                clients=clients, form=request.form,
                today=date.today(),
                app_name=current_app.config.get("APP_NAME"))

        gst_amount, total = Invoice.calculate_gst(amount, rate=gst_rate)

        invoice = Invoice(
            owner_id       = current_user.id,
            invoice_number = Invoice.next_invoice_number(owner_id=current_user.id),
            client_id      = int(client_id),
            amount         = amount,
            gst            = gst_amount,
            gst_rate       = gst_rate,
            total          = total,
            due_date       = due_date,
            status         = InvoiceStatus.UNPAID,
            notes          = notes or None,
        )
        db.session.add(invoice)
        db.session.flush()

        app = current_app._get_current_object()
        try:
            from utils.pdf import build_and_save_invoice_pdf
            _, rel_path = build_and_save_invoice_pdf(invoice, app)
            invoice.pdf_path = rel_path
        except Exception as exc:
            logger.error("PDF failed for %s: %s", invoice.invoice_number, exc)

        db.session.commit()
        flash(f"Invoice {invoice.invoice_number} created for {invoice.client.name}.", "success")
        _dispatch_email(invoice, app)
        return redirect(url_for("invoices.view_invoice", invoice_id=invoice.id))

    default_due = (date.today() + timedelta(days=30)).isoformat()
    return render_template("invoices/create.html",
        clients=clients, form={},
        default_due=default_due, today=date.today(),
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@invoices_bp.route("/<int:invoice_id>/mark-paid", methods=["POST"])
@login_required
def mark_paid(invoice_id):
    invoice = _owned_invoice(invoice_id)
    if invoice.status == InvoiceStatus.PAID:
        flash(f"{invoice.invoice_number} is already paid.", "warning")
    else:
        invoice.mark_paid()
        db.session.commit()
        flash(f"{invoice.invoice_number} marked as paid.", "success")
    next_page = request.args.get("next") or url_for("invoices.list_invoices")
    return redirect(next_page)


@invoices_bp.route("/<int:invoice_id>/delete", methods=["POST"])
@login_required
def delete_invoice(invoice_id):
    invoice = _owned_invoice(invoice_id)
    num     = invoice.invoice_number
    # Delete PDF from disk if it exists
    if invoice.pdf_path and os.path.exists(invoice.pdf_path):
        try:
            os.remove(invoice.pdf_path)
        except OSError:
            pass
    db.session.delete(invoice)
    db.session.commit()
    flash(f"Invoice {num} deleted.", "warning")
    return redirect(url_for("invoices.list_invoices"))


@invoices_bp.route("/<int:invoice_id>/download")
@login_required
def download_pdf(invoice_id):
    invoice = _owned_invoice(invoice_id)
    app = current_app._get_current_object()

    if not invoice.pdf_path or not os.path.exists(invoice.pdf_path):
        try:
            from utils.pdf import build_and_save_invoice_pdf
            _, rel_path = build_and_save_invoice_pdf(invoice, app)
            invoice.pdf_path = rel_path
            db.session.commit()
        except Exception as exc:
            flash(f"Could not generate PDF: {exc}", "danger")
            return redirect(url_for("invoices.view_invoice", invoice_id=invoice_id))

    return send_file(invoice.pdf_path, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"{invoice.invoice_number}.pdf")


@invoices_bp.route("/export/csv")
@login_required
def export_csv():
    from utils.csv_export import invoices_to_csv_response
    status = request.args.get("status", "").strip()
    query  = (Invoice.query
              .filter_by(owner_id=current_user.id)
              .join(Client).order_by(Invoice.created_at.desc()))
    if status and status in InvoiceStatus.ALL:
        query = query.filter(Invoice.status == status)
    filename = f"invoices{'_'+status if status else ''}.csv"
    return invoices_to_csv_response(query.all(), filename=filename)


@invoices_bp.route("/gst-preview")
@login_required
def gst_preview():
    try:
        amount = float(request.args.get("amount", 0))
        if amount < 0:
            raise ValueError
    except ValueError:
        return jsonify({"error": "invalid amount"}), 400
    gst, total = Invoice.calculate_gst(amount)
    return jsonify({"gst": f"{float(gst):,.2f}", "total": f"{float(total):,.2f}"})


@invoices_bp.route("/<int:invoice_id>/resend-email", methods=["POST"])
@login_required
def resend_email(invoice_id):
    invoice = _owned_invoice(invoice_id)
    _dispatch_email(invoice, current_app._get_current_object(), force=True)
    return redirect(url_for("invoices.view_invoice", invoice_id=invoice_id))


def _dispatch_email(invoice, app, force=False):
    try:
        from utils.email import send_invoice_email
        send_invoice_email(invoice, app)
        if app.config.get("MAIL_ENABLED", False):
            recipient = invoice.client.email or app.config.get("MAIL_FALLBACK_RECIPIENT", "")
            flash(f"Invoice email sent to {recipient}.", "info")
    except Exception as exc:
        logger.exception("Email failed for %s: %s", invoice.invoice_number, exc)
        flash(f"Invoice saved but email failed: {exc}", "warning")
