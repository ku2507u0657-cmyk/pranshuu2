"""
utils/bill_pdf.py - Professional bill PDF generation using the shared branding
and template settings used by invoices.
"""

import io
import logging
import os
from datetime import datetime
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from utils.invoice_branding import resolve_business_branding
from utils.pdf_templates import THEMES, _terms_and_signature

logger = logging.getLogger(__name__)

INK = colors.HexColor("#1A1714")
INK_2 = colors.HexColor("#4A4540")
INK_3 = colors.HexColor("#8A8480")
GREEN = colors.HexColor("#16A34A")
GREEN_BG = colors.HexColor("#DCFCE7")
AMBER = colors.HexColor("#D97706")
AMBER_BG = colors.HexColor("#FEF3C7")
RED = colors.HexColor("#DC2626")
RED_BG = colors.HexColor("#FEE2E2")
BG = colors.HexColor("#F7F6F2")
BORDER = colors.HexColor("#E4E0D8")
WHITE = colors.white
INR = "\u20b9"


def _s(styles, name="Normal", **kw):
    return ParagraphStyle(
        f"_bill_{id(kw)}",
        parent=styles.get(name, styles["Normal"]),
        textColor=kw.pop("color", INK),
        **kw,
    )


def _txt(value):
    return escape(str(value or ""))


def _status_colors(status):
    return {
        "paid": (GREEN, GREEN_BG),
        "unpaid": (AMBER, AMBER_BG),
        "overdue": (RED, RED_BG),
        "draft": (INK_3, BG),
    }.get(status, (INK_2, BG))


def _money(value):
    return f"{INR}{float(value or 0):,.2f}"


def build_and_save_bill_pdf(bill, app):
    pdf_bytes = _render(bill, app)
    pdf_folder = app.config.get("PDF_FOLDER", "invoices")
    os.makedirs(pdf_folder, exist_ok=True)

    filename = f"{bill.bill_number}.pdf"
    full_path = os.path.join(pdf_folder, filename)
    rel_path = os.path.join("invoices", filename)

    try:
        with open(full_path, "wb") as fh:
            fh.write(pdf_bytes)
    except OSError as exc:
        logger.error("Could not save bill PDF: %s", exc)

    return pdf_bytes, rel_path


def build_bill_pdf_bytes(bill, app):
    return _render(bill, app)


def _render(bill, app) -> bytes:
    from utils.qr import build_upi_qr_bytes

    brand = resolve_business_branding(bill.owner_id, app)
    theme = THEMES.get(brand["template"], THEMES["modern"])

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    S = lambda **kw: _s(styles, **kw)
    story = []
    W = A4[0] - 40 * mm

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Bill {bill.bill_number}",
        author=brand["company_name"],
    )

    logo_cell = Spacer(1, 1)
    if brand["logo_path"] and os.path.exists(brand["logo_path"]):
        try:
            logo_cell = Image(
                brand["logo_path"],
                width=40 * mm,
                height=16 * mm,
                kind="proportional",
            )
        except Exception:
            pass

    company_alignment = TA_CENTER if theme["centered_company"] else TA_LEFT
    company_lines = [
        Paragraph(
            _txt(brand["company_name"]),
            S(
                fontSize=14,
                fontName=theme["bold_font"],
                color=INK,
                leading=18,
                alignment=company_alignment,
            ),
        )
    ]
    if brand["company_address"]:
        company_lines.append(
            Paragraph(
                _txt(brand["company_address"]),
                S(fontSize=8, color=INK_2, leading=11, alignment=company_alignment),
            )
        )
    contact_parts = [p for p in [brand["company_phone"], brand["company_email"]] if p]
    if contact_parts:
        company_lines.append(
            Paragraph(
                _txt("  |  ".join(contact_parts)),
                S(fontSize=8, color=INK_2, leading=11, alignment=company_alignment),
            )
        )
    if brand["company_gstin"]:
        company_lines.append(
            Paragraph(
                f"GSTIN: {_txt(brand['company_gstin'])}",
                S(fontSize=8, color=INK_3, leading=11, alignment=company_alignment),
            )
        )

    header = Table(
        [
            [
                [logo_cell, Spacer(1, 4)] + company_lines,
                Paragraph(
                    "BILL",
                    S(
                        fontSize=theme["title_size"],
                        fontName=theme["title_font"],
                        color=theme["title_color"],
                        alignment=TA_RIGHT,
                    ),
                ),
            ]
        ],
        colWidths=[W * 0.55, W * 0.45],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header)
    story.append(
        HRFlowable(
            width="100%",
            thickness=theme["header_rule"],
            color=BORDER,
            spaceAfter=8,
        )
    )

    eff = bill.effective_status
    text_color, bg_color = _status_colors(eff)
    badge = Table(
        [
            [
                Paragraph(
                    _txt(eff.upper()),
                    S(
                        fontSize=7.5,
                        fontName=theme["bold_font"],
                        color=text_color,
                        alignment=TA_CENTER,
                    ),
                )
            ]
        ],
        colWidths=[28 * mm],
    )
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg_color),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    meta_rows = [
        [
            Paragraph("Bill No.", S(fontSize=8, color=INK_3)),
            Paragraph(
                _txt(bill.bill_number),
                S(fontSize=10, fontName=theme["bold_font"], alignment=TA_RIGHT),
            ),
        ],
        [
            Paragraph("Issue Date", S(fontSize=8, color=INK_3)),
            Paragraph(
                bill.created_at.strftime("%d %B %Y"),
                S(fontSize=9, alignment=TA_RIGHT),
            ),
        ],
    ]
    if bill.due_date:
        meta_rows.append(
            [
                Paragraph("Due Date", S(fontSize=8, color=INK_3)),
                Paragraph(
                    bill.due_date.strftime("%d %B %Y"),
                    S(fontSize=9, alignment=TA_RIGHT),
                ),
            ]
        )
    meta_rows.append([Paragraph("Status", S(fontSize=8, color=INK_3)), badge])

    meta_tbl = Table(meta_rows, colWidths=[30 * mm, 38 * mm])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    c = bill.client
    bill_lines = [
        Paragraph(
            "BILL TO",
            S(fontSize=7.5, fontName=theme["bold_font"], color=INK_3, leading=12),
        ),
        Paragraph(
            _txt(c.name),
            S(fontSize=11, fontName=theme["bold_font"], color=INK, leading=15),
        ),
    ]
    if c.business_name:
        bill_lines.append(Paragraph(_txt(c.business_name), S(fontSize=9, color=INK_2, leading=13)))
    if c.email:
        bill_lines.append(Paragraph(_txt(c.email), S(fontSize=9, color=INK_2, leading=13)))
    if c.phone:
        bill_lines.append(Paragraph(_txt(c.phone), S(fontSize=9, color=INK_2, leading=13)))
    if c.full_address:
        bill_lines.append(Paragraph(_txt(c.full_address), S(fontSize=8.5, color=INK_2, leading=12)))
    if c.gst_number:
        bill_lines.append(Paragraph(f"GSTIN: {_txt(c.gst_number)}", S(fontSize=8.5, color=INK_3, leading=12)))

    info_tbl = Table([[bill_lines, meta_tbl]], colWidths=[W * 0.52, W * 0.48])
    info_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(info_tbl)
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=12))

    cw = [W * 0.04, W * 0.22, W * 0.18, W * 0.07, W * 0.12, W * 0.07, W * 0.12, W * 0.13]

    def th(text, align=TA_LEFT):
        return Paragraph(
            text,
            S(
                fontSize=7.5,
                fontName=theme["bold_font"],
                color=theme["table_header_fg"],
                alignment=align,
            ),
        )

    def td(text, align=TA_LEFT, bold=False, color=INK_2):
        return Paragraph(
            _txt(text),
            S(
                fontSize=8.5,
                color=color,
                fontName=theme["bold_font"] if bold else theme["body_font"],
                alignment=align,
                leading=12,
            ),
        )

    rows = [
        [
            th("#", TA_CENTER),
            th("Item"),
            th("Description"),
            th("Qty", TA_RIGHT),
            th("Rate", TA_RIGHT),
            th("GST%", TA_CENTER),
            th("GST Amt", TA_RIGHT),
            th("Total", TA_RIGHT),
        ]
    ]

    for i, item in enumerate(bill.items, 1):
        description = item.description or ""
        if getattr(item, "product", None):
            product_bits = [f"SKU {item.product.sku}"]
            if item.product.unit:
                product_bits.append(f"Unit {item.product.unit}")
            if item.product.hsn_code:
                product_bits.append(f"HSN {item.product.hsn_code}")
            product_meta = " | ".join(product_bits)
            if product_meta and product_meta not in description:
                description = f"{description} | {product_meta}" if description else product_meta

        rows.append(
            [
                td(str(i), TA_CENTER),
                td(item.item_name, bold=True, color=INK),
                td(description, color=INK_3),
                td(f"{float(item.quantity):g}", TA_RIGHT),
                td(_money(item.rate), TA_RIGHT),
                td(item.gst_rate_display, TA_CENTER),
                td(_money(item.gst_amount), TA_RIGHT, color=theme["title_color"]),
                td(_money(item.item_total), TA_RIGHT, bold=True, color=INK),
            ]
        )

    items_tbl = Table(rows, colWidths=cw, repeatRows=1)
    item_style = [
        ("BACKGROUND", (0, 0), (-1, 0), theme["table_header_bg"]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if theme["row_alt"]:
        item_style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG]))
    items_tbl.setStyle(TableStyle(item_style))
    story.append(items_tbl)
    story.append(Spacer(1, 4 * mm))

    label_w = 44 * mm
    value_w = 30 * mm
    spacer_w = W - label_w - value_w

    def tot(label, val, bold=False, highlight=False):
        fn = theme["bold_font"] if bold else theme["body_font"]
        tc = WHITE if highlight else INK
        return [
            Paragraph("", S(fontSize=1)),
            Paragraph(label, S(fontSize=9, fontName=fn, color=tc, alignment=TA_RIGHT)),
            Paragraph(val, S(fontSize=9, fontName=fn, color=tc, alignment=TA_RIGHT)),
        ]

    totals_data = [
        tot("Subtotal (excl. GST)", _money(bill.subtotal)),
        tot("Total GST", _money(bill.total_gst)),
        tot("Grand Total", _money(bill.grand_total), bold=True, highlight=theme["highlight_total"]),
    ]

    if bill.notes:
        story.append(Paragraph(f"<b>Notes:</b> {_txt(bill.notes)}", S(fontSize=8.5, color=INK_2)))
        story.append(Spacer(1, 3 * mm))

    totals_tbl = Table(totals_data, colWidths=[spacer_w, label_w, value_w])
    totals_style = [
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (1, 0), (-1, -1), 10),
        ("RIGHTPADDING", (1, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), 0),
        ("TOPPADDING", (0, 0), (0, -1), 0),
        ("BOTTOMPADDING", (0, 0), (0, -1), 0),
        ("LINEABOVE", (1, 0), (-1, 0), 0.75, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if theme["highlight_total"]:
        totals_style += [
            ("LINEABOVE", (1, 2), (-1, 2), 0.75, theme["accent_color"]),
            ("BACKGROUND", (1, 2), (-1, 2), theme["accent_color"]),
        ]
    else:
        totals_style += [
            ("LINEABOVE", (1, 2), (-1, 2), 1.5, theme["accent_color"]),
            ("FONTSIZE", (1, 2), (-1, 2), 10),
        ]
    totals_tbl.setStyle(TableStyle(totals_style))

    qr_bytes = b""
    try:
        if brand["upi_id"]:
            qr_bytes = build_upi_qr_bytes(
                brand["upi_id"],
                brand["company_name"],
                float(bill.grand_total),
                bill.bill_number,
            )
    except Exception:
        pass

    if qr_bytes and brand["upi_id"]:
        qr_img = Image(io.BytesIO(qr_bytes), width=28 * mm, height=28 * mm)
        qr_block = Table(
            [
                [qr_img],
                [
                    Paragraph(
                        "Scan to Pay (UPI)",
                        S(
                            fontSize=7.5,
                            fontName=theme["bold_font"],
                            color=INK_2,
                            alignment=TA_CENTER,
                        ),
                    )
                ],
                [Paragraph(_txt(brand["upi_id"]), S(fontSize=7, color=INK_3, alignment=TA_CENTER))],
            ],
            colWidths=[32 * mm],
        )
        qr_block.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        combined = Table([[totals_tbl, qr_block]], colWidths=[W - 34 * mm, 34 * mm])
        combined.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (1, 0), (1, -1), 8),
                    ("RIGHTPADDING", (1, 0), (1, -1), 0),
                ]
            )
        )
        story.append(combined)
    else:
        story.append(totals_tbl)

    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceBefore=2, spaceAfter=6))
    story.append(Paragraph("Payment Notes", S(fontSize=9, fontName=theme["bold_font"], color=INK_2)))
    story.append(Spacer(1, 3))

    pay_note = f"Please reference <b>{_txt(bill.bill_number)}</b> when making payment."
    if bill.due_date:
        pay_note += f" Payment is due by <b>{bill.due_date.strftime('%d %B %Y')}</b>."
    if brand["upi_id"]:
        pay_note += " You may also scan the UPI QR code to pay instantly via GPay, PhonePe, or BHIM."
    story.append(Paragraph(pay_note, S(fontSize=8.5, color=INK_2, leading=13)))

    story.extend(_terms_and_signature(brand, styles, S, theme, W))

    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=5))
    story.append(
        Paragraph(
            f"{_txt(brand['company_name'])}  &bull;  "
            f"Generated {datetime.utcnow().strftime('%d %b %Y')}  &bull;  "
            f"{_txt(bill.bill_number)}",
            S(fontSize=7.5, color=INK_3, alignment=TA_CENTER),
        )
    )

    doc.build(story)
    return buf.getvalue()
