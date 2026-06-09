"""
routes/products.py - Product inventory CRUD scoped per owner/user.
"""

from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Product


products_bp = Blueprint("products", __name__, url_prefix="/products")

MONEY_PLACES = Decimal("0.01")
STOCK_PLACES = Decimal("0.001")


def _product_query():
    return Product.query.filter_by(owner_id=current_user.id, is_active=True)


def _get_owned_product(product_id):
    return Product.query.filter_by(
        id=product_id,
        owner_id=current_user.id,
        is_active=True,
    ).first_or_404()


def _decimal_from_form(raw, label, errors, *, required=True, minimum=None, maximum=None, places=None):
    value = (raw or "").strip()
    if not value:
        if required:
            errors.append(f"{label} is required.")
        return None

    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        errors.append(f"{label} must be a valid number.")
        return None

    if minimum is not None and parsed < minimum:
        errors.append(f"{label} cannot be less than {minimum}.")
    if maximum is not None and parsed > maximum:
        errors.append(f"{label} cannot be greater than {maximum}.")

    if places is not None:
        parsed = parsed.quantize(places)
    return parsed


def _duplicate_sku(sku, product_id=None):
    query = Product.query.filter_by(owner_id=current_user.id, sku=sku)
    if product_id:
        query = query.filter(Product.id != product_id)
    return query.first()


def _form_to_product(product):
    data = {
        "name": request.form.get("name", "").strip(),
        "sku": request.form.get("sku", "").strip().upper(),
        "category": request.form.get("category", "").strip(),
        "unit": request.form.get("unit", "").strip(),
        "hsn_code": request.form.get("hsn_code", "").strip().upper(),
        "gst_rate": request.form.get("gst_rate", "").strip(),
        "purchase_price": request.form.get("purchase_price", "").strip(),
        "selling_price": request.form.get("selling_price", "").strip(),
        "current_stock": request.form.get("current_stock", "").strip(),
    }

    errors = []

    if not data["name"]:
        errors.append("Product name is required.")
    elif len(data["name"]) > 200:
        errors.append("Product name cannot exceed 200 characters.")

    if not data["sku"]:
        errors.append("Product code/SKU is required.")
    elif len(data["sku"]) > 80:
        errors.append("Product code/SKU cannot exceed 80 characters.")
    elif _duplicate_sku(data["sku"], product.id if product.id else None):
        errors.append("Product code/SKU already exists.")

    if not data["unit"]:
        errors.append("Unit is required.")
    elif len(data["unit"]) > 40:
        errors.append("Unit cannot exceed 40 characters.")

    if data["category"] and len(data["category"]) > 120:
        errors.append("Category cannot exceed 120 characters.")

    if data["hsn_code"]:
        if len(data["hsn_code"]) > 20:
            errors.append("HSN code cannot exceed 20 characters.")
        elif not data["hsn_code"].isalnum():
            errors.append("HSN code can contain only letters and numbers.")

    gst_rate = _decimal_from_form(
        data["gst_rate"],
        "GST rate",
        errors,
        minimum=Decimal("0"),
        maximum=Decimal("100"),
        places=MONEY_PLACES,
    )
    purchase_price = _decimal_from_form(
        data["purchase_price"],
        "Purchase price",
        errors,
        minimum=Decimal("0"),
        places=MONEY_PLACES,
    )
    selling_price = _decimal_from_form(
        data["selling_price"],
        "Selling price",
        errors,
        minimum=Decimal("0.01"),
        places=MONEY_PLACES,
    )
    current_stock = _decimal_from_form(
        data["current_stock"],
        "Current stock",
        errors,
        minimum=Decimal("0"),
        places=STOCK_PLACES,
    )

    product.name = data["name"]
    product.sku = data["sku"]
    product.category = data["category"] or None
    product.unit = data["unit"] or "pcs"
    product.hsn_code = data["hsn_code"] or None
    if gst_rate is not None:
        product.gst_rate = gst_rate
    if purchase_price is not None:
        product.purchase_price = purchase_price
    if selling_price is not None:
        product.selling_price = selling_price
    if current_stock is not None:
        product.current_stock = current_stock

    return len(errors) == 0, errors


def _flash_errors(errors):
    for error in errors:
        flash(error, "danger")


def _build_product_stats():
    base = _product_query()
    return {
        "total": base.count(),
        "in_stock": base.filter(Product.current_stock > 0).count(),
        "low_stock": base.filter(
            Product.current_stock > 0,
            Product.current_stock <= 5,
        ).count(),
        "out_of_stock": base.filter(Product.current_stock <= 0).count(),
    }


def _category_options():
    rows = (
        db.session.query(Product.category)
        .filter(
            Product.owner_id == current_user.id,
            Product.is_active.is_(True),
            Product.category.isnot(None),
            Product.category != "",
        )
        .distinct()
        .order_by(Product.category.asc())
        .all()
    )
    return [row[0] for row in rows]


@products_bp.route("/")
@login_required
def list_products():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    stock_filter = request.args.get("stock", "").strip()
    page = request.args.get("page", 1, type=int)

    query = _product_query().order_by(Product.name.asc())

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                Product.name.ilike(like),
                Product.sku.ilike(like),
                Product.category.ilike(like),
                Product.hsn_code.ilike(like),
            )
        )

    if category:
        query = query.filter(Product.category == category)

    if stock_filter == "in_stock":
        query = query.filter(Product.current_stock > 0)
    elif stock_filter == "low_stock":
        query = query.filter(Product.current_stock > 0, Product.current_stock <= 5)
    elif stock_filter == "out_of_stock":
        query = query.filter(Product.current_stock <= 0)

    pagination = query.paginate(page=page, per_page=15, error_out=False)

    return render_template(
        "products/list.html",
        products=pagination.items,
        pagination=pagination,
        search=search,
        category=category,
        stock_filter=stock_filter,
        categories=_category_options(),
        stats=_build_product_stats(),
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@products_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_product():
    product = Product(
        owner_id=current_user.id,
        unit="pcs",
        gst_rate=Decimal("18.00"),
        purchase_price=Decimal("0.00"),
        selling_price=Decimal("0.00"),
        current_stock=Decimal("0.000"),
    )

    if request.method == "POST":
        ok, errors = _form_to_product(product)
        if not ok:
            _flash_errors(errors)
            return render_template(
                "products/form.html",
                mode="add",
                product=product,
                app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
            )

        try:
            db.session.add(product)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Product code/SKU already exists.", "danger")
            return render_template(
                "products/form.html",
                mode="add",
                product=product,
                app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
            )

        flash(f"Product '{product.name}' added.", "success")
        return redirect(url_for("products.list_products"))

    return render_template(
        "products/form.html",
        mode="add",
        product=product,
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@products_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    product = _get_owned_product(product_id)

    if request.method == "POST":
        ok, errors = _form_to_product(product)
        if not ok:
            _flash_errors(errors)
            return render_template(
                "products/form.html",
                mode="edit",
                product=product,
                app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
            )

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Product code/SKU already exists.", "danger")
            return render_template(
                "products/form.html",
                mode="edit",
                product=product,
                app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
            )

        flash(f"Product '{product.name}' updated.", "success")
        return redirect(url_for("products.list_products"))

    return render_template(
        "products/form.html",
        mode="edit",
        product=product,
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@products_bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    product = _get_owned_product(product_id)
    name = product.name
    product.is_active = False
    db.session.commit()
    flash(f"Product '{name}' removed. Invoice history is preserved.", "warning")
    return redirect(url_for("products.list_products"))
