"""Any REST/JSON API, described by configuration instead of code.

A config says how to authenticate, where the customer and order lists live, how they page, and
which JSON path holds each field. Example (see ``examples/http_config.json`` for a full one)::

    {
      "base_url": "https://pos.example.com",
      "auth": {"type": "bearer", "token_env": "POS_TOKEN"},
      "timezone": "Asia/Kolkata",
      "customers": {
        "path": "/api/customers",
        "records": "data",
        "since_param": "updated_since",
        "pagination": {"type": "page", "param": "page", "size_param": "per_page", "size": 100},
        "fields": {"external_id": "id", "name": ["first_name", "last_name"], "phone": "contact.mobile",
                   "email": "contact.email", "first_seen_at": "created_at", "updated_at": "updated_at"}
      },
      "transactions": {
        "path": "/api/orders",
        "records": "data",
        "pagination": {"type": "cursor", "param": "cursor", "next_path": "meta.next_cursor"},
        "fields": {"external_id": "number", "customer_ref": "customer.id", "occurred_at": "closed_at",
                   "amount": "total", "status": "state",
                   "line_items": {"path": "items", "name": "title", "quantity": "qty", "unit_price": "price"}}
      }
    }

Auth types: ``bearer`` (``token``), ``basic`` (``username``/``password``), ``header``
(``name``/``value``) and ``query`` (``name``/``value``). Any secret can be given as
``<key>_env`` to read it from an environment variable instead of the config.

Pagination types: ``none``, ``page`` (``param``, ``start``, ``size_param``, ``size``,
``total_path`` or ``total_pages_path``), ``offset`` (``param``, ``size_param``, ``size``),
``cursor`` (``param``, ``next_path``) and ``next_url`` (``next_path``, or the ``Link`` header).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from ..base import BaseConnector, register
from ..models import REFUND, SALE, ImportReport, LineItem, RawCustomer, RawTransaction
from ..money import detect_currency, parse_money
from ..names import clean_person_name, clean_text
from ..netguard import BlockedURLError, guarded_client
from ..normalize import content_id, identity_key, is_cancelled_status, is_return_document
from ..phone import is_masked_phone, normalize_phone
from ..times import format_since, has_time_of_day, parse_datetime

__all__ = ["HttpJsonConnector", "ConnectorError", "dig", "map_customer", "map_transaction"]

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.IGNORECASE)


class ConnectorError(RuntimeError):
    """The API answered with something the config can't read, or paging went in circles."""


def dig(obj: Any, path: str | Sequence[str] | None) -> Any:
    """Read a dot path such as ``"customer.address.city"`` or ``"items.0.name"``; None when missing.

    A list of paths joins the non-empty values with a space (``["first_name", "last_name"]``).
    """
    if path is None or path == "":
        return None
    if not isinstance(path, str):
        parts = [clean_text(dig(obj, p)) for p in path]
        return " ".join(p for p in parts if p) or None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


class _Options:
    """Per-connector reading options shared by the record mappers."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        self.tz: str | None = cfg.get("timezone")
        self.country: str | None = cfg.get("default_country")
        self.formats: tuple[str, ...] = tuple(cfg.get("date_formats") or ())
        self.dayfirst: bool = bool(cfg.get("dayfirst", True))
        self.currency: str | None = cfg.get("currency")
        self.divisor = Decimal(str(cfg.get("amount_divisor", 1)))
        self.source: str = cfg.get("source", "http")

    def time(self, raw: Any) -> datetime | None:
        return parse_datetime(raw, formats=self.formats, tz=self.tz, dayfirst=self.dayfirst)

    def money(self, raw: Any) -> Decimal | None:
        n = parse_money(raw)
        if n is None or self.divisor == 1:
            return n
        return n / self.divisor


def _attributes(rec: Any, paths: Sequence[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in paths or ():
        v = dig(rec, p)
        if v not in (None, ""):
            out[p] = v
    return out


def map_customer(
    rec: Any,
    fields: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
    report: ImportReport | None = None,
) -> RawCustomer | None:
    """Map one JSON object to a ``RawCustomer`` using ``fields`` (field -> path).

    Useful on its own for webhook payloads. Returns None when the record has no identity.
    """
    opt = _Options(options or {})
    phone_raw = dig(rec, fields.get("phone"))
    email = clean_text(dig(rec, fields.get("email"))).lower() or None
    name = clean_person_name(dig(rec, fields.get("name")))
    key = identity_key(dig(rec, fields.get("external_id")), opt.country)
    if not key and not is_masked_phone(phone_raw):
        key = identity_key(phone_raw, opt.country) or identity_key(email) or name
    if not key:
        if report is not None:
            report.skip("hidden_phone" if is_masked_phone(phone_raw) else "no_identity", 0, name)
        return None
    return RawCustomer(
        external_id=key,
        name=name,
        phone=normalize_phone(phone_raw, opt.country),
        email=email if email and "@" in email else None,
        first_seen_at=opt.time(dig(rec, fields.get("first_seen_at"))),
        updated_at=opt.time(dig(rec, fields.get("updated_at"))),
        source=opt.source,
        attributes=_attributes(rec, fields.get("attributes")),
    )


def _line_items(rec: Any, spec: Mapping[str, Any] | None, opt: _Options) -> list[LineItem]:
    if not spec:
        return []
    raw = dig(rec, spec.get("path", "items"))
    if not isinstance(raw, list):
        return []
    out: list[LineItem] = []
    for item in raw:
        name = clean_text(dig(item, spec.get("name", "name")))
        if not name:
            continue
        qty = parse_money(dig(item, spec.get("quantity", "quantity")))
        out.append(
            LineItem(
                name=name,
                quantity=qty if qty is not None else Decimal(1),
                unit_price=opt.money(dig(item, spec.get("unit_price"))),
                total=opt.money(dig(item, spec.get("total"))),
                sku=clean_text(dig(item, spec.get("sku"))) or None,
                category=clean_text(dig(item, spec.get("category"))) or None,
                attributes=_attributes(item, spec.get("attributes")),
            )
        )
    return out


def map_transaction(
    rec: Any,
    fields: Mapping[str, Any],
    options: Mapping[str, Any] | None = None,
    report: ImportReport | None = None,
    content_seen: dict[str, int] | None = None,
) -> RawTransaction | None:
    """Map one JSON object to a ``RawTransaction``. Returns None (and counts why) when it
    can't be used: unreadable date or amount, or a cancelled bill."""
    opt = _Options(options or {})
    report = report or ImportReport()
    ref_id = clean_text(dig(rec, fields.get("external_id")))
    raw_amount = dig(rec, fields.get("amount"))
    amount = None
    if fields.get("amount"):
        amount = opt.money(raw_amount)
        if amount is None:
            report.skip("bad_amount", 0, f"{ref_id}: {raw_amount}")
            return None
    status = dig(rec, fields.get("status"))
    if is_cancelled_status(status) and (amount is None or amount >= 0):
        report.skip("cancelled", 0, f"{ref_id}: {status}")
        return None
    if amount is not None and amount > 0 and is_return_document(dig(rec, fields.get("doc_type"))):
        amount = -amount
    raw_when = dig(rec, fields.get("occurred_at"))
    when = opt.time(raw_when)
    if when is None:
        report.skip("bad_date", 0, f"{ref_id}: {raw_when}")
        return None
    ref = dig(rec, fields.get("customer_ref"))
    if is_masked_phone(ref):
        report.warn("hidden_phone", 0, ref)
    cust = identity_key(ref, opt.country)
    seen = content_seen if content_seen is not None else {}
    ext = ref_id or content_id(seen, cust, raw_when, amount if amount is not None else "")
    currency = (
        clean_text(dig(rec, fields.get("currency"))).upper() or detect_currency(raw_amount) or opt.currency
    )
    return RawTransaction(
        external_id=ext,
        customer_external_id=cust,
        occurred_at=when,
        amount=amount,
        kind=REFUND if amount is not None and amount < 0 else SALE,
        currency=currency,
        time_known=has_time_of_day(raw_when),
        channel=clean_text(dig(rec, fields.get("channel"))) or None,
        store_id=clean_text(dig(rec, fields.get("store_id"))) or None,
        store_name=clean_text(dig(rec, fields.get("store_name"))) or None,
        updated_at=opt.time(dig(rec, fields.get("updated_at"))),
        line_items=_line_items(rec, fields.get("line_items"), opt),
        source=opt.source,
        attributes=_attributes(rec, fields.get("attributes")),
    )


def _secret(spec: Mapping[str, Any], key: str) -> str:
    env = spec.get(f"{key}_env")
    if env:
        value = os.environ.get(str(env))
        if value is None:
            raise ConnectorError(f"environment variable {env} is not set")
        return value
    return str(spec.get(key, ""))


@register("http")
class HttpJsonConnector(BaseConnector):
    """Customers and orders from a JSON API, driven by a config dict (see the module docs).

    Requests go through an SSRF-guarded client; set ``"allow_private": true`` in the config (or
    pass ``allow_private=True``) to reach a server on localhost or your LAN. Pass ``client`` to
    use your own ``httpx.Client`` (for example with ``httpx.MockTransport`` in tests).
    """

    name = "http"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        client: httpx.Client | None = None,
        allow_private: bool | None = None,
    ) -> None:
        super().__init__()
        self.config = dict(config)
        if not self.config.get("customers") and not self.config.get("transactions"):
            raise ValueError("the config needs a 'customers' and/or 'transactions' section")
        self.options = dict(self.config)
        self.options.setdefault("source", "http")
        headers = {"Accept": "application/json", **dict(self.config.get("headers") or {})}
        auth = self.config.get("auth") or {}
        self._query_auth: dict[str, str] = {}
        basic: httpx.BasicAuth | None = None
        kind = auth.get("type", "none")
        if kind == "bearer":
            headers["Authorization"] = f"Bearer {_secret(auth, 'token')}"
        elif kind == "basic":
            basic = httpx.BasicAuth(_secret(auth, "username"), _secret(auth, "password"))
        elif kind == "header":
            headers[str(auth.get("name", "X-API-Key"))] = _secret(auth, "value")
        elif kind == "query":
            self._query_auth[str(auth.get("name", "api_key"))] = _secret(auth, "value")
        elif kind != "none":
            raise ValueError(f"unknown auth type {kind!r}")
        if client is None:
            private = (
                bool(self.config.get("allow_private", False)) if allow_private is None else allow_private
            )
            client = guarded_client(
                allow_private=private,
                base_url=self.config.get("base_url", ""),
                headers=headers,
                auth=basic,
                timeout=float(self.config.get("timeout", 30)),
                follow_redirects=True,
            )
        else:
            client.headers.update(headers)
            if basic is not None:
                client.auth = basic
        self.client = client
        self.max_pages = int(self.config.get("max_pages", 10_000))

    # -- paging --------------------------------------------------------------
    def iter_records(self, spec: Mapping[str, Any], since: datetime | None = None) -> Iterator[Any]:
        """Yield raw JSON records of one endpoint, page by page."""
        pg = dict(spec.get("pagination") or {"type": "none"})
        kind = pg.get("type", "none")
        method = str(spec.get("method", "GET")).upper()
        params: dict[str, Any] = {**self._query_auth, **dict(spec.get("params") or {})}
        if since is not None and spec.get("since_param"):
            params[str(spec["since_param"])] = format_since(since, str(spec.get("since_format", "iso")))
        url: str = str(spec.get("path", "/"))
        page = int(pg.get("start", 1 if kind == "page" else 0))
        size = int(pg["size"]) if pg.get("size") else None
        offset = 0
        cursor: Any = None
        seen_tokens: set[str] = set()
        for _ in range(self.max_pages):
            p = dict(params)
            if kind == "page":
                p[pg.get("param", "page")] = page
            elif kind == "offset":
                p[pg.get("param", "offset")] = offset
            elif kind == "cursor" and cursor:
                p[pg.get("param", "cursor")] = cursor
            if size and pg.get("size_param"):
                p[str(pg["size_param"])] = size
            if kind == "next_url" and cursor:
                p, url = {}, str(cursor)
            if method == "GET":
                resp = self.client.request(method, url, params=p)
            else:
                body = {**dict(spec.get("body") or {}), **p}
                resp = self.client.request(method, url, params=self._query_auth or None, json=body)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError as exc:
                raise ConnectorError(f"{url} did not return JSON") from exc
            records = dig(data, spec.get("records")) if spec.get("records") else data
            if records is None:
                records = []
            if not isinstance(records, list):
                raise ConnectorError(f"{url}: 'records' path {spec.get('records')!r} is not a list")
            yield from records
            if kind == "page":
                total = dig(data, pg.get("total_path"))
                pages = dig(data, pg.get("total_pages_path"))
                start = int(pg.get("start", 1))
                if not records or (size and len(records) < size and pg.get("size_param")):
                    return
                if pages is not None and page - start + 1 >= int(pages):
                    return
                if total is not None and size and (page - start + 1) * size >= int(total):
                    return
                page += 1
            elif kind == "offset":
                if not records or (size and len(records) < size):
                    return
                offset += len(records)
            elif kind in ("cursor", "next_url"):
                cursor = dig(data, pg.get("next_path")) if pg.get("next_path") else None
                if kind == "next_url" and not cursor:
                    m = _LINK_NEXT.search(resp.headers.get("link", ""))
                    cursor = m.group(1) if m else None
                if not cursor:
                    return
                token = str(cursor)
                if token in seen_tokens:
                    raise ConnectorError(f"{url}: the API returned the same page token twice ({token})")
                seen_tokens.add(token)
            else:
                return
        raise ConnectorError(f"{url}: stopped after {self.max_pages} pages (raise max_pages if expected)")

    # -- protocol ------------------------------------------------------------
    def test_connection(self) -> bool:
        spec = self.config.get("customers") or self.config.get("transactions") or {}
        try:
            resp = self.client.request(str(spec.get("method", "GET")), str(spec.get("path", "/")),
                                       params=self._query_auth or None)  # fmt: skip
        except (httpx.HTTPError, BlockedURLError):
            return False
        return resp.status_code < 400

    def fetch_customers(self, since: datetime | None = None) -> Iterator[RawCustomer]:
        spec = self.config.get("customers")
        if not spec:
            return
        fields = spec.get("fields") or {}
        for rec in self.iter_records(spec, since):
            self.report.rows += 1
            cust = map_customer(rec, fields, self.options, self.report)
            if cust is None:
                continue
            if since is not None and cust.cursor_time is not None and cust.cursor_time < since:
                continue
            self.report.customers += 1
            yield cust

    def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
        spec = self.config.get("transactions")
        if not spec:
            return
        fields = spec.get("fields") or {}
        content_seen: dict[str, int] = {}
        for rec in self.iter_records(spec, since):
            self.report.rows += 1
            tx = map_transaction(rec, fields, self.options, self.report, content_seen)
            if tx is None:
                continue
            if since is not None and tx.cursor_time < since:
                continue
            self.report.transactions += 1
            yield tx
