"""Telling real names from placeholders and codes."""

from __future__ import annotations

import re

__all__ = ["clean_person_name", "is_placeholder", "reads_like_name", "clean_text"]

# what people type into an empty cell
_PLACEHOLDER_VALUES = {"-", "--", "---", "—", "–", "na", "n/a", "nil", "null", "none", "nan", "#n/a", "?"}

# what a cashier types when they don't know the customer
_PLACEHOLDER_NAMES = {
    "abc", "xyz", "test", "testing", "guest", "customer", "walk in", "walk-in", "walkin", "unknown",
    "no name", "noname", "cash", "cash sale", "cash customer", "parcel", "takeaway",
    "take away", "anonymous", "sir", "madam", "mr", "mrs", "ms", "self",
}  # fmt: skip


def clean_text(value: object) -> str:
    """The cell as stripped text, or ``""`` when it is empty or only a placeholder (``-``, ``N/A``)."""
    s = " ".join(str(value if value is not None else "").split())
    return "" if s.lower() in _PLACEHOLDER_VALUES else s


def is_placeholder(value: object) -> bool:
    """True for an empty cell or a stand-in name such as ``Guest``, ``Walk-in``, ``A`` or ``10``."""
    return not clean_person_name(value)


def clean_person_name(value: object) -> str:
    """The name as written, or ``""`` when it is a placeholder rather than a person's name.

    A name needs at least two letters and must not be a known stand-in (``Guest``, ``Cash``,
    ``Walk-in``...) or only digits and punctuation.
    """
    s = clean_text(value)
    if not s:
        return ""
    letters = sum(ch.isalpha() for ch in s)
    if letters < 2 or s.lower().strip(".") in _PLACEHOLDER_NAMES or re.fullmatch(r"[\d\s.,+\-()/#]+", s):
        return ""
    return s


def reads_like_name(value: object) -> bool:
    """True when a value reads like a name (``Main Street``, ``Downtown``) rather than a code (``ST-004``).

    Used to decide whether an outlet/store column holds names or ids.
    """
    v = str(value or "").strip()
    if not v:
        return False
    if any(c.isspace() for c in v):
        return sum(c.isalpha() for c in v) >= 2
    return sum(c.isalpha() for c in v) >= 4 and not any(c.isdigit() for c in v)
