"""Phone numbers: normalize to E.164 and recognize hidden or masked numbers.

This is deliberately lightweight (no metadata tables). It keeps an explicit country code
(``+44...`` or ``0044...``), drops a national trunk ``0``, and adds ``default_country`` to bare
national numbers. Use the ``phonenumbers`` package if you need per-country validation.
"""

from __future__ import annotations

import os
import re

__all__ = [
    "digits",
    "normalize_phone",
    "phone_key",
    "is_masked_phone",
    "clean_phone",
    "default_country",
    "DEFAULT_COUNTRY_ENV",
]

DEFAULT_COUNTRY_ENV = "TILLSYNC_DEFAULT_COUNTRY"
_MASK_CHARS = set("xX*•#")
_PHONE_CHARS = re.compile(r"^[\d\s+\-().*•#xX/]+$")


def default_country() -> str:
    """The calling code used for bare national numbers: ``$TILLSYNC_DEFAULT_COUNTRY`` or ``1``."""
    cc = digits(os.environ.get(DEFAULT_COUNTRY_ENV, ""))
    return cc or "1"


def digits(value: object) -> str:
    """Only the digits of ``value``; empty for None."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def is_masked_phone(value: object) -> bool:
    """True for a number a marketplace or POS has hidden (``98******10``, ``XXXXXX3210``).

    A masked cell that still holds a full number (``9*876543210``) is not hidden; see
    :func:`clean_phone`.
    """
    s = str(value or "").strip()
    if not s or not any(c in _MASK_CHARS for c in s) or not _PHONE_CHARS.match(s):
        return False
    return len(digits(s)) < 10


def clean_phone(value: object) -> str:
    """The phone cell with stray mask characters removed when a full number survives.

    Returns ``""`` for blanks and hidden numbers.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    if any(c in _MASK_CHARS for c in s):
        if not _PHONE_CHARS.match(s) or len(digits(s)) < 10:
            return ""
        return ("+" if s.startswith("+") else "") + digits(s)
    return s


def normalize_phone(value: object, default_country_code: str | None = None) -> str | None:
    """Return ``+<country><number>`` or None when the value can't be a phone number.

    ``default_country_code`` is a calling code such as ``"1"``, ``"44"`` or ``"91"``. Without it,
    :func:`default_country` is used.
    """
    s = clean_phone(value)
    if not s or re.search(r"[A-Za-z]", s):
        return None
    cc = digits(default_country_code) or default_country()
    explicit = s.startswith("+")
    if s.startswith("00"):
        s, explicit = s[2:], True
    d = digits(s)
    if not d:
        return None
    if explicit:
        e164 = d
    else:
        d = d.lstrip("0")
        if not d:
            return None
        e164 = d if d.startswith(cc) and len(d) > 10 else cc + d
    if not 8 <= len(e164) <= 15:
        return None
    return "+" + e164


def phone_key(value: object) -> str | None:
    """A matching key that ignores formatting and an optional country code.

    Two writings of the same number (``+1 (202) 555-0101`` and ``202-555-0101``) share a key:
    the last ten digits. Numbers with fewer than seven digits have no key.
    """
    d = digits(clean_phone(value))
    if len(d) < 7:
        return None
    return d[-10:]
