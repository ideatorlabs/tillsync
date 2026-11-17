"""CSV, TSV and Excel files: the fallback that works with every POS that can export."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..automap import AutoMapping, automap
from ..base import BaseConnector, register
from ..models import REFUND, SALE, ImportReport, RawCustomer, RawTransaction
from ..money import detect_currency, parse_money
from ..names import clean_person_name, clean_text
from ..normalize import content_id, identity_key, is_cancelled_status, is_return_document, is_total_row
from ..phone import is_masked_phone, normalize_phone
from ..tables import Table, parse_csv_text, read_table
from ..times import has_time_of_day, parse_datetime

__all__ = ["CsvConnector", "MappingError"]

Mapping_ = Mapping[str, Mapping[str, str]]


class MappingError(ValueError):
    """The mapping is missing a required field or names a column the file doesn't have."""


def _load(path: str | Path | None, text: str | None, table: Table | None, delimiter: str | None,
          sheet: str | int | None) -> Table | None:  # fmt: skip
    if table is not None:
        return table
    if text is not None:
        return parse_csv_text(text, delimiter)
    if path is not None:
        return read_table(path, delimiter=delimiter, sheet=sheet)
    return None


@register("csv")
class CsvConnector(BaseConnector):
    """Customers and bills from exported files.

    Give a sales file (``path``, ``text`` or ``table``). Customers are read from it, one per
    distinct identity, unless you also give a separate customers file (``customers_path``...).

    ``mapping`` is ``{"customer": {field: column}, "transaction": {field: column}}``, an
    ``AutoMapping``, or None to guess it with
    ``automap`` (inspect the guess at ``connector.automapping``).
    Naive times are read in ``timezone`` (IANA name, default UTC); bare phone numbers get
    ``default_country``.
    """

    name = "csv"

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        text: str | None = None,
        table: Table | None = None,
        customers_path: str | Path | None = None,
        customers_text: str | None = None,
        customers_table: Table | None = None,
        mapping: Mapping_ | AutoMapping | None = None,
        timezone: str | None = None,
        default_country: str | None = None,
        date_formats: Sequence[str] = (),
        dayfirst: bool | None = None,
        currency: str | None = None,
        delimiter: str | None = None,
        sheet: str | int | None = None,
        source: str = "csv",
    ) -> None:
        super().__init__()
        self.sales = _load(path, text, table, delimiter, sheet)
        self.customers_table = _load(customers_path, customers_text, customers_table, delimiter, sheet)
        if self.sales is None and self.customers_table is None:
            raise ValueError("give a sales file (path/text/table) or a customers file")
        self.timezone = timezone
        self.default_country = default_country
        self.date_formats = tuple(date_formats)
        self.currency = currency
        self.source = source
        self.automapping: AutoMapping | None = None
        if mapping is None:
            mapping = self._guess()
        if isinstance(mapping, AutoMapping):
            self.automapping = mapping
            if dayfirst is None:
                dayfirst = mapping.dayfirst
            mapping = mapping.mapping()
        self.dayfirst = True if dayfirst is None else dayfirst
        self.customer_map: dict[str, str] = dict(mapping.get("customer") or {})
        self.transaction_map: dict[str, str] = dict(mapping.get("transaction") or {})
        # "Net Amount (INR)" tells us the currency of every amount in the column
        self.currency = self.currency or detect_currency(self.transaction_map.get("amount"))
        self._validate()

    # -- setup ---------------------------------------------------------------
    def _guess(self) -> AutoMapping:
        sales = self.sales or Table(headers=[])
        if self.customers_table is not None:
            return automap(
                sales.headers, sales.rows,
                customer_headers=self.customers_table.headers, customer_rows=self.customers_table.rows,
            )  # fmt: skip
        return automap(sales.headers, sales.rows)

    def _validate(self) -> None:
        if self.sales is not None and self.sales.headers:
            if "occurred_at" not in self.transaction_map:
                raise MappingError("the sales file needs a date column (transaction.occurred_at)")
            self._check_columns(self.transaction_map, self.sales, "transaction")
            if self.customers_table is None:
                self._check_columns(self.customer_map, self.sales, "customer")
        if self.customers_table is not None:
            self._check_columns(self.customer_map, self.customers_table, "customer")

    @staticmethod
    def _check_columns(section: Mapping[str, str], table: Table, label: str) -> None:
        bad = [c for c in section.values() if c not in table.headers]
        if bad:
            raise MappingError(f"{label} mapping names columns not in the file: {bad}")

    @property
    def single_file(self) -> bool:
        return self.customers_table is None

    def test_connection(self) -> bool:
        return bool(
            (self.sales and self.sales.headers) or (self.customers_table and self.customers_table.headers)
        )

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _cell(row: Mapping[str, Any], section: Mapping[str, str], fld: str) -> Any:
        col = section.get(fld)
        return row.get(col) if col else None

    def _time(self, raw: Any) -> datetime | None:
        return parse_datetime(raw, formats=self.date_formats, tz=self.timezone, dayfirst=self.dayfirst)

    def _when(self, row: Mapping[str, Any]) -> tuple[datetime | None, bool]:
        """(the bill's time, whether it has a time of day). A separate time column is joined in."""
        m = self.transaction_map
        raw_date = self._cell(row, m, "occurred_at")
        when = self._time(raw_date)
        if when is None:
            return None, False
        time_known = has_time_of_day(raw_date)
        raw_time = clean_text(self._cell(row, m, "occurred_time"))
        if raw_time and not time_known:
            joined = self._time(f"{clean_text(raw_date)} {raw_time}")
            if joined is not None:
                return joined, True
        return when, time_known

    def _attributes(self, row: Mapping[str, Any], used: set[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in row.items():
            if k in used:
                continue
            s = clean_text(v)
            if s:
                out[k] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else s
        return out

    def _customer_key(self, row: Mapping[str, Any]) -> tuple[str, str]:
        """(key, which field it came from) for a row: id, else phone, else email, else name.

        A hidden phone stops the search (``("", "hidden")``): falling back to the name would invent
        a customer out of an anonymous marketplace order.
        """
        m = self.customer_map
        for fld in ("external_id", "phone", "email", "name"):
            raw = self._cell(row, m, fld)
            if is_masked_phone(raw):
                return "", "hidden"
            if fld == "name":
                raw = clean_person_name(raw)
            key = identity_key(raw, self.default_country)
            if key:
                return key, fld
        return "", ""

    def _customer_from_row(self, row: Mapping[str, Any], when: datetime | None) -> RawCustomer | None:
        m = self.customer_map
        key, _ = self._customer_key(row)
        if not key:
            return None
        email = clean_text(self._cell(row, m, "email")).lower() or None
        first_seen = self._time(self._cell(row, m, "first_seen_at")) if "first_seen_at" in m else None
        return RawCustomer(
            external_id=key,
            name=clean_person_name(self._cell(row, m, "name")),
            phone=normalize_phone(self._cell(row, m, "phone"), self.default_country),
            email=email if email and "@" in email else None,
            first_seen_at=first_seen or when,
            updated_at=when,
            source=self.source,
            attributes={} if self.single_file else self._attributes(row, set(m.values())),
        )

    # -- customers -----------------------------------------------------------
    def fetch_customers(self, since: datetime | None = None) -> Iterator[RawCustomer]:
        if self.single_file:
            yield from self._customers_from_sales(since)
            return
        assert self.customers_table is not None
        report = self.report
        merged: dict[str, RawCustomer] = {}
        table = self.customers_table
        for i, row in enumerate(table.rows):
            line = table.row_number(i)
            if not row:
                continue
            report.rows += 1
            _, from_field = self._customer_key(row)
            if from_field == "hidden":
                report.skip("hidden_phone", line, self._cell(row, self.customer_map, "phone"))
                continue
            cust = self._customer_from_row(row, None)
            if cust is None:
                report.skip("no_identity", line, clean_text(self._cell(row, self.customer_map, "name")))
                continue
            if cust.external_id in merged:
                report.skip("duplicate", line, cust.external_id)
                _merge(merged[cust.external_id], cust)
                continue
            merged[cust.external_id] = cust
        for cust in merged.values():
            if since is None or cust.cursor_time is None or cust.cursor_time >= since:
                report.customers += 1
                yield cust

    def _customers_from_sales(self, since: datetime | None) -> Iterator[RawCustomer]:
        """One customer per identity in the sales file, including people whose bill was left out
        (cancelled, unreadable amount): they are still customers."""
        if self.sales is None:
            return
        merged: dict[str, RawCustomer] = {}
        for row in self.sales.rows:
            if not row or is_total_row(list(row.values())):
                continue
            when, _ = self._when(row)
            cust = self._customer_from_row(row, when)
            if cust is None:
                continue
            if cust.external_id in merged:
                _merge(merged[cust.external_id], cust)
            else:
                merged[cust.external_id] = cust
        for cust in merged.values():
            if since is None or cust.cursor_time is None or cust.cursor_time >= since:
                self.report.customers += 1
                yield cust

    # -- transactions --------------------------------------------------------
    def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
        for tx in self._iter_sales():
            if since is None or tx.cursor_time >= since:
                self.report.transactions += 1
                yield tx

    def _iter_sales(self) -> Iterator[RawTransaction]:
        if self.sales is None or not self.sales.headers:
            return
        report = self.report
        seen_ids: set[str] = set()
        content_seen: dict[str, int] = {}
        used = set(self.transaction_map.values()) | (
            set(self.customer_map.values()) if self.single_file else set()
        )
        table = self.sales
        for i, row in enumerate(table.rows):
            line = table.row_number(i)
            if not row:
                continue
            report.rows += 1
            if is_total_row(list(row.values())):
                report.skip("total_row", line, "")
                continue
            tx = self._transaction(row, line, report, content_seen, used)
            if tx is None:
                continue
            if tx.external_id in seen_ids:
                report.skip("duplicate", line, tx.external_id)
                continue
            seen_ids.add(tx.external_id)
            yield tx

    def _transaction(
        self,
        row: Mapping[str, Any],
        line: int,
        report: ImportReport,
        content_seen: dict[str, int],
        used: set[str],
    ) -> RawTransaction | None:
        m = self.transaction_map
        # amount: no column means a visit list; a column we can't read means skip and count
        amount = None
        raw_amount = self._cell(row, m, "amount")
        if "amount" in m:
            amount = parse_money(raw_amount)
            if amount is None:
                report.skip("bad_amount", line, raw_amount)
                return None
        status = self._cell(row, m, "status")
        if is_cancelled_status(status) and (amount is None or amount >= 0):
            report.skip("cancelled", line, status)
            return None
        kind = SALE
        if amount is not None and amount > 0 and is_return_document(self._cell(row, m, "doc_type")):
            amount = -amount
        if amount is not None and amount < 0:
            kind = REFUND

        raw_date = self._cell(row, m, "occurred_at")
        raw_time = clean_text(self._cell(row, m, "occurred_time"))
        when, time_known = self._when(row)
        if when is None:
            report.skip("bad_date", line, raw_date)
            return None

        # which customer the bill belongs to
        if self.single_file:
            cust, from_field = self._customer_key(row)
            if from_field == "hidden":
                report.warn("hidden_phone", line, self._cell(row, self.customer_map, "phone"))
        else:
            ref = self._cell(row, m, "customer_ref")
            cust = identity_key(ref, self.default_country)
            if is_masked_phone(ref):
                report.warn("hidden_phone", line, ref)

        bill_no = clean_text(self._cell(row, m, "external_id"))
        amount_text = f"{amount:.2f}" if amount is not None else ""
        ext = bill_no or content_id(content_seen, cust, clean_text(raw_date), raw_time, amount_text)
        currency = (
            clean_text(self._cell(row, m, "currency")).upper() or detect_currency(raw_amount) or self.currency
        )
        return RawTransaction(
            external_id=ext,
            customer_external_id=cust,
            occurred_at=when,
            amount=amount,
            kind=kind,
            currency=currency,
            time_known=time_known,
            channel=clean_text(self._cell(row, m, "channel")) or None,
            store_id=clean_text(self._cell(row, m, "store_id")) or None,
            store_name=clean_text(self._cell(row, m, "store_name")) or None,
            source=self.source,
            attributes=self._attributes(row, used),
        )


def _merge(into: RawCustomer, other: RawCustomer) -> None:
    """Fill blanks in ``into`` from ``other`` and widen its time range."""
    into.name = into.name or other.name
    into.phone = into.phone or other.phone
    into.email = into.email or other.email
    if other.first_seen_at and (into.first_seen_at is None or other.first_seen_at < into.first_seen_at):
        into.first_seen_at = other.first_seen_at
    if other.updated_at and (into.updated_at is None or other.updated_at > into.updated_at):
        into.updated_at = other.updated_at
    for k, v in other.attributes.items():
        into.attributes.setdefault(k, v)
