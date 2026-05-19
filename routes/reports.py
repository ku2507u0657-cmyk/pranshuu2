"""
routes/reports.py - Dedicated Reports dashboard for InvoiceFlow.

The app uses a dual-ledger model: Invoice and Bill can both represent money in
or money out. Direction is determined only by transaction_type.
"""

import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO

from flask import Blueprint, Response, current_app, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from models import Bill, Invoice


reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

CENT = Decimal("0.01")
EXCLUDED_STATUSES = {"draft", "cancelled"}
PAID_STATUS = "paid"
UNPAID_STATUSES = {"unpaid", "overdue"}


def _as_decimal(value):
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value):
    return _as_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _sum_money(values):
    return _money(sum((_as_decimal(value) for value in values), Decimal("0.00")))


def _format_money(value):
    value = _money(value)
    sign = "-" if value < 0 else ""
    return f"{sign}\u20b9{abs(value):,.2f}"


def _status(document):
    return (getattr(document, "status", "") or "").strip().lower()


def _effective_status(document):
    return (getattr(document, "effective_status", None) or _status(document)).strip().lower()


def _is_reportable(document):
    return _status(document) not in EXCLUDED_STATUSES


def _is_paid(document):
    return _status(document) == PAID_STATUS


def _is_unpaid_or_overdue(document):
    return _status(document) in UNPAID_STATUSES or _effective_status(document) in UNPAID_STATUSES


def _is_money_in(document):
    return (getattr(document, "transaction_type", "") or "").strip().lower() == "in"


def _is_money_out(document):
    return (getattr(document, "transaction_type", "") or "").strip().lower() == "out"


def _base_amount(document):
    if isinstance(document, Invoice):
        return _as_decimal(document.amount)
    return _as_decimal(document.subtotal)


def _gst_amount(document):
    if isinstance(document, Invoice):
        return _as_decimal(document.gst)
    return _as_decimal(document.total_gst)


def _gross_amount(document):
    if isinstance(document, Invoice):
        return _as_decimal(document.total)
    return _as_decimal(document.grand_total)


def _document_number(document):
    if isinstance(document, Invoice):
        return document.invoice_number
    return document.bill_number


def _document_label(document):
    if isinstance(document, Invoice):
        return "Simple Invoice"
    return "Itemized Bill"


def _document_url(document):
    if isinstance(document, Invoice):
        return url_for("invoices.view_invoice", invoice_id=document.id)
    return url_for("bills.view_bill", bill_id=document.id)


def _status_label(document):
    status = _effective_status(document)
    if status == "paid":
        return "Paid", "status-paid"
    if status == "overdue":
        return "Overdue", "status-overdue"
    if status == "draft":
        return "Draft", "status-draft"
    return "Unpaid", "status-unpaid"


def _created_date(document):
    created_at = getattr(document, "created_at", None)
    if isinstance(created_at, datetime):
        return created_at.date()
    return created_at


def _month_start(day):
    return day.replace(day=1)


def _add_months(month_start, months):
    month_index = (month_start.year * 12) + (month_start.month - 1) + months
    year = month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, 1)


def _datetime_bounds(start_date, end_date):
    start_at = datetime.combine(start_date, time.min)
    end_before = datetime.combine(end_date, time.min)
    return start_at, end_before


def _owned_invoices():
    return (
        Invoice.query.options(joinedload(Invoice.client))
        .filter(Invoice.owner_id == current_user.id)
        .all()
    )


def _owned_bills():
    return (
        Bill.query.options(joinedload(Bill.client))
        .filter(Bill.owner_id == current_user.id)
        .all()
    )


def _build_outstanding_row(document, today):
    due_date = getattr(document, "due_date", None)
    label, css_class = _status_label(document)
    days_overdue = (today - due_date).days if due_date and due_date < today else 0

    return {
        "document_type": _document_label(document),
        "document_number": _document_number(document),
        "client_name": document.client.name if document.client else "Unknown",
        "due_date": due_date,
        "status_label": label,
        "status_class": css_class,
        "amount": _money(_gross_amount(document)),
        "days_overdue": days_overdue,
        "is_overdue": days_overdue > 0 or _effective_status(document) == "overdue",
        "url": _document_url(document),
    }


def _build_top_clients(documents):
    grouped = {}

    for document in documents:
        if not (_is_money_in(document) and _is_paid(document)):
            continue

        client_id = getattr(document, "client_id", None)
        if client_id is None:
            continue

        if client_id not in grouped:
            grouped[client_id] = {
                "client": document.client,
                "client_name": document.client.name if document.client else "Unknown",
                "initials": document.client.initials if document.client else "NA",
                "revenue": Decimal("0.00"),
                "document_count": 0,
            }

        grouped[client_id]["revenue"] += _base_amount(document)
        grouped[client_id]["document_count"] += 1

    leaders = sorted(
        grouped.values(),
        key=lambda row: (-_money(row["revenue"]), row["client_name"].lower()),
    )[:3]

    for row in leaders:
        row["revenue"] = _money(row["revenue"])

    max_revenue = leaders[0]["revenue"] if leaders else Decimal("0.00")
    for row in leaders:
        row["bar_width"] = int((row["revenue"] / max_revenue) * 100) if max_revenue else 0

    return leaders


def _build_chart(documents, today):
    current_month = _month_start(today)
    first_month = _add_months(current_month, -5)
    labels = []
    money_in = []
    money_out = []

    for offset in range(6):
        start = _add_months(first_month, offset)
        end = _add_months(start, 1)
        labels.append(start.strftime("%b %Y"))

        month_documents = [
            document
            for document in documents
            if _created_date(document)
            and start <= _created_date(document) < end
            and _is_reportable(document)
        ]

        money_in.append(
            float(_sum_money(_gross_amount(document) for document in month_documents if _is_money_in(document)))
        )
        money_out.append(
            float(_sum_money(_gross_amount(document) for document in month_documents if _is_money_out(document)))
        )

    return {"labels": labels, "money_in": money_in, "money_out": money_out}


def _build_csv_row(document):
    transaction_type = "Money In" if _is_money_in(document) else "Money Out"
    sign = Decimal("1.00") if _is_money_in(document) else Decimal("-1.00")
    created_at = getattr(document, "created_at", None)
    paid_at = getattr(document, "paid_at", None)
    due_date = getattr(document, "due_date", None)

    return {
        "created_at": created_at,
        "transaction_type": transaction_type,
        "document_format": _document_label(document),
        "document_number": _document_number(document),
        "client": document.client.name if document.client else "",
        "status": _effective_status(document).title(),
        "base_amount": _money(_base_amount(document)),
        "gst": _money(_gst_amount(document)),
        "gross_total": _money(_gross_amount(document)),
        "signed_total": _money(_gross_amount(document) * sign),
        "due_date": due_date,
        "paid_at": paid_at,
        "notes": getattr(document, "notes", None) or "",
    }


@reports_bp.route("/")
@login_required
def index():
    today = date.today()
    forecast_until = today + timedelta(days=30)

    invoices = _owned_invoices()
    bills = _owned_bills()
    documents = [*invoices, *bills]
    reportable_documents = [document for document in documents if _is_reportable(document)]

    money_in_documents = [document for document in reportable_documents if _is_money_in(document)]
    money_out_documents = [document for document in reportable_documents if _is_money_out(document)]

    total_money_in = _sum_money(_gross_amount(document) for document in money_in_documents)
    total_money_out = _sum_money(_gross_amount(document) for document in money_out_documents)
    net_pnl = _money(total_money_in - total_money_out)

    gst_collected = _sum_money(_gst_amount(document) for document in money_in_documents)
    gst_paid = _sum_money(_gst_amount(document) for document in money_out_documents)
    net_gst = _money(gst_collected - gst_paid)

    forecast_money_in = [
        document
        for document in money_in_documents
        if _is_unpaid_or_overdue(document)
        and getattr(document, "due_date", None)
        and today <= document.due_date <= forecast_until
    ]
    forecast_money_out = [
        document
        for document in money_out_documents
        if _is_unpaid_or_overdue(document)
        and getattr(document, "due_date", None)
        and today <= document.due_date <= forecast_until
    ]

    outstanding_documents = [
        document
        for document in money_in_documents
        if _is_unpaid_or_overdue(document)
    ]
    outstanding_rows = [
        _build_outstanding_row(document, today)
        for document in sorted(
            outstanding_documents,
            key=lambda item: (
                getattr(item, "due_date", None) or date.max,
                _document_number(item),
            ),
        )[:5]
    ]

    top_clients = _build_top_clients(reportable_documents)
    chart = _build_chart(reportable_documents, today)

    summary = {
        "total_money_in": total_money_in,
        "total_money_out": total_money_out,
        "net_pnl": net_pnl,
        "net_pnl_label": "Profit" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Break-even",
        "gst_collected": gst_collected,
        "gst_paid": gst_paid,
        "net_gst": net_gst,
        "net_gst_label": "GST Payable" if net_gst > 0 else "Input Credit" if net_gst < 0 else "Settled",
        "expected_inflow": _sum_money(_gross_amount(document) for document in forecast_money_in),
        "upcoming_outflow": _sum_money(_gross_amount(document) for document in forecast_money_out),
        "expected_inflow_count": len(forecast_money_in),
        "upcoming_outflow_count": len(forecast_money_out),
    }

    return render_template(
        "reports.html",
        app_name=current_app.config.get("APP_NAME", "InvoiceFlow"),
        chart=chart,
        forecast_until=forecast_until,
        format_money=_format_money,
        outstanding_documents=outstanding_rows,
        summary=summary,
        today=today,
        top_clients=top_clients,
    )


@reports_bp.route("/export")
@login_required
def export():
    today = date.today()
    month_start = _month_start(today)
    next_month = _add_months(month_start, 1)
    start_at, end_before = _datetime_bounds(month_start, next_month)

    invoices = (
        Invoice.query.options(joinedload(Invoice.client))
        .filter(Invoice.owner_id == current_user.id)
        .filter(Invoice.created_at >= start_at)
        .filter(Invoice.created_at < end_before)
        .all()
    )
    bills = (
        Bill.query.options(joinedload(Bill.client))
        .filter(Bill.owner_id == current_user.id)
        .filter(Bill.created_at >= start_at)
        .filter(Bill.created_at < end_before)
        .all()
    )

    rows = [
        _build_csv_row(document)
        for document in [*invoices, *bills]
        if _is_reportable(document)
    ]
    rows.sort(key=lambda row: (row["created_at"] or datetime.min, row["document_number"]))

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Transaction Date",
            "Transaction Type",
            "Document Format",
            "Document Number",
            "Client / Vendor",
            "Status",
            "Base Amount",
            "GST",
            "Gross Total",
            "Signed Gross Total",
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
                row["document_format"],
                row["document_number"],
                row["client"],
                row["status"],
                f"{row['base_amount']:.2f}",
                f"{row['gst']:.2f}",
                f"{row['gross_total']:.2f}",
                f"{row['signed_total']:.2f}",
                row["due_date"].isoformat() if row["due_date"] else "",
                row["paid_at"].date().isoformat() if row["paid_at"] else "",
                row["notes"],
            ]
        )

    filename = f"invoiceflow_transactions_{month_start.strftime('%Y_%m')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@reports_bp.route("/exports/transactions.csv")
@login_required
def export_transactions_csv():
    return export()
