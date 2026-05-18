"""
routes/reports.py -- Financial reports for InvoiceFlow.

Step 3 scope:
- Combined transaction CSV export for invoices and bills.
- GST Summary Report comparing GST collected vs GST paid.
"""

import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    render_template,
    request,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from models import Bill, Client, Invoice


reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _money(value):
    return value or Decimal("0.00")


def _format_money(value):
    return f"\u20b9{float(_money(value)):,.2f}"


def _default_start_date():
    today = date.today()
    return today.replace(day=1)


def _parse_report_dates():
    today = date.today()
    start_raw = request.args.get("start_date", "").strip()
    end_raw = request.args.get("end_date", "").strip()

    try:
        start_date = date.fromisoformat(start_raw) if start_raw else _default_start_date()
    except ValueError:
        flash("Start date was invalid, so this month was used.", "warning")
        start_date = _default_start_date()

    try:
        end_date = date.fromisoformat(end_raw) if end_raw else today
    except ValueError:
        flash("End date was invalid, so today was used.", "warning")
        end_date = today

    if start_date > end_date:
        flash("Start date cannot be after end date.", "warning")
        start_date, end_date = end_date, start_date

    return start_date, end_date


def _datetime_bounds(start_date, end_date):
    start_at = datetime.combine(start_date, time.min)
    end_before = datetime.combine(end_date + timedelta(days=1), time.min)
    return start_at, end_before


def _invoice_period_query(start_at, end_before):
    return (
        Invoice.query.filter(Invoice.owner_id == current_user.id)
        .filter(Invoice.created_at >= start_at)
        .filter(Invoice.created_at < end_before)
    )


def _bill_period_query(start_at, end_before):
    return (
        Bill.query.filter(Bill.owner_id == current_user.id)
        .filter(Bill.created_at >= start_at)
        .filter(Bill.created_at < end_before)
    )


@reports_bp.route("/")
@login_required
def index():
    start_date, end_date = _parse_report_dates()
    start_at, end_before = _datetime_bounds(start_date, end_date)

    invoice_base = _invoice_period_query(start_at, end_before)
    bill_base = _bill_period_query(start_at, end_before)

    gst_collected = _money(
        invoice_base.with_entities(func.coalesce(func.sum(Invoice.gst), 0)).scalar()
    )
    gst_paid = _money(
        bill_base.with_entities(func.coalesce(func.sum(Bill.total_gst), 0)).scalar()
    )
    invoice_subtotal = _money(
        invoice_base.with_entities(func.coalesce(func.sum(Invoice.amount), 0)).scalar()
    )
    invoice_total = _money(
        invoice_base.with_entities(func.coalesce(func.sum(Invoice.total), 0)).scalar()
    )
    bill_subtotal = _money(
        bill_base.with_entities(func.coalesce(func.sum(Bill.subtotal), 0)).scalar()
    )
    bill_total = _money(
        bill_base.with_entities(func.coalesce(func.sum(Bill.grand_total), 0)).scalar()
    )

    net_gst = gst_collected - gst_paid
    gst_position_label = "GST Payable" if net_gst >= 0 else "Input Credit"

    gst_summary = {
        "gst_collected": gst_collected,
        "gst_paid": gst_paid,
        "net_gst": net_gst,
        "position_label": gst_position_label,
        "invoice_subtotal": invoice_subtotal,
        "invoice_total": invoice_total,
        "bill_subtotal": bill_subtotal,
        "bill_total": bill_total,
        "invoice_count": invoice_base.count(),
        "bill_count": bill_base.count(),
    }

    recent_invoices = (
        invoice_base.join(Client)
        .order_by(Invoice.created_at.desc())
        .limit(5)
        .all()
    )
    recent_bills = (
        bill_base.join(Client)
        .order_by(Bill.created_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "reports.html",
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
        start_date=start_date,
        end_date=end_date,
        gst_summary=gst_summary,
        recent_invoices=recent_invoices,
        recent_bills=recent_bills,
        format_money=_format_money,
    )


@reports_bp.route("/exports/transactions.csv")
@login_required
def export_transactions_csv():
    start_date, end_date = _parse_report_dates()
    start_at, end_before = _datetime_bounds(start_date, end_date)

    invoices = (
        _invoice_period_query(start_at, end_before)
        .join(Client)
        .order_by(Invoice.created_at.asc())
        .all()
    )
    bills = (
        _bill_period_query(start_at, end_before)
        .join(Client)
        .order_by(Bill.created_at.asc())
        .all()
    )

    rows = []
    for invoice in invoices:
        rows.append(
            {
                "created_at": invoice.created_at,
                "transaction_type": "Money In",
                "document_type": "Invoice",
                "document_number": invoice.invoice_number,
                "client": invoice.client.name if invoice.client else "",
                "status": invoice.effective_status,
                "subtotal": invoice.amount,
                "gst": invoice.gst,
                "total": invoice.total,
                "signed_total": invoice.total,
                "due_date": invoice.due_date,
                "paid_at": invoice.paid_at,
                "notes": invoice.notes or "",
            }
        )

    for bill in bills:
        rows.append(
            {
                "created_at": bill.created_at,
                "transaction_type": "Money Out",
                "document_type": "Bill",
                "document_number": bill.bill_number,
                "client": bill.client.name if bill.client else "",
                "status": bill.effective_status,
                "subtotal": bill.subtotal,
                "gst": bill.total_gst,
                "total": bill.grand_total,
                "signed_total": -_money(bill.grand_total),
                "due_date": bill.due_date,
                "paid_at": bill.paid_at,
                "notes": bill.notes or "",
            }
        )

    rows.sort(key=lambda row: row["created_at"] or datetime.min)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Transaction Date",
            "Transaction Type",
            "Document Type",
            "Document Number",
            "Client / Vendor",
            "Status",
            "Subtotal",
            "GST",
            "Total",
            "Signed Total",
            "Due Date",
            "Paid At",
            "Notes",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row["created_at"].date().isoformat() if row["created_at"] else "",
                row["transaction_type"],
                row["document_type"],
                row["document_number"],
                row["client"],
                row["status"],
                f"{float(_money(row['subtotal'])):.2f}",
                f"{float(_money(row['gst'])):.2f}",
                f"{float(_money(row['total'])):.2f}",
                f"{float(_money(row['signed_total'])):.2f}",
                row["due_date"].isoformat() if row["due_date"] else "",
                row["paid_at"].date().isoformat() if row["paid_at"] else "",
                row["notes"],
            ]
        )

    filename = f"transactions_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
