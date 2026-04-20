"""
models.py — SQLAlchemy models for InvoiceFlow
Multi-user: every Client, Invoice, Bill is scoped to an Admin owner.
"""

from datetime import datetime, date, timezone
from decimal import Decimal, ROUND_HALF_UP
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


GST_RATE = Decimal("0.18")


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────
#  Admin  (Flask-Login principal + Google OAuth)
# ─────────────────────────────────────────────────────────────

class Admin(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "admins"

    id            = db.Column(db.Integer,     primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=True)

    # Google OAuth
    google_id    = db.Column(db.String(128), unique=True, nullable=True, index=True)
    email        = db.Column(db.String(255), unique=True, nullable=True, index=True)
    display_name = db.Column(db.String(200), nullable=True)
    avatar_url   = db.Column(db.String(500), nullable=True)

    def set_password(self, plaintext):
        self.password_hash = generate_password_hash(plaintext)

    def check_password(self, plaintext):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, plaintext)

    @property
    def has_password(self):
        return bool(self.password_hash)

    @property
    def login_method(self):
        if self.google_id and self.password_hash:
            return "Google + Password"
        if self.google_id:
            return "Google"
        return "Password"

    def __repr__(self):
        return f"<Admin id={self.id} username={self.username!r}>"


# ─────────────────────────────────────────────────────────────
#  Client  (scoped to owner Admin)
# ─────────────────────────────────────────────────────────────

class Client(db.Model):
    __tablename__ = "clients"

    id          = db.Column(db.Integer,        primary_key=True)
    owner_id    = db.Column(db.Integer,        db.ForeignKey("admins.id"),
                             nullable=False, index=True)            # multi-user FK
    name        = db.Column(db.String(200),    nullable=False, index=True)
    phone       = db.Column(db.String(50),     nullable=True)
    email       = db.Column(db.String(255),    nullable=True, index=True)
    monthly_fee = db.Column(db.Numeric(10, 2), nullable=True, default=0.00)
    gst_number  = db.Column(db.String(30),     nullable=True)
    address     = db.Column(db.Text,           nullable=True)
    notes       = db.Column(db.Text,           nullable=True)
    is_active   = db.Column(db.Boolean,        default=True, nullable=False)
    created_at  = db.Column(db.DateTime,       nullable=False,
                             default=lambda: datetime.now(timezone.utc))

    owner    = db.relationship("Admin", backref=db.backref("clients", lazy="dynamic"))
    invoices = db.relationship("Invoice", back_populates="client",
                               lazy="dynamic", cascade="all, delete-orphan")
    bills    = db.relationship("Bill", backref="client", lazy="dynamic",
                               cascade="all, delete-orphan")

    @property
    def monthly_fee_display(self):
        if self.monthly_fee is None:
            return "—"
        return f"\u20b9{float(self.monthly_fee):,.2f}"

    @property
    def initials(self):
        parts = self.name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.name[:2].upper()

    def __repr__(self):
        return f"<Client id={self.id} name={self.name!r}>"

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "phone": self.phone,
            "email": self.email,
            "monthly_fee": float(self.monthly_fee) if self.monthly_fee else 0.0,
            "gst_number": self.gst_number, "address": self.address,
            "is_active": self.is_active, "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────
#  Invoice  (scoped to owner Admin)
# ─────────────────────────────────────────────────────────────

class InvoiceStatus:
    UNPAID  = "unpaid"
    PAID    = "paid"
    OVERDUE = "overdue"
    ALL     = [UNPAID, PAID, OVERDUE]


class Invoice(db.Model):
    __tablename__ = "invoices"

    id             = db.Column(db.Integer,        primary_key=True)
    owner_id       = db.Column(db.Integer,        db.ForeignKey("admins.id"),
                                nullable=False, index=True)         # multi-user FK
    invoice_number = db.Column(db.String(20),     unique=True, nullable=False, index=True)
    client_id      = db.Column(db.Integer,        db.ForeignKey("clients.id"),
                                nullable=False, index=True)
    amount         = db.Column(db.Numeric(10, 2), nullable=False)
    gst            = db.Column(db.Numeric(10, 2), nullable=False)
    gst_rate       = db.Column(db.Numeric(5, 2),  nullable=False, default=18.00)
    total          = db.Column(db.Numeric(10, 2), nullable=False)
    due_date       = db.Column(db.Date,           nullable=False)
    status         = db.Column(db.String(10),     nullable=False,
                                default=InvoiceStatus.UNPAID, index=True)
    pdf_path       = db.Column(db.String(300),    nullable=True)
    notes          = db.Column(db.Text,           nullable=True)
    is_recurring   = db.Column(db.Boolean,        default=False, nullable=False)
    paid_at        = db.Column(db.DateTime,       nullable=True)
    created_at     = db.Column(db.DateTime,       nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    owner  = db.relationship("Admin", backref=db.backref("invoices", lazy="dynamic"))
    client = db.relationship("Client", back_populates="invoices")

    @classmethod
    def next_invoice_number(cls, owner_id=None):
        """Sequential invoice number scoped per owner."""
        q = cls.query
        if owner_id:
            q = q.filter_by(owner_id=owner_id)
        last = q.order_by(cls.id.desc()).with_entities(cls.invoice_number).first()
        if last is None:
            return "INV-0001"
        try:
            seq = int(last[0].split("-")[1]) + 1
        except (IndexError, ValueError):
            seq = cls.query.count() + 1
        return f"INV-{seq:04d}"

    @classmethod
    def calculate_gst(cls, amount, rate=None):
        base    = Decimal(str(amount))
        gst_pct = Decimal(str(rate)) / Decimal("100") if rate is not None else GST_RATE
        gst     = (base * gst_pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return gst, base + gst

    @property
    def is_overdue(self):
        return self.status == InvoiceStatus.UNPAID and self.due_date < date.today()

    @property
    def effective_status(self):
        return InvoiceStatus.OVERDUE if self.is_overdue else self.status

    @property
    def status_label(self):
        return {
            InvoiceStatus.PAID:    ("Paid",    "status-paid"),
            InvoiceStatus.UNPAID:  ("Unpaid",  "status-unpaid"),
            InvoiceStatus.OVERDUE: ("Overdue", "status-overdue"),
        }.get(self.effective_status, ("Unknown", ""))

    @property
    def gst_rate_display(self):
        rate = float(self.gst_rate) if self.gst_rate is not None else 18.0
        return "0% (Exempt)" if rate == 0 else f"{rate:g}%"

    @property
    def amount_display(self):
        return f"\u20b9{float(self.amount):,.2f}"

    @property
    def gst_display(self):
        return f"\u20b9{float(self.gst):,.2f}"

    @property
    def total_display(self):
        return f"\u20b9{float(self.total):,.2f}"

    def mark_paid(self):
        self.status  = InvoiceStatus.PAID
        self.paid_at = datetime.now(timezone.utc)

    def __repr__(self):
        return f"<Invoice {self.invoice_number} status={self.status!r}>"

    def to_dict(self):
        return {
            "id": self.id, "invoice_number": self.invoice_number,
            "client_id": self.client_id,
            "client_name": self.client.name if self.client else None,
            "amount": float(self.amount), "gst": float(self.gst),
            "gst_rate": float(self.gst_rate) if self.gst_rate else 18.0,
            "total": float(self.total),
            "due_date": self.due_date.isoformat(),
            "status": self.effective_status,
            "is_recurring": self.is_recurring,
            "created_at": self.created_at.isoformat(),
        }


# ─────────────────────────────────────────────────────────────
#  Bill  (scoped to owner Admin)
# ─────────────────────────────────────────────────────────────

class BillStatus:
    DRAFT  = "draft"
    UNPAID = "unpaid"
    PAID   = "paid"
    ALL    = [DRAFT, UNPAID, PAID]


class Bill(db.Model):
    __tablename__ = "bills"

    id          = db.Column(db.Integer,        primary_key=True)
    owner_id    = db.Column(db.Integer,        db.ForeignKey("admins.id"),
                             nullable=False, index=True)            # multi-user FK
    bill_number = db.Column(db.String(20), nullable=False, index=True)
    client_id   = db.Column(db.Integer,        db.ForeignKey("clients.id"),
                             nullable=False, index=True)
    subtotal    = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total_gst   = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    grand_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes       = db.Column(db.Text,           nullable=True)
    status      = db.Column(db.String(10),     nullable=False,
                             default=BillStatus.UNPAID, index=True)
    pdf_path    = db.Column(db.String(300),    nullable=True)
    due_date    = db.Column(db.Date,           nullable=True)
    paid_at     = db.Column(db.DateTime,       nullable=True)
    created_at  = db.Column(db.DateTime,       nullable=False,
                             default=lambda: datetime.now(timezone.utc))

    owner = db.relationship("Admin", backref=db.backref("bills", lazy="dynamic"))
    items = db.relationship("BillItem", back_populates="bill",
                             cascade="all, delete-orphan", order_by="BillItem.id")
    __table_args__ = (
    db.UniqueConstraint('owner_id', 'bill_number', name='unique_user_bill'),
)

    @classmethod
    def next_bill_number(cls, owner_id):
        q = cls.query.filter_by(owner_id=owner_id)

        last = q.order_by(cls.id.desc()).with_entities(cls.bill_number).first()

        if last is None:
           return "BILL-0001"

        try:
            seq = int(last[0].split("-")[1]) + 1
        except (IndexError, ValueError):
            seq = q.count() + 1

        return f"BILL-{seq:04d}"

    def recalculate_totals(self):
        subtotal  = sum(item.total_amount for item in self.items)
        total_gst = sum(item.gst_amount   for item in self.items)
        self.subtotal    = subtotal
        self.total_gst   = total_gst
        self.grand_total = subtotal + total_gst

    @property
    def is_overdue(self):
        return (self.status == BillStatus.UNPAID
                and self.due_date and self.due_date < date.today())

    @property
    def effective_status(self):
        return "overdue" if self.is_overdue else self.status

    @property
    def status_label(self):
        return {
            "paid":    ("Paid",    "status-paid"),
            "unpaid":  ("Unpaid",  "status-unpaid"),
            "overdue": ("Overdue", "status-overdue"),
            "draft":   ("Draft",   "status-draft"),
        }.get(self.effective_status, ("Unknown", ""))

    @property
    def subtotal_display(self):
        return f"\u20b9{float(self.subtotal):,.2f}"

    @property
    def total_gst_display(self):
        return f"\u20b9{float(self.total_gst):,.2f}"

    @property
    def grand_total_display(self):
        return f"\u20b9{float(self.grand_total):,.2f}"

    def mark_paid(self):
        self.status  = BillStatus.PAID
        self.paid_at = datetime.now(timezone.utc)

    def __repr__(self):
        return f"<Bill {self.bill_number} status={self.status!r}>"


class BillItem(db.Model):
    __tablename__ = "bill_items"

    id           = db.Column(db.Integer,        primary_key=True)
    bill_id      = db.Column(db.Integer,        db.ForeignKey("bills.id"),
                              nullable=False, index=True)
    item_name    = db.Column(db.String(200),    nullable=False)
    description  = db.Column(db.String(300),    nullable=True)
    quantity     = db.Column(db.Numeric(10, 3), nullable=False, default=1)
    rate         = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    gst_rate     = db.Column(db.Numeric(5, 2),  nullable=False, default=0)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    gst_amount   = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    item_total   = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    bill = db.relationship("Bill", back_populates="items")

    def calculate(self):
        qty     = Decimal(str(self.quantity))
        rate    = Decimal(str(self.rate))
        gst_pct = Decimal(str(self.gst_rate)) / Decimal("100")
        total   = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        gst_amt = (total * gst_pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.total_amount = total
        self.gst_amount   = gst_amt
        self.item_total   = total + gst_amt

    @property
    def rate_display(self):
        return f"\u20b9{float(self.rate):,.2f}"

    @property
    def total_display(self):
        return f"\u20b9{float(self.item_total):,.2f}"

    @property
    def gst_rate_display(self):
        return f"{float(self.gst_rate):g}%"

    def __repr__(self):
        return f"<BillItem bill_id={self.bill_id} name={self.item_name!r}>"


class BusinessProfile(db.Model):
    __tablename__ = "business_profiles"

    id = db.Column(db.Integer, primary_key=True)

    owner_id = db.Column(db.Integer, db.ForeignKey("admins.id"), nullable=False, unique=True)

    business_name = db.Column(db.String(200), nullable=False)
    owner_name = db.Column(db.String(200), nullable=True)
    upi_id = db.Column(db.String(100), nullable=True)
    gst_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(120), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("Admin", backref=db.backref("business_profile", uselist=False))
