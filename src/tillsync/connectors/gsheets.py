"""Google Sheets shared by link: a live source for tools that can only sync to a spreadsheet.

The sheet must be readable without signing in ("Anyone with the link can view", or
File > Share > Publish to web as CSV). Any sheet link is turned into its CSV export URL; the CSV
is then read exactly like a file by :class:`~tillsync.connectors.csv_file.CsvConnector`.
No Google API key or OAuth is involved.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx

from ..automap import AutoMapping
from ..base import BaseConnector, register
from ..models import RawCustomer, RawTransaction
from ..netguard import guarded_client
from .csv_file import CsvConnector

__all__ = ["GoogleSheetsConnector", "to_csv_url", "SheetNotPublicError"]

_SHEET_ID = re.compile(r"/spreadsheets/d/(e/)?([A-Za-z0-9_-]+)")
_GID = re.compile(r"[#&?]gid=(\d+)")


class SheetNotPublicError(RuntimeError):
    """The link returned a sign-in page instead of CSV."""


def to_csv_url(url: str, base: str = "https://docs.google.com") -> str:
    """Turn a Google Sheets link (edit, share, publish or export) into a CSV export URL.

    Links that already ask for CSV, and links that are not Google Sheets, are returned unchanged.
    """
    u = (url or "").strip()
    if not u or any(t in u for t in ("output=csv", "format=csv", "tqx=out:csv")):
        return u
    m = _SHEET_ID.search(u)
    if not m:
        return u
    gid_m = _GID.search(u)
    gid = gid_m.group(1) if gid_m else "0"
    if m.group(1):  # a "published to the web" id (/d/e/...)
        return f"{base}/spreadsheets/d/e/{m.group(2)}/pub?output=csv&gid={gid}"
    return f"{base}/spreadsheets/d/{m.group(2)}/export?format=csv&gid={gid}"


@register("gsheets")
class GoogleSheetsConnector(BaseConnector):
    """Sales (and optionally customers) from publicly readable Google Sheets.

    Accepts every option :class:`CsvConnector` does (``mapping``, ``timezone``,
    ``default_country``...). The download goes through an SSRF-guarded client unless you pass
    your own ``client``.
    """

    name = "gsheets"

    def __init__(
        self,
        url: str,
        *,
        customers_url: str | None = None,
        mapping: Mapping[str, Mapping[str, str]] | AutoMapping | None = None,
        client: httpx.Client | None = None,
        allow_private: bool = False,
        timeout: float = 30.0,
        timezone: str | None = None,
        default_country: str | None = None,
        date_formats: Sequence[str] = (),
        dayfirst: bool | None = None,
        currency: str | None = None,
    ) -> None:
        super().__init__()
        self.url = to_csv_url(url)
        self.customers_url = to_csv_url(customers_url) if customers_url else None
        self.client = client or guarded_client(
            allow_private=allow_private, timeout=timeout, follow_redirects=True
        )
        self._options: dict[str, Any] = {
            "mapping": mapping,
            "timezone": timezone,
            "default_country": default_country,
            "date_formats": date_formats,
            "dayfirst": dayfirst,
            "currency": currency,
            "source": "gsheets",
        }
        self._csv: CsvConnector | None = None

    def _download(self, url: str) -> str:
        r = self.client.get(url)
        r.raise_for_status()
        text = r.text
        head = text.lstrip()[:300].lower()
        if head.startswith("<!doctype html") or "<html" in head:
            raise SheetNotPublicError(
                "the sheet is not publicly readable: share it as 'Anyone with the link can view' "
                "or publish it to the web as CSV"
            )
        return text

    def load(self) -> CsvConnector:
        """Download the sheet(s) now and return the underlying :class:`CsvConnector`."""
        sales = self._download(self.url)
        customers = self._download(self.customers_url) if self.customers_url else None
        self._csv = CsvConnector(text=sales, customers_text=customers, **self._options)
        self._csv.report = self.report  # one report across downloads
        return self._csv

    @property
    def automapping(self) -> AutoMapping | None:
        return self._csv.automapping if self._csv else None

    def test_connection(self) -> bool:
        try:
            return self.load().test_connection()
        except (httpx.HTTPError, SheetNotPublicError, ValueError):
            return False

    def fetch_customers(self, since: datetime | None = None) -> Iterator[RawCustomer]:
        yield from self.load().fetch_customers(since)

    def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
        yield from self.load().fetch_transactions(since)
