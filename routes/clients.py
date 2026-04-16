"""
routes/clients.py — Client management CRUD (scoped per owner/user).
"""

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app,
)
from flask_login import login_required, current_user
from extensions import db
from models import Client

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


def _form_to_client(client):
    name        = request.form.get("name",        "").strip()
    phone       = request.form.get("phone",       "").strip()
    email       = request.form.get("email",       "").strip()
    monthly_fee = request.form.get("monthly_fee", "").strip()
    gst_number  = request.form.get("gst_number",  "").strip()
    address     = request.form.get("address",     "").strip()
    notes       = request.form.get("notes",       "").strip()

    if not name:
        return False, "Client name is required."

    fee_value = None
    if monthly_fee:
        try:
            fee_value = float(monthly_fee)
            if fee_value < 0:
                return False, "Monthly fee cannot be negative."
        except ValueError:
            return False, "Monthly fee must be a valid number."

    client.name        = name
    client.phone       = phone or None
    client.email       = email or None
    client.monthly_fee = fee_value
    client.gst_number  = gst_number or None
    client.address     = address or None
    client.notes       = notes or None
    return True, ""


def _owned(client_id):
    """Return client if it belongs to current user, else 404."""
    return db.get_or_404(
        Client,
        client_id,
        description="Client not found."
    ) if Client.query.filter_by(id=client_id, owner_id=current_user.id).first() \
    else db.get_or_404(Client, None)   # force 404


def _get_owned_client(client_id):
    """Return client owned by current user or abort 404."""
    client = Client.query.filter_by(id=client_id, owner_id=current_user.id).first_or_404()
    return client


@clients_bp.route("/")
@login_required
def list_clients():
    search = request.args.get("q", "").strip()
    page   = request.args.get("page", 1, type=int)

    query = (Client.query
             .filter_by(owner_id=current_user.id, is_active=True)
             .order_by(Client.name.asc()))

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(Client.name.ilike(like), Client.email.ilike(like),
                   Client.phone.ilike(like), Client.gst_number.ilike(like))
        )

    pagination = query.paginate(page=page, per_page=15, error_out=False)
    return render_template("clients/list.html",
        clients    = pagination.items,
        pagination = pagination,
        search     = search,
        app_name   = current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@clients_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_client():
    if request.method == "POST":
        client = Client(owner_id=current_user.id)
        ok, err = _form_to_client(client)
        if not ok:
            flash(err, "danger")
            return render_template("clients/form.html", mode="add", client=client,
                                   app_name=current_app.config.get("APP_NAME"))
        db.session.add(client)
        db.session.commit()
        flash(f"Client '{client.name}' added.", "success")
        return redirect(url_for("clients.list_clients"))

    return render_template("clients/form.html", mode="add",
                           client=Client(owner_id=current_user.id),
                           app_name=current_app.config.get("APP_NAME", "InvoiceFlow"))


@clients_bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = _get_owned_client(client_id)

    if request.method == "POST":
        ok, err = _form_to_client(client)
        if not ok:
            flash(err, "danger")
            return render_template("clients/form.html", mode="edit", client=client,
                                   app_name=current_app.config.get("APP_NAME"))
        db.session.commit()
        flash(f"Client '{client.name}' updated.", "success")
        return redirect(url_for("clients.list_clients"))

    return render_template("clients/form.html", mode="edit", client=client,
                           app_name=current_app.config.get("APP_NAME", "InvoiceFlow"))


@clients_bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    client = _get_owned_client(client_id)
    name   = client.name
    client.is_active = False
    db.session.commit()
    flash(f"Client '{name}' removed.", "warning")
    return redirect(url_for("clients.list_clients"))
