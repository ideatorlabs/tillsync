"""Reading money cells from exports and APIs.

Handles currency marks (``₹ $ € £ ¥``, ``Rs.``, ``INR``, ``USD``...), Western (``120,000``) and
Indian lakh (``1,20,000``) grouping, European decimal commas (``1.234,56``), refunds written
``-250``, ``(250)`` or ``250-``, a trailing ``/-``, and a lone dash as zero. Anything else is
unreadable and returns None, never zero, so callers can skip and count the row instead of
recording a free sale.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = ["parse_money", "detect_currency"]

_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CODES = ("INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "CAD", "JPY", "NZD", "ZAR", "CHF")
# longest alternatives first so "rupees" goes before "rs"
_MARKS = re.compile(
    r"rupees|rupee|us\$|rs\.?|" + "|".join(c.lower() for c in _CODES) + r"|[₹$€£¥]",
    re.IGNORECASE,
)
_PLAIN = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")
_EURO = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d{1,2})?$|^[+-]?\d+,\d{1,2}$")
_DASHES = {"-", "–", "—"}


def detect_currency(value: object) -> str | None:
    """The ISO code a money cell names (``"₹ 250"`` -> ``"INR"``), or None."""
    s = str(value or "")
    for sym, code in _SYMBOLS.items():
        if sym in s:
            return code
    low = s.lower()
    if "rs" in low or "rupee" in low:
        return "INR"
    for code in _CODES:
        if re.search(rf"\b{code}\b", s, re.IGNORECASE):
            return code
    return None


def parse_money(value: object) -> Decimal | None:
    """A money value as a Decimal, or None when it can't be read (including blank)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if value == value and value not in (float("inf"), float("-inf")) else None
    s = str(value).replace(" ", " ").replace(" ", " ").strip()
    if s in _DASHES:
        return Decimal(0)
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    s = s.replace("/-", "")
    s = _MARKS.sub(" ", s).strip()
    if s.endswith("-") and not s.startswith("-"):
        negative, s = True, s[:-1].strip()  # accounting style "250-"
    s = re.sub(r"^([+-])\s+", r"\1", s)  # "- 250" -> "-250"
    s = s.replace(" ", "") if re.fullmatch(r"[+-]?\d{1,3}( \d{3})+([.,]\d+)?", s) else s
    s = s.replace(".", "").replace(",", ".") if _EURO.match(s) else s.replace(",", "")
    if not _PLAIN.fullmatch(s):
        return None
    try:
        n = Decimal(s)
    except InvalidOperation:  # pragma: no cover - the regex already guarantees a number
        return None
    return -abs(n) if negative else n
