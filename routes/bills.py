"""
routes/bills.py — Itemized bill management (scoped per owner/user).
Includes: list, create, view, mark-paid, delete, download PDF, resend email.
"""

import logging
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, jsonify, send_file,
)
from flask_login import login_required, current_user
from extensions import db
from models import Bill, BillItem, BillStatus, Client, Product
from utils.customer_validation import (
    find_customer_by_phone,
    validate_email_address,
    validate_phone,
)

logger = logging.getLogger(__name__)
bills_bp = Blueprint("bills", __name__, url_prefix="/bills")

QUANTITY_PLACES = Decimal("0.001")
MONEY_PLACES = Decimal("0.01")


def _owned_bill(bill_id):
    return Bill.query.filter_by(id=bill_id, owner_id=current_user.id).first_or_404()


@bills_bp.route("/")
@login_required
def list_bills():
    status = request.args.get("status", "").strip()
    q      = request.args.get("q",      "").strip()
    page   = request.args.get("page", 1, type=int)

    query = (Bill.query
             .filter_by(owner_id=current_user.id)
             .join(Client).order_by(Bill.created_at.desc()))

    if status and status in BillStatus.ALL:
        query = query.filter(Bill.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Bill.bill_number.ilike(like), Client.name.ilike(like))
        )

    pagination = query.paginate(page=page, per_page=15, error_out=False)
    base = Bill.query.filter_by(owner_id=current_user.id)
    counts = {
        "all":    base.count(),
        "unpaid": base.filter_by(status=BillStatus.UNPAID).count(),
        "paid":   base.filter_by(status=BillStatus.PAID).count(),
    }

    return render_template("bills/list.html",
        bills=pagination.items, pagination=pagination,
        status_filter=status, search=q, counts=counts,
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@bills_bp.route("/client/<int:client_id>")
@login_required
def client_bills(client_id):
    client = Client.query.filter_by(id=client_id, owner_id=current_user.id).first_or_404()
    bills  = (Bill.query
              .filter_by(client_id=client_id, owner_id=current_user.id)
              .order_by(Bill.created_at.desc()).all())
    return render_template("bills/client_bills.html",
        client=client, bills=bills,
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@bills_bp.route("/create", methods=["GET", "POST"])
@bills_bp.route("/create/<int:client_id>", methods=["GET", "POST"])
@login_required
def create_bill(client_id=None):
    clients = (Client.query
               .filter_by(owner_id=current_user.id, is_active=True)
               .order_by(Client.name.asc()).all())
    products = (Product.query
                .filter_by(owner_id=current_user.id, is_active=True)
                .order_by(Product.name.asc()).all())
    default_due = (date.today() + timedelta(days=30)).isoformat()
    preselected = client_id

    if request.method == "POST":
        # --- NEW: Detect Client Type ---
        client_type = request.form.get("client_type", "existing").strip()
        
        notes   = request.form.get("notes",     "").strip()
        due_raw = request.form.get("due_date",  "").strip()
        transaction_type = request.form.get("transaction_type", "out").strip()

        item_names   = request.form.getlist("item_name[]")
        descriptions = request.form.getlist("description[]")
        product_ids  = request.form.getlist("product_id[]")
        quantities   = request.form.getlist("quantity[]")
        rates        = request.form.getlist("rate[]")
        gst_rates    = request.form.getlist("item_gst_rate[]")

        errors = []
        cid = None
        pending_guest = None

        if transaction_type not in {"in", "out"}:
            errors.append("Transaction type is invalid.")

        if client_type == "one-time":
            guest_name = request.form.get("guest_name", "").strip()
            guest_email = request.form.get("guest_email", "").strip()
            guest_phone = request.form.get("guest_phone", "").strip()

            if not guest_name:
                errors.append("Vendor/customer name is required for one-time billing.")

            phone_ok, phone_error = validate_phone(guest_phone)
            if not phone_ok:
                errors.append(phone_error)

            email_ok, normalized_email, email_error = validate_email_address(guest_email)
            if not email_ok:
                errors.append(email_error)

            duplicate = find_customer_by_phone(current_user.id, guest_phone) if guest_phone else None
            if duplicate:
                cid = duplicate.id
            elif guest_name:
                pending_guest = {
                    "name": guest_name,
                    "email": normalized_email or None,
                    "phone": guest_phone or None,
                }
        else:
            cid_raw = request.form.get("client_id", "").strip()
            if not cid_raw:
                errors.append("Please select a customer/vendor.")
            else:
                try:
                    selected_id = int(cid_raw)
                    client = Client.query.filter_by(
                        id=selected_id,
                        owner_id=current_user.id,
                        is_active=True,
                    ).first()
                    if not client:
                        errors.append("Selected customer/vendor not found.")
                    else:
                        cid = client.id
                except ValueError:
                    errors.append("Selected customer/vendor is invalid.")

        valid_items = []
        stock_movements = {}
        row_count = max(
            len(item_names),
            len(descriptions),
            len(product_ids),
            len(quantities),
            len(rates),
            len(gst_rates),
        )

        for i in range(row_count):
            row_num = i + 1
            name = item_names[i].strip() if i < len(item_names) else ""
            desc = descriptions[i].strip() if i < len(descriptions) else ""
            product_id_raw = product_ids[i].strip() if i < len(product_ids) else ""
            selected_product = None

            if not name and not product_id_raw:
                continue

            if product_id_raw:
                try:
                    selected_product_id = int(product_id_raw)
                except ValueError:
                    selected_product_id = None
                    errors.append(f"Row {row_num}: Selected product is invalid.")

                if selected_product_id:
                    selected_product = Product.query.filter_by(
                        id=selected_product_id,
                        owner_id=current_user.id,
                        is_active=True,
                    ).first()
                    if not selected_product:
                        errors.append(f"Row {row_num}: Selected product not found.")
                        continue

                if selected_product and not name:
                    name = selected_product.name

            if not name:
                errors.append(f"Row {row_num}: Item name is required.")
                continue

            try:
                qty_raw = quantities[i].strip() if i < len(quantities) else "1"
                rate_raw = rates[i].strip() if i < len(rates) else "0"
                gst_raw = gst_rates[i].strip() if i < len(gst_rates) else "0"
                qty = Decimal(qty_raw or "1").quantize(QUANTITY_PLACES)
                rate = Decimal(rate_raw or "0").quantize(MONEY_PLACES)
                gst = Decimal(gst_raw or "0").quantize(MONEY_PLACES)
                if qty <= 0:
                    errors.append(f"Row {row_num}: Quantity must be > 0.")
                    continue
                if rate < 0:
                    errors.append(f"Row {row_num}: Rate cannot be negative.")
                    continue
                if gst < 0 or gst > 100:
                    errors.append(f"Row {row_num}: GST rate must be between 0 and 100.")
                    continue

                if selected_product and not desc:
                    parts = [f"SKU {selected_product.sku}"]
                    if selected_product.unit:
                        parts.append(f"Unit {selected_product.unit}")
                    if selected_product.hsn_code:
                        parts.append(f"HSN {selected_product.hsn_code}")
                    desc = " | ".join(parts)

                valid_items.append({
                    "name": name,
                    "desc": desc,
                    "qty": qty,
                    "rate": rate,
                    "gst": gst,
                    "product": selected_product,
                    "product_id": selected_product.id if selected_product else None,
                })

                if selected_product:
                    movement = qty if transaction_type == "out" else -qty
                    stock_movements.setdefault(
                        selected_product.id,
                        {"product": selected_product, "delta": Decimal("0")},
                    )
                    stock_movements[selected_product.id]["delta"] += movement
            except (InvalidOperation, ValueError, IndexError):
                errors.append(f"Row {row_num}: Invalid number.")

        if not valid_items:
            errors.append("Add at least one item.")

        for movement in stock_movements.values():
            product = movement["product"]
            current_stock = Decimal(str(product.current_stock or 0))
            if current_stock + movement["delta"] < 0:
                errors.append(
                    f"Only {product.stock_display} is available for {product.name}."
                )

        due_date = None
        if due_raw:
            try:
                due_date = date.fromisoformat(due_raw)
            except ValueError:
                errors.append("Invalid due date.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("bills/create.html",
                clients=clients, products=products, default_due=default_due,
                preselected=int(cid) if cid else preselected,
                form=request.form,
                app_name=current_app.config.get("APP_NAME"),
            )

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
            cid = new_client.id

        bill = Bill(
            owner_id         = current_user.id,
            bill_number      = Bill.next_bill_number(current_user.id),
            client_id        = cid, # Uses either selection or newly created ID
            notes            = notes or None,
            status           = BillStatus.UNPAID,
            due_date         = due_date,
            transaction_type = transaction_type 
        )
        db.session.add(bill)
        db.session.flush()

        for item_data in valid_items:
            item = BillItem(
                bill_id     = bill.id,
                product_id  = item_data["product_id"],
                item_name   = item_data["name"],
                description = item_data["desc"] or None,
                quantity    = item_data["qty"],
                rate        = item_data["rate"],
                gst_rate    = item_data["gst"],
            )
            item.calculate()
            db.session.add(item)

        for movement in stock_movements.values():
            product = movement["product"]
            product.current_stock = Decimal(str(product.current_stock or 0)) + movement["delta"]

        db.session.flush()
        bill.recalculate_totals()

        app = current_app._get_current_object()
        try:
            from utils.bill_pdf import build_and_save_bill_pdf
            _, rel_path = build_and_save_bill_pdf(bill, app)
            bill.pdf_path = rel_path
        except Exception as exc:
            logger.error("Bill PDF failed: %s", exc)

        db.session.commit()
        flash(f"Bill {bill.bill_number} created for {bill.client.name}.", "success")
        _dispatch_bill_email(bill, app)
        return redirect(url_for("bills.view_bill", bill_id=bill.id))

    return render_template("bills/create.html",
        clients=clients, products=products, default_due=default_due,
        preselected=preselected, form={},
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@bills_bp.route("/<int:bill_id>")
@login_required
def view_bill(bill_id):
    bill = _owned_bill(bill_id)
    app = current_app._get_current_object()

    from utils.invoice_branding import resolve_business_branding
    brand = resolve_business_branding(bill.owner_id, app)

    qr_b64 = ""
    try:
        import base64
        from utils.qr import build_upi_qr_bytes

        if brand["upi_id"]:
            qr_bytes = build_upi_qr_bytes(
                upi_id=brand["upi_id"],
                payee_name=brand["company_name"],
                amount=float(bill.grand_total),
                note=bill.bill_number,
            )
            if qr_bytes:
                qr_b64 = base64.b64encode(qr_bytes).decode()
    except Exception:
        pass

    return render_template("bills/view.html",
        bill=bill, qr_b64=qr_b64,
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


@bills_bp.route("/<int:bill_id>/mark-paid", methods=["POST"])
@login_required
def mark_paid(bill_id):
    bill = _owned_bill(bill_id)
    if bill.status == BillStatus.PAID:
        flash(f"{bill.bill_number} is already paid.", "warning")
    else:
        bill.mark_paid()
        db.session.commit()
        flash(f"{bill.bill_number} marked as paid.", "success")
    next_page = request.args.get("next") or url_for("bills.list_bills")
    return redirect(next_page)


@bills_bp.route("/<int:bill_id>/delete", methods=["POST"])
@login_required
def delete_bill(bill_id):
    bill = _owned_bill(bill_id)
    num  = bill.bill_number
    if bill.pdf_path and os.path.exists(bill.pdf_path):
        try:
            os.remove(bill.pdf_path)
        except OSError:
            pass
    db.session.delete(bill)
    db.session.commit()
    flash(f"Bill {num} deleted.", "warning")
    return redirect(url_for("bills.list_bills"))


@bills_bp.route("/<int:bill_id>/download")
@login_required
def download_pdf(bill_id):
    bill = _owned_bill(bill_id)
    app  = current_app._get_current_object()

    try:
        from utils.bill_pdf import build_and_save_bill_pdf
        _, rel_path = build_and_save_bill_pdf(bill, app)
        bill.pdf_path = rel_path
        db.session.commit()
    except Exception as exc:
        flash(f"Could not generate PDF: {exc}", "danger")
        return redirect(url_for("bills.view_bill", bill_id=bill_id))

    return send_file(bill.pdf_path, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"{bill.bill_number}.pdf")


@bills_bp.route("/<int:bill_id>/resend-email", methods=["POST"])
@login_required
def resend_email(bill_id):
    bill = _owned_bill(bill_id)
    _dispatch_bill_email(bill, current_app._get_current_object(), force=True)
    return redirect(url_for("bills.view_bill", bill_id=bill_id))


@bills_bp.route("/calc-row")
@login_required
def calc_row():
    try:
        qty  = float(request.args.get("qty",  1))
        rate = float(request.args.get("rate", 0))
        gst  = float(request.args.get("gst",  0))
    except ValueError:
        return jsonify({"error": "invalid"}), 400

    from decimal import Decimal, ROUND_HALF_UP
    q   = Decimal(str(qty))
    r   = Decimal(str(rate))
    g   = Decimal(str(gst)) / 100
    tot = (q * r).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ga  = (tot * g).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return jsonify({
        "total":      f"{float(tot):.2f}",
        "gst_amount": f"{float(ga):.2f}",
        "item_total": f"{float(tot+ga):.2f}",
    })


def _dispatch_bill_email(bill, app, force=False):
    try:
        from utils.email import send_bill_email
        send_bill_email(bill, app)
        if app.config.get("MAIL_ENABLED", False):
            recipient = bill.client.email or app.config.get("MAIL_FALLBACK_RECIPIENT", "")
            if recipient:
                flash(f"Bill emailed to {recipient}.", "info")
    except Exception as exc:
        logger.exception("Bill email failed for %s: %s", bill.bill_number, exc)
        flash(f"Bill saved but email failed: {exc}", "warning")
