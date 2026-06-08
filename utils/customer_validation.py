"""
Shared validation helpers for customer/contact data.
"""

import re


GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
EMAIL_FALLBACK_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(value):
    """Return a comparable phone digit string, or an empty string."""
    digits = re.sub(r"\D+", "", value or "")
    digits = digits.lstrip("0")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def validate_phone(value):
    """Validate an optional mobile number and return (is_valid, message)."""
    if not value:
        return True, ""
    digits = normalize_phone(value)
    if len(digits) < 7 or len(digits) > 15:
        return False, "Mobile number must contain between 7 and 15 digits."
    return True, ""


def validate_email_address(value):
    """Validate an optional email address and return (is_valid, normalized, message)."""
    email = (value or "").strip()
    if not email:
        return True, "", ""

    try:
        from email_validator import EmailNotValidError, validate_email

        result = validate_email(email, check_deliverability=False)
        return True, result.normalized, ""
    except ImportError:
        if EMAIL_FALLBACK_RE.match(email):
            return True, email, ""
        return False, email, "Email address format is invalid."
    except EmailNotValidError as exc:
        return False, email, str(exc)


def validate_gstin(value):
    """Validate an optional Indian GSTIN and return (is_valid, normalized, message)."""
    gstin = (value or "").strip().upper()
    if not gstin:
        return True, "", ""
    if not GSTIN_RE.match(gstin):
        return False, gstin, "GST number format is invalid. Use a valid 15-character GSTIN."
    return True, gstin, ""


def find_customer_by_phone(owner_id, phone, exclude_id=None):
    """Find an active customer owned by owner_id with the same normalized phone."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None

    from models import Client

    query = Client.query.filter(
        Client.owner_id == owner_id,
        Client.is_active.is_(True),
        Client.phone.isnot(None),
    )
    if exclude_id:
        query = query.filter(Client.id != exclude_id)

    for customer in query.all():
        if normalize_phone(customer.phone) == normalized:
            return customer
    return None
