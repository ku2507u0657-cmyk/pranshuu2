"""
routes/clients.py - Customer management CRUD (scoped per owner/user).
The underlying table remains clients so existing invoices and bills keep their
foreign keys intact.
"""

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required

from extensions import db
from models import Bill, Client, Invoice
from utils.customer_validation import (
    find_customer_by_phone,
    validate_email_address,
    validate_gstin,
    validate_phone,
)


clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


def _form_to_client(client):
    data = {
        "name": request.form.get("name", "").strip(),
        "business_name": request.form.get("business_name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "monthly_fee": request.form.get("monthly_fee", "").strip(),
        "gst_number": request.form.get("gst_number", "").strip(),
        "address": request.form.get("address", "").strip(),
        "city": request.form.get("city", "").strip(),
        "state": request.form.get("state", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    errors = []

    if not data["name"]:
        errors.append("Customer name is required.")

    phone_ok, phone_error = validate_phone(data["phone"])
    if not phone_ok:
        errors.append(phone_error)
    elif data["phone"]:
        duplicate = find_customer_by_phone(
            current_user.id,
            data["phone"],
            exclude_id=client.id if client.id else None,
        )
        if duplicate:
            errors.append(f"Mobile number already exists for {duplicate.name}.")

    email_ok, normalized_email, email_error = validate_email_address(data["email"])
    if not email_ok:
        errors.append(email_error)

    gst_ok, normalized_gstin, gst_error = validate_gstin(data["gst_number"])
    if not gst_ok:
        errors.append(gst_error)

    fee_value = None
    if data["monthly_fee"]:
        try:
            fee_value = float(data["monthly_fee"])
            if fee_value < 0:
                errors.append("Monthly fee cannot be negative.")
        except ValueError:
            errors.append("Monthly fee must be a valid number.")

    client.name = data["name"]
    client.business_name = data["business_name"] or None
    client.phone = data["phone"] or None
    client.email = normalized_email or data["email"] or None
    client.monthly_fee = fee_value
    client.gst_number = normalized_gstin or None
    client.address = data["address"] or None
    client.city = data["city"] or None
    client.state = data["state"] or None
    client.notes = data["notes"] or None

    return len(errors) == 0, errors


def _flash_errors(errors):
    for error in errors:
        flash(error, "danger")


def _client_query():
    return Client.query.filter_by(owner_id=current_user.id, is_active=True)


def _build_client_stats():
    base = _client_query()
    return {
        "total": base.count(),
        "with_gst": base.filter(Client.gst_number.isnot(None)).count(),
        "with_business": base.filter(Client.business_name.isnot(None)).count(),
    }


def _get_owned_client(client_id):
    """Return a customer owned by current user or abort 404."""
    return Client.query.filter_by(id=client_id, owner_id=current_user.id).first_or_404()


@clients_bp.route("/")
@login_required
def list_clients():
    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    query = _client_query().order_by(Client.name.asc())

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                Client.name.ilike(like),
                Client.business_name.ilike(like),
                Client.email.ilike(like),
                Client.phone.ilike(like),
                Client.gst_number.ilike(like),
                Client.city.ilike(like),
                Client.state.ilike(like),
            )
        )

    pagination = query.paginate(page=page, per_page=15, error_out=False)
    return render_template(
        "clients/list.html",
        clients=pagination.items,
        pagination=pagination,
        search=search,
        stats=_build_client_stats(),
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@clients_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_client():
    if request.method == "POST":
        client = Client(owner_id=current_user.id)
        ok, errors = _form_to_client(client)
        if not ok:
            _flash_errors(errors)
            return render_template(
                "clients/form.html",
                mode="add",
                client=client,
                app_name=current_app.config.get("APP_NAME"),
            )

        db.session.add(client)
        db.session.commit()
        flash(f"Customer '{client.name}' added.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))

    return render_template(
        "clients/form.html",
        mode="add",
        client=Client(owner_id=current_user.id),
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@clients_bp.route("/<int:client_id>")
@login_required
def view_client(client_id):
    client = _get_owned_client(client_id)
    recent_invoices = client.invoices.order_by(Invoice.created_at.desc()).limit(5).all()
    recent_bills = client.bills.order_by(Bill.created_at.desc()).limit(5).all()

    return render_template(
        "clients/view.html",
        client=client,
        invoice_count=client.invoices.count(),
        bill_count=client.bills.count(),
        recent_invoices=recent_invoices,
        recent_bills=recent_bills,
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@clients_bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = _get_owned_client(client_id)

    if request.method == "POST":
        ok, errors = _form_to_client(client)
        if not ok:
            _flash_errors(errors)
            return render_template(
                "clients/form.html",
                mode="edit",
                client=client,
                app_name=current_app.config.get("APP_NAME"),
            )

        db.session.commit()
        flash(f"Customer '{client.name}' updated.", "success")
        return redirect(url_for("clients.view_client", client_id=client.id))

    return render_template(
        "clients/form.html",
        mode="edit",
        client=client,
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@clients_bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    client = _get_owned_client(client_id)
    name = client.name
    client.is_active = False
    db.session.commit()
    flash(f"Customer '{name}' removed. Invoice history is preserved.", "warning")
    return redirect(url_for("clients.list_clients"))
