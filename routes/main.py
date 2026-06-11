"""
routes/main.py — Dashboard with multi-user scoped stats (Cash Flow Edition).
"""
import json
import os
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, current_app, request, redirect, url_for, send_from_directory, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import BusinessProfile
from extensions import db
from utils.invoice_branding import INVOICE_TEMPLATES, DEFAULT_TERMS, logo_url

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html",
                           app_name=current_app.config.get("APP_NAME", "InvoiceFlow"))


@main_bp.route("/dashboard")
@login_required
def dashboard(): 

    # 🚨 Force setup
    profile = BusinessProfile.query.filter_by(owner_id=current_user.id).first()
    if not profile:
        return redirect(url_for("main.settings_profile"))

    from extensions import db
    from models import Client, Invoice, InvoiceStatus, Bill

    uid        = current_user.id
    today      = date.today()
    month_start = today.replace(day=1)

    def inv_q():
        return Invoice.query.filter_by(owner_id=uid)
        
    def bill_q():
        return Bill.query.filter_by(owner_id=uid)

    # Grab bill statuses first
    try:
        from models import BillStatus
        bill_paid = BillStatus.PAID
        bill_unpaid = BillStatus.UNPAID
    except ImportError:
        bill_paid = 'paid'
        bill_unpaid = 'unpaid'

    # ─── 1. MONEY IN (Revenue this month) ──────────────────────────
    inv_money_in = float(db.session.query(
        db.func.coalesce(db.func.sum(Invoice.total), 0)
    ).filter(
        Invoice.owner_id == uid, 
        Invoice.status == InvoiceStatus.PAID,
        Invoice.transaction_type == 'in',
        Invoice.paid_at >= datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    ).scalar())

    bill_money_in = float(db.session.query(
        db.func.coalesce(db.func.sum(Bill.grand_total), 0)
    ).filter(
        Bill.owner_id == uid, 
        Bill.status == bill_paid,
        Bill.transaction_type == 'in',
        Bill.paid_at >= datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    ).scalar())

    money_in = inv_money_in + bill_money_in

    # ─── 2. MONEY OUT (Expenses this month) ────────────────────────
    inv_money_out = float(db.session.query(
        db.func.coalesce(db.func.sum(Invoice.total), 0)
    ).filter(
        Invoice.owner_id == uid, 
        Invoice.status == InvoiceStatus.PAID,
        Invoice.transaction_type == 'out',
        Invoice.paid_at >= datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    ).scalar())

    bill_money_out = float(db.session.query(
        db.func.coalesce(db.func.sum(Bill.grand_total), 0)
    ).filter(
        Bill.owner_id == uid, 
        Bill.status == bill_paid,
        Bill.transaction_type == 'out',
        Bill.paid_at >= datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    ).scalar())

    money_out = inv_money_out + bill_money_out

    # ─── 3. PENDING COLLECTIONS (Unpaid Money In) ──────────────────
    unpaid_inv_in = inv_q().filter_by(status=InvoiceStatus.UNPAID, transaction_type='in').all()
    unpaid_bill_in = bill_q().filter_by(status=bill_unpaid, transaction_type='in').all()
    
    unpaid_count = len(unpaid_inv_in) + len(unpaid_bill_in)
    unpaid_total = float(sum(i.total for i in unpaid_inv_in) + sum(b.grand_total for b in unpaid_bill_in))
    
    # Send unpaid invoices to the HTML table to preserve layout
    unpaid_invoices = sorted(unpaid_inv_in, key=lambda i: (not i.is_overdue, i.due_date))[:8]

    # ─── 4. PENDING PAYMENTS (Unpaid Money Out) ────────────────────
    unpaid_bill_out = bill_q().filter_by(status=bill_unpaid, transaction_type='out').all()
    unpaid_bills = sorted(unpaid_bill_out, key=lambda b: (not getattr(b, 'is_overdue', False), getattr(b, 'due_date', datetime.max)))[:8]

    # ─── 5. RECENT ACTIVITY ─────────────────────────────────────────
    recent_invoices = inv_q().order_by(Invoice.created_at.desc()).limit(5).all()
    recent_bills    = bill_q().order_by(Bill.created_at.desc()).limit(5).all()

    # ─── 6. CHARTS & TOTALS (Strictly checking 'in' transactions) ───
    total_clients  = Client.query.filter_by(owner_id=uid, is_active=True).count()
    new_clients_30 = Client.query.filter_by(owner_id=uid).filter(
        Client.created_at >= datetime.now(timezone.utc) - relativedelta(days=30)
    ).count()

    # Total All-Time Revenue (Money In)
    tr_inv = float(db.session.query(db.func.coalesce(db.func.sum(Invoice.total), 0)).filter(
        Invoice.owner_id == uid, Invoice.status == InvoiceStatus.PAID, Invoice.transaction_type == 'in'
    ).scalar())
    tr_bill = float(db.session.query(db.func.coalesce(db.func.sum(Bill.grand_total), 0)).filter(
        Bill.owner_id == uid, Bill.status == bill_paid, Bill.transaction_type == 'in'
    ).scalar())
    total_revenue = tr_inv + tr_bill

    # Total All-Time Issued (Money In)
    ti_inv = float(db.session.query(db.func.coalesce(db.func.sum(Invoice.total), 0)).filter(
        Invoice.owner_id == uid, Invoice.transaction_type == 'in'
    ).scalar())
    ti_bill = float(db.session.query(db.func.coalesce(db.func.sum(Bill.grand_total), 0)).filter(
        Bill.owner_id == uid, Bill.transaction_type == 'in'
    ).scalar())
    total_issued = ti_inv + ti_bill

    collection_rate = round((total_revenue / total_issued) * 100, 1) if total_issued > 0 else 0

    # 12-Month Chart Loop (Money In ONLY)
    chart_labels  = []
    chart_revenue = []
    chart_issued  = []
    
    for i in range(11, -1, -1):
        mo_start = month_start - relativedelta(months=i)
        mo_end   = mo_start + relativedelta(months=1)
        chart_labels.append(mo_start.strftime("%b '%y"))
        
        # Collected this month (Paid)
        p_inv = float(db.session.query(db.func.coalesce(db.func.sum(Invoice.total), 0)).filter(
            Invoice.owner_id == uid, Invoice.status == InvoiceStatus.PAID, Invoice.transaction_type == 'in',
            Invoice.paid_at >= datetime(mo_start.year, mo_start.month, 1, tzinfo=timezone.utc),
            Invoice.paid_at <  datetime(mo_end.year, mo_end.month, 1, tzinfo=timezone.utc)
        ).scalar())
        p_bill = float(db.session.query(db.func.coalesce(db.func.sum(Bill.grand_total), 0)).filter(
            Bill.owner_id == uid, Bill.status == bill_paid, Bill.transaction_type == 'in',
            Bill.paid_at >= datetime(mo_start.year, mo_start.month, 1, tzinfo=timezone.utc),
            Bill.paid_at <  datetime(mo_end.year, mo_end.month, 1, tzinfo=timezone.utc)
        ).scalar())
        
        # Issued this month
        i_inv = float(db.session.query(db.func.coalesce(db.func.sum(Invoice.total), 0)).filter(
            Invoice.owner_id == uid, Invoice.transaction_type == 'in',
            Invoice.created_at >= datetime(mo_start.year, mo_start.month, 1, tzinfo=timezone.utc),
            Invoice.created_at <  datetime(mo_end.year, mo_end.month, 1, tzinfo=timezone.utc)
        ).scalar())
        i_bill = float(db.session.query(db.func.coalesce(db.func.sum(Bill.grand_total), 0)).filter(
            Bill.owner_id == uid, Bill.transaction_type == 'in',
            Bill.created_at >= datetime(mo_start.year, mo_start.month, 1, tzinfo=timezone.utc),
            Bill.created_at <  datetime(mo_end.year, mo_end.month, 1, tzinfo=timezone.utc)
        ).scalar())
        
        chart_revenue.append(round(p_inv + p_bill, 2))
        chart_issued.append(round(i_inv + i_bill, 2))

    # Top Clients (By Total Money In)
    top_clients_raw = []
    for c in Client.query.filter_by(owner_id=uid).all():
        inv_sum = db.session.query(db.func.sum(Invoice.total)).filter(Invoice.client_id == c.id, Invoice.transaction_type == 'in').scalar() or 0
        bill_sum = db.session.query(db.func.sum(Bill.grand_total)).filter(Bill.client_id == c.id, Bill.transaction_type == 'in').scalar() or 0
        total_b = float(inv_sum) + float(bill_sum)
        
        inv_cnt = Invoice.query.filter_by(client_id=c.id, transaction_type='in').count()
        bill_cnt = Bill.query.filter_by(client_id=c.id, transaction_type='in').count()
        
        if total_b > 0:
            top_clients_raw.append({"client": c, "total_billed": total_b, "invoice_count": inv_cnt + bill_cnt})
    
    top_clients = sorted(top_clients_raw, key=lambda x: x["total_billed"], reverse=True)[:5]


    # Send everything to the HTML template
    return render_template("dashboard.html",
        stats={
            "money_in": money_in,
            "money_out": money_out,
            "unpaid_count": unpaid_count, 
            "unpaid_total": unpaid_total,
            "total_clients": total_clients,
            "new_clients_30": new_clients_30, 
            "total_revenue": total_revenue,
            "collection_rate": collection_rate
        },
        unpaid_invoices = unpaid_invoices,
        unpaid_bills    = unpaid_bills,      
        recent_invoices = recent_invoices,
        recent_bills    = recent_bills,      
        top_clients     = top_clients,
        chart_labels    = json.dumps(chart_labels),
        chart_revenue   = json.dumps(chart_revenue),
        chart_issued    = json.dumps(chart_issued),
        doughnut_data   = json.dumps([round(total_revenue, 2), round(unpaid_total, 2)]),
        today           = today,
        app_name        = current_app.config.get("APP_NAME", "InvoiceFlow"),
    )


@main_bp.route("/health")
def health():
    from extensions import db
    try:
        db.session.execute(db.text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"
    return {"status": "ok", "database": db_status,
            "app": current_app.config.get("APP_NAME")}


ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def _save_logo_upload(profile, file_storage, app):
    """Validate and persist an uploaded company logo. Returns error message or None."""
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        return "Logo must be PNG, JPG, GIF, or WebP."

    file_storage.seek(0, os.SEEK_END)
    size_mb = file_storage.tell() / (1024 * 1024)
    file_storage.seek(0)
    max_mb = app.config.get("MAX_LOGO_SIZE_MB", 2)
    if size_mb > max_mb:
        return f"Logo must be smaller than {max_mb} MB."

    upload_root = app.config.get("UPLOAD_FOLDER", "uploads")
    logos_dir = os.path.join(upload_root, "logos")
    os.makedirs(logos_dir, exist_ok=True)

    stored_name = f"owner_{profile.owner_id}.{ext}"
    rel_path = os.path.join("logos", stored_name)
    full_path = os.path.join(upload_root, rel_path)

    if profile.logo_path and profile.logo_path != rel_path:
        old_path = os.path.join(upload_root, profile.logo_path)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    file_storage.save(full_path)
    profile.logo_path = rel_path
    return None


@main_bp.route("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    """Serve uploaded files (e.g. company logos) for the current user."""
    upload_root = current_app.config.get("UPLOAD_FOLDER", "uploads")
    profile = BusinessProfile.query.filter_by(owner_id=current_user.id).first()
    if not profile or not profile.logo_path or profile.logo_path != filename:
        return "Forbidden", 403

    subdir = os.path.dirname(filename)
    directory = os.path.join(upload_root, subdir) if subdir else upload_root
    return send_from_directory(directory, os.path.basename(filename))


@main_bp.route("/settings/profile", methods=["GET", "POST"])
@login_required
def settings_profile():
    profile = BusinessProfile.query.filter_by(owner_id=current_user.id).first()
    app = current_app._get_current_object()

    if request.method == "POST":
        if not profile:
            profile = BusinessProfile(owner_id=current_user.id)

        profile.business_name = request.form.get("business_name", "").strip()
        profile.owner_name = request.form.get("owner_name", "").strip()
        profile.upi_id = request.form.get("upi_id", "").strip() or None
        profile.gst_number = request.form.get("gst_number", "").strip() or None
        profile.phone = request.form.get("phone", "").strip() or None
        profile.email = request.form.get("email", "").strip() or None
        profile.address = request.form.get("address", "").strip() or None
        profile.authorized_signatory = request.form.get("authorized_signatory", "").strip() or None
        profile.terms_conditions = request.form.get("terms_conditions", "").strip() or None

        template = request.form.get("invoice_template", "modern").strip()
        profile.invoice_template = template if template in INVOICE_TEMPLATES else "modern"

        logo_error = _save_logo_upload(profile, request.files.get("logo"), app)
        if logo_error:
            flash(logo_error, "danger")
            return render_template(
                "setup_business.html",
                profile=profile,
                logo_url=logo_url(profile, app),
                templates=_template_choices(),
                default_terms=DEFAULT_TERMS,
                max_logo_mb=app.config.get("MAX_LOGO_SIZE_MB", 2),
                app_name=app.config.get("APP_NAME", "InvoiceFlow"),
            )

        db.session.add(profile)
        db.session.commit()
        flash("Business profile saved.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template(
        "setup_business.html",
        profile=profile,
        logo_url=logo_url(profile, app) if profile else None,
        templates=_template_choices(),
        default_terms=DEFAULT_TERMS,
        max_logo_mb=app.config.get("MAX_LOGO_SIZE_MB", 2),
        app_name=app.config.get("APP_NAME", "InvoiceFlow"),
    )


def _template_choices():
    return [
        ("modern", "Modern", "Bold accent header, dark table, clean layout"),
        ("classic", "Classic", "Traditional serif style with formal borders"),
        ("minimal", "Minimal", "Light typography with maximum whitespace"),
    ]