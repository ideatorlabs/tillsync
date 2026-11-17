"""Reading timestamps the way POS exports and APIs actually write them.

Rules:

* An explicit offset or ``Z`` is respected.
* A value with no zone is a wall-clock time in the connector's time zone (``tz``).
* A date with no time of day is midnight in ``tz``; :func:`has_time_of_day` tells you which.
* Epoch seconds and milliseconds are UTC.
* Every result is a timezone-aware ``datetime`` in UTC, or None when the value can't be read.
  A bad date is never silently replaced with "now".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

__all__ = ["parse_datetime", "has_time_of_day", "resolve_tz", "guess_dayfirst", "format_since"]

_TIMES = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M%p", "%H:%M:%S.%f")
_YMD = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
_NAMED = ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%y", "%d %b, %Y")


def _with_times(dates: Iterable[str]) -> list[str]:
    out: list[str] = []
    for d in dates:
        out.extend(f"{d} {t}" for t in _TIMES)
        out.append(f"{d}T%H:%M:%S")
        out.append(d)
    return out


def _numeric(dayfirst: bool) -> list[str]:
    dmy = [f"%d{s}%m{s}%Y" for s in "/-."] + [f"%d{s}%m{s}%y" for s in "/-."]
    mdy = [f"%m{s}%d{s}%Y" for s in "/-."] + [f"%m{s}%d{s}%y" for s in "/-."]
    return dmy + mdy if dayfirst else mdy + dmy


_FORMATS = {
    True: tuple(_with_times(_YMD) + _with_times(_numeric(True)) + _with_times(_NAMED)),
    False: tuple(_with_times(_YMD) + _with_times(_numeric(False)) + _with_times(_NAMED)),
}
_OFFSET_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")
_EPOCH = re.compile(r"^\d{9,13}(\.\d+)?$")


def resolve_tz(tz: str | tzinfo | None) -> tzinfo:
    """A time zone from an IANA name ("Asia/Kolkata"), a tzinfo, or None (UTC)."""
    if tz is None or tz == "" or tz == "UTC":
        return timezone.utc
    if isinstance(tz, tzinfo):
        return tz
    return ZoneInfo(tz)


def has_time_of_day(value: object) -> bool:
    """True when the raw value carries a time of day (not just a date)."""
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return False
    if isinstance(value, (int, float)):
        return True
    s = str(value or "").strip()
    return ":" in s or bool(_EPOCH.match(s))


def _aware(dt: datetime, zone: tzinfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(timezone.utc)


def parse_datetime(
    value: object,
    *,
    formats: Sequence[str] = (),
    tz: str | tzinfo | None = None,
    dayfirst: bool = True,
) -> datetime | None:
    """Parse ``value`` into an aware UTC datetime, or None.

    ``formats`` (strptime patterns) are tried before the built-in ones. ``dayfirst`` decides
    whether ``03/04/2026`` is 3 April (True) or 4 March (False).
    """
    zone = resolve_tz(tz)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return _aware(value, zone)
    if isinstance(value, date):
        return _aware(datetime(value.year, value.month, value.day), zone)
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    s = " ".join(str(value).split())
    if not s:
        return None
    if _EPOCH.match(s):
        return _from_epoch(float(s))
    if len(s) == 8 and s.isdigit():
        try:
            return _aware(datetime.strptime(s, "%Y%m%d"), zone)
        except ValueError:
            return None
    for fmt in formats:
        try:
            return _aware(datetime.strptime(s, fmt), zone)
        except ValueError:
            continue
    iso = s[:-1] + "+00:00" if s[-1:] in ("Z", "z") else s
    try:
        return _aware(datetime.fromisoformat(iso), zone)
    except ValueError:
        pass
    for fmt in _OFFSET_FORMATS:
        try:
            return _aware(datetime.strptime(iso, fmt), zone)
        except ValueError:
            continue
    for fmt in _FORMATS[bool(dayfirst)]:
        try:
            return _aware(datetime.strptime(s, fmt), zone)
        except ValueError:
            continue
    return None


def _from_epoch(n: float) -> datetime | None:
    if n > 1e11:  # milliseconds
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


_SLASHED = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})")


def guess_dayfirst(values: Iterable[object]) -> bool | None:
    """Look at sample dates like ``13/02/2026`` and say whether the day comes first.

    Returns True or False when the sample proves it, None when every value is ambiguous.
    """
    day_first = month_first = 0
    for v in values:
        m = _SLASHED.match(str(v or ""))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            day_first += 1
        elif b > 12 >= a:
            month_first += 1
    if day_first == month_first:
        return None
    return day_first > month_first


def format_since(when: datetime, style: str = "iso") -> str:
    """Render a cursor time for an API's "updated since" parameter.

    ``style`` is ``iso`` (``2026-01-31T10:00:00+00:00``), ``iso_z`` (``...Z``), ``epoch``,
    ``epoch_ms``, or any strftime pattern (applied to the UTC time).
    """
    utc = when.astimezone(timezone.utc)
    if style == "iso":
        return utc.isoformat()
    if style == "iso_z":
        return utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    if style == "epoch":
        return str(int(utc.timestamp()))
    if style == "epoch_ms":
        return str(int(utc.timestamp() * 1000))
    return utc.strftime(style)
