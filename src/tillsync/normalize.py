"""Small rules shared by the connectors: identity keys, cancelled and return bills, content ids."""

from __future__ import annotations

import hashlib
import re

from .names import clean_text
from .phone import digits, is_masked_phone, normalize_phone

__all__ = ["identity_key", "is_cancelled_status", "is_return_document", "content_id", "is_total_row"]

CANCELLED = {"cancelled", "canceled", "cancel", "void", "voided", "deleted", "rejected", "refunded", "failed"}
_RETURN = re.compile(r"\breturn|credit\s*note|refund", re.IGNORECASE)
_PHONEISH = re.compile(r"^\+?[\d\s\-().]{7,20}$")
_EMAILISH = re.compile(r"^[^@\s]+@[^@\s]+$")


def identity_key(value: object, default_country: str | None = None) -> str:
    """A customer key that is the same however the source wrote it.

    Phone-like values become E.164, emails are lower-cased, anything else is stripped text.
    Hidden phones (``98******10``) and placeholders give ``""``.
    """
    s = clean_text(value)
    if not s or is_masked_phone(s):
        return ""
    if _EMAILISH.match(s):
        return s.lower()
    if _PHONEISH.match(s) and len(digits(s)) >= 7:
        return normalize_phone(s, default_country) or s
    return s


def is_cancelled_status(value: object) -> bool:
    return clean_text(value).lower() in CANCELLED


def is_return_document(value: object) -> bool:
    """True for a document type such as ``Sales Return`` or ``Credit Note``."""
    return bool(_RETURN.search(clean_text(value)))


def is_total_row(values: list[object]) -> bool:
    """A footer such as ``Total, , , 12,345`` that summarizes the rows above it."""
    first = next((clean_text(v) for v in values if clean_text(v)), "")
    return first.lower().rstrip(":") in {"total", "grand total", "totals", "sub total", "subtotal"}


def content_id(seen: dict[str, int], *parts: object) -> str:
    """A stable id for a bill that has no number: what it is, plus its place among identical rows.

    Re-reading the same file gives the same ids, so a re-import updates instead of duplicating.
    """
    base = "|".join(str(p).strip().lower() for p in parts)
    n = seen[base] = seen.get(base, 0) + 1
    return "auto-" + hashlib.sha1(f"{base}|{n}".encode()).hexdigest()[:16]
