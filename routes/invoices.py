"""
routes/invoices.py — Invoice management (scoped per owner/user).
Includes: list, view, create, mark-paid, delete, download PDF, CSV export, resend email.
"""

import logging
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, jsonify, send_file,
)
from flask_login import login_required, current_user
from extensions import db
from models import Invoice, InvoiceStatus, Client, Product
from utils.customer_validation import (
    find_customer_by_phone,
    validate_email_address,
    validate_phone,
)
from utils.security import safe_redirect_target

logger = logging.getLogger(__name__)
invoices_bp = Blueprint("invoices", __name__, url_prefix="/invoices")


def _owned_invoice(invoice_id):
    return Invoice.query.filter_by(id=invoice_id, owner_id=current_user.id).first_or_404()


def _parse_invoice_quantity(raw):
    try:
        quantity = Decimal((raw or "").strip())
    except (InvalidOperation, ValueError):
        return None
    return quantity.quantize(Decimal("0.001"))


def _quantity_text(quantity):
    value = Decimal(str(quantity or 0))
    return format(value.normalize(), "f").rstrip("0").rstrip(".") or "0"


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
    app = current_app._get_current_object()

    from utils.invoice_branding import resolve_business_branding
    brand = resolve_business_branding(invoice.owner_id, app)

    qr_b64 = ""
    try:
        import base64
        from utils.qr import build_upi_qr_bytes

        if brand["upi_id"]:
            qr_bytes = build_upi_qr_bytes(
                upi_id=brand["upi_id"],
                payee_name=brand["company_name"],
                amount=float(invoice.total),
                note=invoice.invoice_number,
            )
            if qr_bytes:
                qr_b64 = base64.b64encode(qr_bytes).decode()
    except Exception:
        pass

    return render_template("invoices/view.html",
        invoice=invoice,
        qr_b64=qr_b64,
        upi_id=brand["upi_id"],
        app_name=app.config.get("APP_NAME", "InvoiceFlow"),
        company_name=brand["company_name"],
        company_address=brand["company_address"],
        company_phone=brand["company_phone"],
        company_email=brand["company_email"],
        company_gstin=brand["company_gstin"],
        logo_url=brand["logo_url"],
        template=brand["template"],
        terms_conditions=brand["terms_conditions"],
        authorized_signatory=brand["authorized_signatory"],
    )


@invoices_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_invoice():
    clients = (Client.query
               .filter_by(owner_id=current_user.id, is_active=True)
               .order_by(Client.name.asc()).all())
    products = (Product.query
                .filter_by(owner_id=current_user.id, is_active=True)
                .order_by(Product.name.asc()).all())
    preselected_client_id = request.args.get("client_id", type=int)
    default_due = (date.today() + timedelta(days=30)).isoformat()

    if request.method == "POST":
        client_type = request.form.get("client_type", "existing").strip()
        
        amount_raw = request.form.get("amount",    "").strip()
        due_date_s = request.form.get("due_date",  "").strip()
        notes      = request.form.get("notes",     "").strip()
        gst_rate_s = request.form.get("gst_rate",  "18").strip()
        transaction_type = request.form.get("transaction_type", "in").strip()
        product_id_raw = request.form.get("product_id", "").strip()
        product_quantity_raw = request.form.get("product_quantity", "").strip()

        errors = []
        client_id = None
        pending_guest = None
        selected_product = None
        product_quantity = None
        product_stock_on_hand = None
        amount = None
        gst_rate = None

        if transaction_type not in {"in", "out"}:
            errors.append("Transaction type is invalid.")

        if client_type == "one-time":
            guest_name = request.form.get("guest_name", "").strip()
            guest_email = request.form.get("guest_email", "").strip()
            guest_phone = request.form.get("guest_phone", "").strip()

            if not guest_name:
                errors.append("Customer name is required for one-time billing.")

            phone_ok, phone_error = validate_phone(guest_phone)
            if not phone_ok:
                errors.append(phone_error)

            email_ok, normalized_email, email_error = validate_email_address(guest_email)
            if not email_ok:
                errors.append(email_error)

            duplicate = find_customer_by_phone(current_user.id, guest_phone) if guest_phone else None
            if duplicate:
                client_id = duplicate.id
            elif guest_name:
                pending_guest = {
                    "name": guest_name,
                    "email": normalized_email or None,
                    "phone": guest_phone or None,
                }
        else:
            client_id_raw = request.form.get("client_id", "").strip()
            if not client_id_raw:
                errors.append("Please select a customer.")
            else:
                try:
                    selected_id = int(client_id_raw)
                    client = Client.query.filter_by(
                        id=selected_id,
                        owner_id=current_user.id,
                        is_active=True,
                    ).first()
                    if not client:
                        errors.append("Selected customer not found.")
                    else:
                        client_id = client.id
                except ValueError:
                    errors.append("Selected customer is invalid.")

        if product_id_raw:
            try:
                selected_product_id = int(product_id_raw)
            except ValueError:
                selected_product_id = None
                errors.append("Selected product is invalid.")

            if selected_product_id:
                selected_product = Product.query.filter_by(
                    id=selected_product_id,
                    owner_id=current_user.id,
                    is_active=True,
                ).first()
                if not selected_product:
                    errors.append("Selected product not found.")

            if selected_product:
                if not product_quantity_raw:
                    errors.append("Product quantity is required.")
                else:
                    product_quantity = _parse_invoice_quantity(product_quantity_raw)
                    if product_quantity is None:
                        errors.append("Product quantity must be a valid number.")
                    elif product_quantity <= 0:
                        errors.append("Product quantity must be greater than zero.")

                if product_quantity is not None and product_quantity > 0:
                    product_stock_on_hand = Decimal(str(selected_product.current_stock or 0))
                    if product_quantity > product_stock_on_hand:
                        errors.append(
                            f"Only {selected_product.stock_display} is available for {selected_product.name}."
                        )

                selling_price = Decimal(str(selected_product.selling_price or 0))
                if selling_price <= 0:
                    errors.append("Selected product must have a selling price greater than zero.")

                if product_quantity is not None and product_quantity > 0 and selling_price > 0:
                    amount_decimal = (selling_price * product_quantity).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
                    amount = float(amount_decimal)
                    gst_rate = float(selected_product.gst_rate or 0)

        # --- Core Invoice Validations (Kept exact same) ---
        if not selected_product:
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

        if not selected_product:
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
                clients=clients, products=products, form=request.form,
                default_due=default_due, today=date.today(),
                preselected_client_id=preselected_client_id,
                app_name=current_app.config.get("APP_NAME"))

        if pending_guest:
            new_client = Client(
                owner_id=current_user.id,
                name=pending_guest["name"],
                email=pending_guest["email"],
                phone=pending_guest["phone"],
                is_active=True,
            )
            db.session.add(new_client)
            db.session.flush()
            client_id = new_client.id

        if selected_product and not notes:
            qty_text = _quantity_text(product_quantity)
            notes = f"{selected_product.name} ({qty_text} {selected_product.unit})"

        gst_amount, total = Invoice.calculate_gst(amount, rate=gst_rate)

        invoice = Invoice(
            owner_id         = current_user.id,
            invoice_number   = Invoice.next_invoice_number(owner_id=current_user.id),
            client_id        = client_id, # Uses either selection or newly created ID!
            amount           = amount,
            gst              = gst_amount,
            gst_rate         = gst_rate,
            total            = total,
            due_date         = due_date,
            status           = InvoiceStatus.UNPAID,
            notes            = notes or None,
            transaction_type = transaction_type,
            product_id       = selected_product.id if selected_product else None,
            product_quantity = product_quantity if selected_product else None,
        )
        db.session.add(invoice)
        db.session.flush()

        if selected_product and product_quantity is not None:
            selected_product.current_stock = product_stock_on_hand - product_quantity

        app = current_app._get_current_object()
        try:
            from utils.pdf import build_and_save_invoice_pdf
            _, rel_path = build_and_save_invoice_pdf(invoice, app)
            invoice.pdf_path = rel_path
        except Exception as exc:
            logger.error("PDF failed for %s: %s", invoice.invoice_number, exc)

        db.session.commit()
        flash(f"Invoice {invoice.invoice_number} generated successfully.", "success")
        _dispatch_email(invoice, app)
        return redirect(url_for("invoices.view_invoice", invoice_id=invoice.id))

    return render_template("invoices/create.html",
        clients=clients, products=products, form={},
        preselected_client_id=preselected_client_id,
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
    next_page = safe_redirect_target(
        request.args.get("next"),
        url_for("invoices.list_invoices"),
    )
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

    try:
        from utils.pdf import build_and_save_invoice_pdf
        _, rel_path = build_and_save_invoice_pdf(invoice, app)
        invoice.pdf_path = rel_path
        db.session.commit()
    except Exception as exc:
        logger.exception("PDF generation failed for invoice %s: %s", invoice.invoice_number, exc)
        flash("Could not generate the PDF. Please try again.", "danger")
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


@invoices_bp.route("/<int:invoice_id>/whatsapp-share")
@login_required
def whatsapp_share(invoice_id):
    """Prepare invoice data for WhatsApp sharing (JSON API)."""
    invoice = _owned_invoice(invoice_id)
    app = current_app._get_current_object()

    try:
        from utils.whatsapp import prepare_invoice_whatsapp_share, WhatsAppShareError

        payload = prepare_invoice_whatsapp_share(invoice, app)
        db.session.commit()
        payload["pdf_url"] = url_for(
            "invoices.download_pdf",
            invoice_id=invoice.id,
            _external=False,
        )
        return jsonify(payload)
    except WhatsAppShareError as exc:
        logger.warning("WhatsApp share failed for invoice %s: %s", invoice_id, exc)
        return jsonify({
            "success": False,
            "error": "Could not prepare the invoice PDF for sharing.",
            "error_code": "pdf_failed",
        }), 500
    except Exception as exc:
        logger.exception("WhatsApp share failed for invoice %s: %s", invoice_id, exc)
        return jsonify({
            "success": False,
            "error": "Something went wrong while preparing the invoice for sharing.",
            "error_code": "unknown",
        }), 500


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
        flash("Invoice saved, but the email could not be sent. Check mail settings and try again.", "warning")
