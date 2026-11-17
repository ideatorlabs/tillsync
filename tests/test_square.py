from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from tillsync import SquareConnector
from tillsync.connectors.square import SANDBOX_URL, money_amount

CUSTOMERS = [
    {
        "id": "SQ-C1",
        "given_name": "Asha",
        "family_name": "Rao",
        "phone_number": "+1 202 555 0101",
        "email_address": "Asha@Example.com",
        "created_at": "2025-12-01T10:00:00Z",
        "updated_at": "2026-01-02T10:00:00Z",
        "reference_id": "R1",
    },
    {"id": "SQ-C2", "company_name": "Sample Store", "created_at": "2025-12-02T10:00:00.123Z"},
]
ORDERS = [
    {
        "id": "SQ-O1",
        "customer_id": "SQ-C1",
        "location_id": "L1",
        "state": "COMPLETED",
        "created_at": "2026-01-03T09:00:00Z",
        "closed_at": "2026-01-03T09:05:00Z",
        "updated_at": "2026-01-03T09:05:00Z",
        "total_money": {"amount": 1999, "currency": "USD"},
        "source": {"name": "Online Store"},
        "line_items": [
            {
                "name": "Latte",
                "quantity": "2",
                "base_price_money": {"amount": 450, "currency": "USD"},
                "total_money": {"amount": 900, "currency": "USD"},
                "catalog_object_id": "ITEM1",
            }
        ],
        "refunds": [
            {
                "id": "RF1",
                "status": "COMPLETED",
                "amount_money": {"amount": 450, "currency": "USD"},
                "created_at": "2026-01-04T10:00:00Z",
                "reason": "spilled",
            },
            {"id": "RF2", "status": "PENDING", "amount_money": {"amount": 100, "currency": "USD"}},
        ],
    },
    {
        "id": "SQ-O2",
        "location_id": "L1",
        "state": "CANCELED",
        "created_at": "2026-01-05T09:00:00Z",
        "total_money": {"amount": 500, "currency": "USD"},
    },
    {"id": "SQ-O3", "location_id": "L1", "state": "COMPLETED", "created_at": "garbage"},
    {
        "id": "SQ-O4",
        "location_id": "L1",
        "state": "OPEN",
        "created_at": "2026-01-06T09:00:00Z",
        "total_money": {"amount": 1200, "currency": "JPY"},
    },
]


def fake_square(log: list[tuple[str, str, Any]]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        log.append((req.method, req.url.path, body or dict(req.url.params)))
        assert req.headers["authorization"] == "Bearer sandbox-token"
        assert req.headers["square-version"]
        if req.url.path == "/v2/locations":
            return httpx.Response(200, json={"locations": [{"id": "L1"}]})
        if req.url.path in ("/v2/customers", "/v2/customers/search"):
            cursor = (body or {}).get("cursor") or req.url.params.get("cursor")
            if not cursor:
                return httpx.Response(200, json={"customers": CUSTOMERS[:1], "cursor": "c2"})
            return httpx.Response(200, json={"customers": CUSTOMERS[1:]})
        if req.url.path == "/v2/orders/search":
            if not body.get("cursor"):
                return httpx.Response(200, json={"orders": ORDERS[:2], "cursor": "o2"})
            return httpx.Response(200, json={"orders": ORDERS[2:]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def connector(log: list[tuple[str, str, Any]], **kw: Any) -> SquareConnector:
    client = httpx.Client(base_url=SANDBOX_URL, transport=fake_square(log))
    return SquareConnector("sandbox-token", client=client, **kw)


def test_customers_paginate_and_normalize() -> None:
    log: list[tuple[str, str, Any]] = []
    conn = connector(log)
    assert conn.test_connection()
    cs = list(conn.fetch_customers())
    assert [c.external_id for c in cs] == ["SQ-C1", "SQ-C2"]
    assert cs[0].name == "Asha Rao" and cs[0].phone == "+12025550101" and cs[0].email == "asha@example.com"
    assert cs[0].attributes == {"reference_id": "R1"}
    assert cs[1].name == "Sample Store"
    assert cs[1].first_seen_at == datetime(2025, 12, 2, 10, 0, 0, 123000, tzinfo=timezone.utc)
    assert [p for _, p, _ in log if "customers" in p] == ["/v2/customers", "/v2/customers"]


def test_incremental_customers_use_search() -> None:
    log: list[tuple[str, str, Any]] = []
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert len(list(connector(log).fetch_customers(since))) == 2
    method, path, body = log[0]
    assert (method, path) == ("POST", "/v2/customers/search")
    assert body["query"]["filter"]["updated_at"]["start_at"] == "2026-01-01T00:00:00Z"
    assert log[1][2]["cursor"] == "c2"


def test_orders_refunds_and_skips() -> None:
    log: list[tuple[str, str, Any]] = []
    conn = connector(log)
    txs = list(conn.fetch_transactions())
    by_id = {t.external_id: t for t in txs}
    assert list(by_id) == ["SQ-O1", "SQ-O1:refund:RF1", "SQ-O4"]
    o1 = by_id["SQ-O1"]
    assert o1.amount == Decimal("19.99") and o1.currency == "USD" and o1.channel == "Online Store"
    assert o1.occurred_at == datetime(2026, 1, 3, 9, 5, tzinfo=timezone.utc)
    assert o1.line_items[0].quantity == Decimal(2) and o1.line_items[0].unit_price == Decimal("4.50")
    refund = by_id["SQ-O1:refund:RF1"]
    assert (
        refund.kind == "refund"
        and refund.amount == Decimal("-4.50")
        and refund.customer_external_id == "SQ-C1"
    )
    assert by_id["SQ-O4"].amount == Decimal(1200) and by_id["SQ-O4"].customer_external_id == ""
    assert conn.report.skipped == {"cancelled": 1, "bad_date": 1}
    search = [b for _, p, b in log if p == "/v2/orders/search"]
    assert search[0]["location_ids"] == ["L1"] and search[1]["cursor"] == "o2"


def test_incremental_orders_filter_on_updated_at() -> None:
    log: list[tuple[str, str, Any]] = []
    conn = connector(log, location_ids=["L1"])
    list(conn.fetch_transactions(datetime(2026, 1, 2, tzinfo=timezone.utc)))
    body = next(b for _, p, b in log if p == "/v2/orders/search")
    assert body["query"]["filter"]["date_time_filter"]["updated_at"]["start_at"] == "2026-01-02T00:00:00Z"
    assert body["query"]["sort"]["sort_field"] == "UPDATED_AT"
    assert not any(p == "/v2/locations" for _, p, _ in log)


def test_no_locations_and_errors() -> None:
    client = httpx.Client(
        base_url=SANDBOX_URL,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"locations": []})),
    )
    assert list(SquareConnector("t", client=client).fetch_transactions()) == []

    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.Client(base_url=SANDBOX_URL, transport=httpx.MockTransport(boom))
    assert SquareConnector("t", client=client).test_connection() is False


def test_default_client_uses_configurable_base_url() -> None:
    conn = SquareConnector("t", base_url="https://square-proxy.example.test")
    assert str(conn.client.base_url) == "https://square-proxy.example.test"


def test_money_amount() -> None:
    assert money_amount(None) == (None, None)
    assert money_amount({"currency": "USD"}) == (None, "USD")
    assert money_amount({"amount": 5, "currency": "JPY"}) == (Decimal(5), "JPY")
