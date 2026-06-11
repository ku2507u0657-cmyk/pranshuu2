"""
Professional invoice PDF templates — Modern, Classic, Minimal.

Each template shares the same data sections but applies distinct typography
and layout styling for business-ready output.
"""

import io
import logging
import os
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image,
)

from utils.invoice_branding import resolve_business_branding, INVOICE_TEMPLATES

logger = logging.getLogger(__name__)

# ── Shared palette ─────────────────────────────────────────────────────────
INK      = colors.HexColor("#1A1714")
INK_2    = colors.HexColor("#4A4540")
INK_3    = colors.HexColor("#8A8480")
ACCENT   = colors.HexColor("#1D4ED8")
GREEN    = colors.HexColor("#16A34A")
GREEN_BG = colors.HexColor("#DCFCE7")
AMBER    = colors.HexColor("#D97706")
AMBER_BG = colors.HexColor("#FEF3C7")
RED      = colors.HexColor("#DC2626")
RED_BG   = colors.HexColor("#FEE2E2")
BG       = colors.HexColor("#F7F6F2")
BORDER   = colors.HexColor("#E4E0D8")
WHITE    = colors.white


def _status_colors(status):
    return {
        "paid":    (GREEN, GREEN_BG),
        "unpaid":  (AMBER, AMBER_BG),
        "overdue": (RED,   RED_BG),
    }.get(status, (INK_2, BG))


def _s(styles, name="Normal", **kw):
    return ParagraphStyle(
        f"_dyn_{id(kw)}",
        parent=styles.get(name, styles["Normal"]),
        textColor=kw.pop("color", INK),
        **kw,
    )


def _line_item_data(invoice):
    """Build description, qty, rate for the single invoice line."""
    c = invoice.client
    desc = invoice.notes or "Professional Services"
    qty_text = "1"
    rate_text = invoice.amount_display

    if getattr(invoice, "product", None) and invoice.product:
        qty = Decimal(str(invoice.product_quantity or 0))
        qty_text = format(qty.normalize(), "f").rstrip("0").rstrip(".") or "0"
        if invoice.product.unit:
            qty_text = f"{qty_text} {invoice.product.unit}"
        rate_text = invoice.product.selling_price_display
        desc = f"{desc} — SKU {invoice.product.sku}"
    else:
        desc = f"{desc} — {c.name}"

    return desc, qty_text, rate_text


def _build_qr_block(upi_id, company_name, invoice, styles, S):
    """Return UPI QR Table block or None."""
    if not upi_id:
        return None
    try:
        from utils.qr import build_upi_qr_bytes
        qr_bytes = build_upi_qr_bytes(
            upi_id=upi_id,
            payee_name=company_name,
            amount=float(invoice.total),
            note=invoice.invoice_number,
        )
    except Exception as exc:
        logger.error("QR build failed: %s", exc)
        return None

    if not qr_bytes:
        return None

    qr_img = Image(io.BytesIO(qr_bytes), width=28 * mm, height=28 * mm)
    qr_block = Table(
        [
            [qr_img],
            [Paragraph("Scan to Pay (UPI)",
                       S(fontSize=7.5, fontName="Helvetica-Bold",
                         color=INK_2, alignment=TA_CENTER))],
            [Paragraph(upi_id,
                       S(fontSize=7, color=INK_3, alignment=TA_CENTER))],
        ],
        colWidths=[32 * mm],
    )
    qr_block.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return qr_block


def _totals_table(invoice, W, styles, S, theme):
    """Build subtotal / GST / grand total table."""
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

    rows = [
        tot("Subtotal (excl. GST)", invoice.amount_display),
        tot(f"GST @ {invoice.gst_rate_display}", invoice.gst_display),
        tot("Grand Total", invoice.total_display, bold=True, highlight=theme["highlight_total"]),
    ]

    tbl = Table(rows, colWidths=[spacer_w, label_w, value_w])
    style_cmds = [
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
        style_cmds += [
            ("LINEABOVE", (1, 2), (-1, 2), 0.75, theme["accent_color"]),
            ("BACKGROUND", (1, 2), (-1, 2), theme["accent_color"]),
        ]
    else:
        style_cmds += [
            ("LINEABOVE", (1, 2), (-1, 2), 1.5, theme["accent_color"]),
            ("FONTSIZE", (1, 2), (-1, 2), 10),
        ]

    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _terms_and_signature(brand, styles, S, theme, W):
    """Terms & conditions and authorized signature section."""
    blocks = []
    blocks.append(Spacer(1, 6 * mm))
    blocks.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6))

    blocks.append(Paragraph("Terms &amp; Conditions",
                            S(fontSize=9, fontName=theme["bold_font"], color=INK_2)))
    blocks.append(Spacer(1, 3))
    blocks.append(Paragraph(brand["terms_conditions"],
                            S(fontSize=8, color=INK_3, leading=12)))

    blocks.append(Spacer(1, 10 * mm))

    sig_name = brand["authorized_signatory"] or brand["company_name"]
    sig_data = [
        [Paragraph("", S(fontSize=1))],
        [HRFlowable(width=55 * mm, thickness=0.75, color=INK_2)],
        [Paragraph("Authorized Signature",
                   S(fontSize=7.5, fontName=theme["bold_font"], color=INK_3, leading=10))],
        [Paragraph(sig_name,
                   S(fontSize=9, fontName=theme["bold_font"], color=INK, leading=12))],
        [Paragraph(brand["company_name"],
                   S(fontSize=8, color=INK_3, leading=11))],
    ]
    sig_tbl = Table(sig_data, colWidths=[W * 0.45])
    sig_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    blocks.append(sig_tbl)
    return blocks


# ── Template themes ────────────────────────────────────────────────────────

THEMES = {
    "modern": {
        "body_font": "Helvetica",
        "bold_font": "Helvetica-Bold",
        "title_font": "Helvetica-Bold",
        "accent_color": INK,
        "header_bg": INK,
        "table_header_bg": INK,
        "table_header_fg": WHITE,
        "highlight_total": True,
        "title_size": 30,
        "title_color": ACCENT,
        "header_rule": 1,
        "row_alt": True,
        "centered_company": False,
    },
    "classic": {
        "body_font": "Times-Roman",
        "bold_font": "Times-Bold",
        "title_font": "Times-Bold",
        "accent_color": INK,
        "header_bg": WHITE,
        "table_header_bg": BG,
        "table_header_fg": INK,
        "highlight_total": False,
        "title_size": 26,
        "title_color": INK,
        "header_rule": 2,
        "row_alt": False,
        "centered_company": True,
    },
    "minimal": {
        "body_font": "Helvetica",
        "bold_font": "Helvetica-Bold",
        "title_font": "Helvetica",
        "accent_color": INK_2,
        "header_bg": WHITE,
        "table_header_bg": WHITE,
        "table_header_fg": INK_3,
        "highlight_total": False,
        "title_size": 22,
        "title_color": INK_2,
        "header_rule": 0.5,
        "row_alt": False,
        "centered_company": False,
    },
}


def render_invoice_pdf(invoice, app, template=None) -> bytes:
    """Render invoice PDF using the specified or profile-selected template."""
    brand = resolve_business_branding(invoice.owner_id, app)
    tpl = template if template in INVOICE_TEMPLATES else brand["template"]
    theme = THEMES.get(tpl, THEMES["modern"])
    return _render(invoice, app, brand, theme)


def _render(invoice, app, brand, theme) -> bytes:
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    story = []
    S = lambda **kw: _s(styles, **kw)

    W = A4[0] - 40 * mm

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Invoice {invoice.invoice_number}",
        author=brand["company_name"],
    )

    # ── Logo + company header ─────────────────────────────────────────────
    logo_cell = Spacer(1, 1)
    if brand["logo_path"] and os.path.exists(brand["logo_path"]):
        try:
            logo_cell = Image(brand["logo_path"], width=40 * mm, height=16 * mm,
                              kind="proportional")
        except Exception:
            pass

    company_lines = [
        Paragraph(brand["company_name"],
                  S(fontSize=14, fontName=theme["bold_font"], color=INK, leading=18,
                    alignment=TA_CENTER if theme["centered_company"] else TA_LEFT)),
    ]
    if brand["company_address"]:
        company_lines.append(Paragraph(
            brand["company_address"],
            S(fontSize=8, color=INK_2, leading=11,
              alignment=TA_CENTER if theme["centered_company"] else TA_LEFT),
        ))
    contact_parts = [p for p in [brand["company_phone"], brand["company_email"]] if p]
    if contact_parts:
        company_lines.append(Paragraph(
            "  |  ".join(contact_parts),
            S(fontSize=8, color=INK_2, leading=11,
              alignment=TA_CENTER if theme["centered_company"] else TA_LEFT),
        ))
    if brand["company_gstin"]:
        company_lines.append(Paragraph(
            f"GSTIN: {brand['company_gstin']}",
            S(fontSize=8, color=INK_3, leading=11,
              alignment=TA_CENTER if theme["centered_company"] else TA_LEFT),
        ))

    if theme["centered_company"]:
        hdr_data = [[
            [logo_cell, Spacer(1, 4)] + company_lines,
        ], [
            Paragraph("INVOICE",
                      S(fontSize=theme["title_size"], fontName=theme["title_font"],
                        color=theme["title_color"], alignment=TA_CENTER)),
        ]]
        hdr = Table(hdr_data, colWidths=[W])
        hdr.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
    else:
        hdr_data = [[
            [logo_cell, Spacer(1, 4)] + company_lines,
            Paragraph("INVOICE",
                      S(fontSize=theme["title_size"], fontName=theme["title_font"],
                        color=theme["title_color"], alignment=TA_RIGHT)),
        ]]
        hdr = Table(hdr_data, colWidths=[W * 0.55, W * 0.45])
        hdr.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

    story.append(hdr)
    story.append(HRFlowable(width="100%", thickness=theme["header_rule"],
                            color=BORDER, spaceAfter=8))

    # ── Status badge + metadata ─────────────────────────────────────────────
    eff = invoice.effective_status
    tc, bgc = _status_colors(eff)
    badge = Table(
        [[Paragraph(eff.upper(),
                    S(fontSize=7.5, fontName=theme["bold_font"],
                      color=tc, alignment=TA_CENTER))]],
        colWidths=[28 * mm],
    )
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bgc),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))

    meta_rows = [
        [Paragraph("Invoice No.", S(fontSize=8, color=INK_3)),
         Paragraph(invoice.invoice_number,
                   S(fontSize=10, fontName=theme["bold_font"], alignment=TA_RIGHT))],
        [Paragraph("Issue Date", S(fontSize=8, color=INK_3)),
         Paragraph(invoice.created_at.strftime("%d %B %Y"),
                   S(fontSize=9, alignment=TA_RIGHT))],
        [Paragraph("Due Date", S(fontSize=8, color=INK_3)),
         Paragraph(invoice.due_date.strftime("%d %B %Y"),
                   S(fontSize=9, alignment=TA_RIGHT))],
        [Paragraph("Status", S(fontSize=8, color=INK_3)), badge],
    ]
    meta_tbl = Table(meta_rows, colWidths=[30 * mm, 38 * mm])
    meta_tbl.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    c = invoice.client
    bill = [
        Paragraph("BILL TO",
                  S(fontSize=7.5, fontName=theme["bold_font"], color=INK_3, leading=12)),
        Paragraph(c.name,
                  S(fontSize=11, fontName=theme["bold_font"], color=INK, leading=15)),
    ]
    if c.business_name:
        bill.append(Paragraph(c.business_name, S(fontSize=9, color=INK_2, leading=13)))
    if c.email:
        bill.append(Paragraph(c.email, S(fontSize=9, color=INK_2, leading=13)))
    if c.phone:
        bill.append(Paragraph(c.phone, S(fontSize=9, color=INK_2, leading=13)))
    if c.full_address:
        bill.append(Paragraph(c.full_address, S(fontSize=8.5, color=INK_2, leading=12)))
    if c.gst_number:
        bill.append(Paragraph(f"GSTIN: {c.gst_number}",
                              S(fontSize=8.5, color=INK_3, leading=12)))

    info_tbl = Table([[bill, meta_tbl]], colWidths=[W * 0.52, W * 0.48])
    info_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(info_tbl)
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=12))

    # ── Line items (KeepTogether avoids page breaks inside table) ───────────
    cw = [W * 0.52, W * 0.10, W * 0.19, W * 0.19]

    def th(text, align=TA_LEFT):
        return Paragraph(text,
                         S(fontSize=8.5, fontName=theme["bold_font"],
                           color=theme["table_header_fg"], alignment=align))

    def td(text, align=TA_LEFT, bold=False):
        return Paragraph(text,
                         S(fontSize=9, color=INK_2,
                           fontName=theme["bold_font"] if bold else theme["body_font"],
                           alignment=align, leading=13))

    desc, qty_text, rate_text = _line_item_data(invoice)
    items = [
        [th("Description"), th("Qty", TA_CENTER),
         th("Rate", TA_RIGHT), th("Amount", TA_RIGHT)],
        [td(desc), td(qty_text, TA_CENTER), td(rate_text, TA_RIGHT),
         td(invoice.amount_display, TA_RIGHT)],
    ]
    items_tbl = Table(items, colWidths=cw, repeatRows=1)
    item_style = [
        ("BACKGROUND", (0, 0), (-1, 0), theme["table_header_bg"]),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if theme["row_alt"]:
        item_style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG]))
    if not theme["row_alt"] and theme["table_header_bg"] == WHITE:
        item_style.append(("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER))
        item_style.append(("LINEBELOW", (0, 0), (-1, 0), 0.5, BORDER))

    items_tbl.setStyle(TableStyle(item_style))

    from reportlab.platypus import KeepTogether
    story.append(KeepTogether([items_tbl]))
    story.append(Spacer(1, 4 * mm))

    # ── Tax breakdown + QR ──────────────────────────────────────────────────
    tots_tbl = _totals_table(invoice, W, styles, S, theme)
    qr_block = _build_qr_block(brand["upi_id"], brand["company_name"], invoice, styles, S)

    if qr_block:
        combined = Table([[tots_tbl, qr_block]], colWidths=[W - 34 * mm, 34 * mm])
        combined.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (1, 0), (1, -1), 8),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ]))
        story.append(combined)
    else:
        story.append(tots_tbl)

    story.append(Spacer(1, 6 * mm))

    # ── Payment notes ───────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER,
                            spaceBefore=2, spaceAfter=6))
    story.append(Paragraph("Payment Notes",
                           S(fontSize=9, fontName=theme["bold_font"], color=INK_2)))
    story.append(Spacer(1, 3))
    pay_note = (
        f"Please reference <b>{invoice.invoice_number}</b> when making payment. "
        f"Payment is due by <b>{invoice.due_date.strftime('%d %B %Y')}</b>."
    )
    if brand["upi_id"]:
        pay_note += " Scan the UPI QR code to pay via GPay, PhonePe, or BHIM."
    story.append(Paragraph(pay_note, S(fontSize=8.5, color=INK_2, leading=13)))

    # ── Terms & signature ───────────────────────────────────────────────────
    story.extend(_terms_and_signature(brand, styles, S, theme, W))

    # ── Footer ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=5))
    story.append(Paragraph(
        f"{brand['company_name']}  &bull;  "
        f"Generated {datetime.utcnow().strftime('%d %b %Y')}  &bull;  "
        f"{invoice.invoice_number}",
        S(fontSize=7.5, color=INK_3, alignment=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()
