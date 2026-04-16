"""
routes/main.py — Dashboard with multi-user scoped stats.
"""
import json
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, current_app
from flask_login import login_required, current_user, current_user

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html",
                           app_name=current_app.config.get("APP_NAME", "InvoiceFlow"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    from extensions import db
    from models import Client, Invoice, InvoiceStatus

    uid        = current_user.id
    today      = date.today()
    month_start = today.replace(day=1)

    def inv_q():
        return Invoice.query.filter_by(owner_id=uid)

    revenue_this_month = float(db.session.query(
        db.func.coalesce(db.func.sum(Invoice.total), 0)
    ).filter(Invoice.owner_id == uid, Invoice.status == InvoiceStatus.PAID,
             Invoice.paid_at >= datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    ).scalar())

    prev_start = month_start - relativedelta(months=1)
    revenue_prev_month = float(db.session.query(
        db.func.coalesce(db.func.sum(Invoice.total), 0)
    ).filter(Invoice.owner_id == uid, Invoice.status == InvoiceStatus.PAID,
             Invoice.paid_at >= datetime(prev_start.year, prev_start.month, 1, tzinfo=timezone.utc),
             Invoice.paid_at <  datetime(month_start.year, month_start.month, 1, tzinfo=timezone.utc)
    ).scalar())

    revenue_change_pct = None
    if revenue_prev_month > 0:
        revenue_change_pct = round(
            ((revenue_this_month - revenue_prev_month) / revenue_prev_month) * 100, 1)

    all_unpaid    = inv_q().filter_by(status=InvoiceStatus.UNPAID).all()
    unpaid_count  = len(all_unpaid)
    unpaid_total  = float(sum(inv.total for inv in all_unpaid))
    overdue_count = sum(1 for inv in all_unpaid if inv.is_overdue)
    unpaid_invoices = sorted(all_unpaid, key=lambda i: (not i.is_overdue, i.due_date))[:8]

    total_clients  = Client.query.filter_by(owner_id=uid, is_active=True).count()
    new_clients_30 = Client.query.filter_by(owner_id=uid).filter(
        Client.created_at >= datetime.now(timezone.utc) - relativedelta(days=30)
    ).count()

    total_revenue = float(db.session.query(
        db.func.coalesce(db.func.sum(Invoice.total), 0)
    ).filter(Invoice.owner_id == uid, Invoice.status == InvoiceStatus.PAID).scalar())

    total_issued = float(db.session.query(
        db.func.coalesce(db.func.sum(Invoice.total), 0)
    ).filter(Invoice.owner_id == uid).scalar())

    collection_rate = round((total_revenue / total_issued) * 100, 1) if total_issued > 0 else 0

    # 12-month chart data
    chart_labels  = []
    chart_revenue = []
    chart_issued  = []
    for i in range(11, -1, -1):
        mo_start = month_start - relativedelta(months=i)
        mo_end   = mo_start + relativedelta(months=1)
        chart_labels.append(mo_start.strftime("%b '%y"))
        paid = float(db.session.query(
            db.func.coalesce(db.func.sum(Invoice.total), 0)
        ).filter(Invoice.owner_id == uid, Invoice.status == InvoiceStatus.PAID,
                 Invoice.paid_at >= datetime(mo_start.year, mo_start.month, 1, tzinfo=timezone.utc),
                 Invoice.paid_at <  datetime(mo_end.year, mo_end.month, 1, tzinfo=timezone.utc)
        ).scalar())
        issued = float(db.session.query(
            db.func.coalesce(db.func.sum(Invoice.total), 0)
        ).filter(Invoice.owner_id == uid,
                 Invoice.created_at >= datetime(mo_start.year, mo_start.month, 1, tzinfo=timezone.utc),
                 Invoice.created_at <  datetime(mo_end.year, mo_end.month, 1, tzinfo=timezone.utc)
        ).scalar())
        chart_revenue.append(round(paid,   2))
        chart_issued.append(round(issued,  2))

    from models import Client as C
    top_clients_raw = (
        db.session.query(C, db.func.coalesce(db.func.sum(Invoice.total), 0).label("total_billed"),
                         db.func.count(Invoice.id).label("invoice_count"))
        .outerjoin(Invoice, db.and_(Invoice.client_id == C.id, Invoice.owner_id == uid))
        .filter(C.owner_id == uid)
        .group_by(C.id).order_by(db.text("total_billed DESC")).limit(5).all()
    )
    top_clients = [{"client": c, "total_billed": float(t), "invoice_count": cnt}
                   for c, t, cnt in top_clients_raw]

    recent_invoices = inv_q().order_by(Invoice.created_at.desc()).limit(5).all()

    return render_template("dashboard.html",
        stats={"revenue_this_month": revenue_this_month,
               "revenue_change_pct": revenue_change_pct,
               "unpaid_count": unpaid_count, "unpaid_total": unpaid_total,
               "overdue_count": overdue_count, "total_clients": total_clients,
               "new_clients_30": new_clients_30, "total_revenue": total_revenue,
               "collection_rate": collection_rate},
        unpaid_invoices = unpaid_invoices,
        recent_invoices = recent_invoices,
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
