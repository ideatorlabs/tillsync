"""Square: customers and orders through the Square REST API (v2).

Endpoints used: ``GET /v2/locations``, ``GET /v2/customers`` and ``POST /v2/customers/search``
(incremental), ``POST /v2/orders/search``. Money arrives in the currency's smallest unit and is
converted (``1999`` USD cents -> ``19.99``). Completed refunds on an order become separate
refund transactions with negative amounts; cancelled orders are skipped.

``base_url`` defaults to production; use ``SANDBOX_URL`` for the sandbox, or any URL (a
proxy, a test server). Create an access token in the Square developer dashboard.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from ..base import BaseConnector, register
from ..models import REFUND, SALE, LineItem, RawCustomer, RawTransaction
from ..names import clean_person_name, clean_text
from ..phone import normalize_phone
from ..times import format_since, parse_datetime

__all__ = ["SquareConnector", "PRODUCTION_URL", "SANDBOX_URL", "API_VERSION"]

PRODUCTION_URL = "https://connect.squareup.com"
SANDBOX_URL = "https://connect.squareupsandbox.com"
API_VERSION = "2025-01-23"
_ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF"}


def money_amount(money: dict[str, Any] | None) -> tuple[Decimal | None, str | None]:
    """A Square Money object as (amount in major units, currency)."""
    if not money or money.get("amount") is None:
        return None, (money or {}).get("currency")
    currency = money.get("currency")
    places = 0 if currency in _ZERO_DECIMAL else 2
    return Decimal(int(money["amount"])).scaleb(-places), currency


@register("square")
class SquareConnector(BaseConnector):
    """Customers and orders from one Square seller account."""

    name = "square"

    def __init__(
        self,
        access_token: str = "",
        *,
        base_url: str = PRODUCTION_URL,
        location_ids: list[str] | None = None,
        default_country: str | None = None,
        page_size: int = 100,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Square-Version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client is None:
            client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        else:
            client.headers.update(headers)
        self.client = client
        self.location_ids = location_ids
        self.default_country = default_country
        self.page_size = page_size

    def test_connection(self) -> bool:
        try:
            return self.client.get("/v2/locations").status_code == 200
        except httpx.HTTPError:
            return False

    # -- customers -----------------------------------------------------------
    def _customer(self, c: dict[str, Any]) -> RawCustomer:
        name = " ".join(p for p in (c.get("given_name"), c.get("family_name")) if p) or c.get("company_name")
        return RawCustomer(
            external_id=str(c["id"]),
            name=clean_person_name(name),
            phone=normalize_phone(c.get("phone_number"), self.default_country),
            email=(clean_text(c.get("email_address")).lower() or None),
            first_seen_at=parse_datetime(c.get("created_at")),
            updated_at=parse_datetime(c.get("updated_at")),
            source="square",
            attributes={k: c[k] for k in ("reference_id", "note", "birthday") if c.get(k)},
        )

    def fetch_customers(self, since: datetime | None = None) -> Iterator[RawCustomer]:
        cursor: str | None = None
        while True:
            if since is None:
                params: dict[str, Any] = {
                    "limit": self.page_size,
                    "sort_field": "CREATED_AT",
                    "sort_order": "ASC",
                }
                if cursor:
                    params["cursor"] = cursor
                resp = self.client.get("/v2/customers", params=params)
            else:
                body: dict[str, Any] = {
                    "limit": self.page_size,
                    "query": {
                        "filter": {"updated_at": {"start_at": format_since(since, "iso_z")}},
                        "sort": {"field": "CREATED_AT", "order": "ASC"},
                    },
                }
                if cursor:
                    body["cursor"] = cursor
                resp = self.client.post("/v2/customers/search", json=body)
            resp.raise_for_status()
            data = resp.json()
            for c in data.get("customers", []):
                self.report.rows += 1
                self.report.customers += 1
                yield self._customer(c)
            cursor = data.get("cursor")
            if not cursor:
                return

    # -- orders --------------------------------------------------------------
    def _locations(self) -> list[str]:
        if self.location_ids:
            return list(self.location_ids)
        resp = self.client.get("/v2/locations")
        resp.raise_for_status()
        return [str(loc["id"]) for loc in resp.json().get("locations", [])]

    def _order(self, o: dict[str, Any]) -> list[RawTransaction]:
        when = parse_datetime(o.get("closed_at") or o.get("created_at"))
        if when is None:
            self.report.skip("bad_date", 0, o.get("id"))
            return []
        if o.get("state") == "CANCELED":
            self.report.skip("cancelled", 0, o.get("id"))
            return []
        amount, currency = money_amount(o.get("total_money"))
        items = []
        for li in o.get("line_items") or []:
            qty = Decimal(str(li.get("quantity") or "1"))
            items.append(
                LineItem(
                    name=clean_text(li.get("name")) or "item",
                    quantity=qty,
                    unit_price=money_amount(li.get("base_price_money"))[0],
                    total=money_amount(li.get("total_money"))[0],
                    sku=li.get("catalog_object_id"),
                    category=li.get("variation_name"),
                )
            )
        common: dict[str, Any] = {
            "customer_external_id": str(o.get("customer_id") or ""),
            "currency": currency,
            "channel": ((o.get("source") or {}).get("name")) or "square",
            "store_id": o.get("location_id"),
            "source": "square",
        }
        out = [
            RawTransaction(
                external_id=str(o["id"]),
                occurred_at=when,
                amount=amount,
                kind=SALE,
                updated_at=parse_datetime(o.get("updated_at")),
                line_items=items,
                attributes={"state": o.get("state")},
                **common,
            )
        ]
        for r in o.get("refunds") or []:
            if r.get("status") not in (None, "COMPLETED", "APPROVED"):
                continue
            ramount, _ = money_amount(r.get("amount_money"))
            rwhen = parse_datetime(r.get("created_at")) or when
            out.append(
                RawTransaction(
                    external_id=f"{o['id']}:refund:{r.get('id')}",
                    occurred_at=rwhen,
                    amount=-abs(ramount) if ramount is not None else None,
                    kind=REFUND,
                    updated_at=parse_datetime(o.get("updated_at")),
                    attributes={"order_id": o["id"], "reason": r.get("reason")},
                    **common,
                )
            )
        return out

    def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
        locations = self._locations()
        if not locations:
            return
        cursor: str | None = None
        while True:
            query: dict[str, Any] = {"sort": {"sort_field": "CREATED_AT", "sort_order": "ASC"}}
            if since is not None:
                query = {
                    "filter": {
                        "date_time_filter": {"updated_at": {"start_at": format_since(since, "iso_z")}}
                    },
                    "sort": {"sort_field": "UPDATED_AT", "sort_order": "ASC"},
                }
            body: dict[str, Any] = {"location_ids": locations, "limit": self.page_size, "query": query}
            if cursor:
                body["cursor"] = cursor
            resp = self.client.post("/v2/orders/search", json=body)
            resp.raise_for_status()
            data = resp.json()
            for o in data.get("orders", []):
                self.report.rows += 1
                for tx in self._order(o):
                    self.report.transactions += 1
                    yield tx
            cursor = data.get("cursor")
            if not cursor:
                return
