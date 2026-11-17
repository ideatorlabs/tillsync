from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest

from tillsync import BlockedURLError, ConnectorError, HttpJsonConnector, map_customer, map_transaction
from tillsync.connectors.http_json import dig

ITEMS = [
    {
        "id": str(i),
        "name": f"Person {chr(65 + i)}{chr(97 + i)}",
        "when": f"2026-01-0{i + 1}T10:00:00Z",
        "total": str(10 * (i + 1)),
    }
    for i in range(5)
]
FIELDS_C = {"external_id": "id", "name": "name", "updated_at": "when"}
FIELDS_T = {"external_id": "id", "customer_ref": "id", "occurred_at": "when", "amount": "total"}


def make(pagination: dict[str, Any], handler: Any, **extra: Any) -> HttpJsonConnector:
    cfg = {
        "base_url": "https://pos.example.test",
        "customers": {"path": "/c", "records": "data", "pagination": pagination, "fields": FIELDS_C},
        "transactions": {"path": "/t", "records": "data", "pagination": pagination, "fields": FIELDS_T},
        **extra,
    }
    client = httpx.Client(base_url="https://pos.example.test", transport=httpx.MockTransport(handler))
    return HttpJsonConnector(cfg, client=client)


def test_page_pagination_with_total() -> None:
    calls: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        calls.append(q)
        page, size = int(q["page"]), int(q["size"])
        return httpx.Response(200, json={"data": ITEMS[(page - 1) * size : page * size], "total": len(ITEMS)})

    conn = make({"type": "page", "size_param": "size", "size": 2, "total_path": "total"}, handler)
    assert [c.external_id for c in conn.fetch_customers()] == ["0", "1", "2", "3", "4"]
    assert [c["page"] for c in calls] == ["1", "2", "3"]


def test_page_pagination_until_empty_and_total_pages() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        page = int(req.url.params["p"])
        return httpx.Response(200, json={"data": ITEMS[page * 2 : page * 2 + 2], "pages": 3})

    conn = make({"type": "page", "param": "p", "start": 0}, handler)
    assert len(list(conn.fetch_transactions())) == 5
    conn = make({"type": "page", "param": "p", "start": 0, "total_pages_path": "pages"}, handler)
    assert len(list(conn.fetch_transactions())) == 5


def test_offset_pagination() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        off, lim = int(req.url.params["offset"]), int(req.url.params["limit"])
        return httpx.Response(200, json={"data": ITEMS[off : off + lim]})

    conn = make({"type": "offset", "size_param": "limit", "size": 2}, handler)
    assert [t.amount for t in conn.fetch_transactions()] == [Decimal(x) for x in (10, 20, 30, 40, 50)]


def test_cursor_pagination_and_loop_detection() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        start = int(req.url.params.get("cursor", 0))
        nxt = str(start + 2) if start + 2 < len(ITEMS) else None
        return httpx.Response(200, json={"data": ITEMS[start : start + 2], "meta": {"next": nxt}})

    conn = make({"type": "cursor", "next_path": "meta.next"}, handler)
    assert len(list(conn.fetch_customers())) == 5

    stuck = make(
        {"type": "cursor", "next_path": "next"}, lambda r: httpx.Response(200, json={"data": [], "next": "A"})
    )
    with pytest.raises(ConnectorError, match="same page token"):
        list(stuck.fetch_customers())


def test_next_url_from_body_and_link_header() -> None:
    def body_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/c":
            return httpx.Response(
                200, json={"data": ITEMS[:3], "links": {"next": "https://pos.example.test/c2"}}
            )
        return httpx.Response(200, json={"data": ITEMS[3:], "links": {"next": None}})

    conn = make({"type": "next_url", "next_path": "links.next"}, body_handler)
    assert len(list(conn.fetch_customers())) == 5

    def link_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/c":
            return httpx.Response(200, json={"data": ITEMS[:1]}, headers={"Link": '</c2?x=1>; rel="next"'})
        return httpx.Response(200, json={"data": ITEMS[1:2]})

    conn = make({"type": "next_url"}, link_handler)
    assert len(list(conn.fetch_customers())) == 2


def test_max_pages_guard() -> None:
    conn = make({"type": "page"}, lambda r: httpx.Response(200, json={"data": ITEMS[:1]}), max_pages=3)
    with pytest.raises(ConnectorError, match="stopped after 3 pages"):
        list(conn.fetch_customers())


def test_bad_responses() -> None:
    conn = make({"type": "none"}, lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(ConnectorError, match="did not return JSON"):
        list(conn.fetch_customers())
    conn = make({"type": "none"}, lambda r: httpx.Response(200, json={"data": {"x": 1}}))
    with pytest.raises(ConnectorError, match="is not a list"):
        list(conn.fetch_customers())
    conn = make({"type": "none"}, lambda r: httpx.Response(500, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        list(conn.fetch_customers())
    assert conn.test_connection() is False
    conn = make({"type": "none"}, lambda r: httpx.Response(200, json={}))
    assert list(conn.fetch_customers()) == []


@pytest.mark.parametrize(
    ("auth", "check"),
    [
        ({"type": "bearer", "token": "t0k"}, lambda r: r.headers["authorization"] == "Bearer t0k"),
        (
            {"type": "basic", "username": "u", "password": "p"},
            lambda r: r.headers["authorization"] == "Basic " + base64.b64encode(b"u:p").decode(),
        ),
        ({"type": "header", "name": "X-Key", "value": "k1"}, lambda r: r.headers["x-key"] == "k1"),
        ({"type": "query", "name": "api_key", "value": "q1"}, lambda r: r.url.params["api_key"] == "q1"),
        (
            {"type": "bearer", "token_env": "TILLSYNC_TEST_TOKEN"},
            lambda r: r.headers["authorization"] == "Bearer envtok",
        ),
    ],
)
def test_auth_styles(auth: dict[str, Any], check: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TILLSYNC_TEST_TOKEN", "envtok")
    seen: list[bool] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(check(req))
        return httpx.Response(200, json={"data": ITEMS[:1]})

    conn = make({"type": "none"}, handler, auth=auth)
    assert conn.test_connection()
    assert len(list(conn.fetch_customers())) == 1
    assert seen and all(seen)


def test_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN_VAR", raising=False)
    with pytest.raises(ConnectorError, match="MISSING_TOKEN_VAR"):
        make(
            {"type": "none"},
            lambda r: httpx.Response(200),
            auth={"type": "bearer", "token_env": "MISSING_TOKEN_VAR"},
        )
    with pytest.raises(ValueError, match="unknown auth"):
        make({"type": "none"}, lambda r: httpx.Response(200), auth={"type": "oauth9"})
    with pytest.raises(ValueError, match="needs a"):
        HttpJsonConnector({"base_url": "https://x.example.test"})


def test_post_endpoints_send_paging_in_body() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        start = int(body.get("cursor") or 0)
        return httpx.Response(
            200, json={"data": ITEMS[start : start + 3], "next": "3" if start == 0 else None}
        )

    cfg = {
        "customers": {
            "path": "/search",
            "method": "POST",
            "body": {"filter": "all"},
            "records": "data",
            "pagination": {"type": "cursor", "next_path": "next"},
            "fields": FIELDS_C,
        },
    }
    client = httpx.Client(base_url="https://pos.example.test", transport=httpx.MockTransport(handler))
    conn = HttpJsonConnector(cfg, client=client)
    assert len(list(conn.fetch_customers())) == 5
    assert bodies == [{"filter": "all"}, {"filter": "all", "cursor": "3"}]
    assert list(conn.fetch_transactions()) == []


def test_since_param_and_client_side_filter() -> None:
    params: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params.append(req.url.params.get("since", ""))
        return httpx.Response(200, json={"data": ITEMS})  # this API ignores the filter

    conn = make({"type": "none"}, handler)
    conn.config["customers"]["since_param"] = "since"
    conn.config["customers"]["since_format"] = "epoch"
    since = datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc)
    assert [c.external_id for c in conn.fetch_customers(since)] == ["2", "3", "4"]
    assert [t.external_id for t in conn.fetch_transactions(since)] == ["2", "3", "4"]
    assert params[0] == str(int(since.timestamp()))


def test_basic_auth_with_own_client() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json=[])

    conn = make({"type": "none"}, handler, auth={"type": "basic", "username": "a", "password": "b"})
    conn.config["customers"]["records"] = ""
    assert list(conn.fetch_customers()) == []


def test_ssrf_guard_is_on_by_default() -> None:
    conn = HttpJsonConnector(
        {"base_url": "http://127.0.0.1:9", "customers": {"path": "/c", "fields": FIELDS_C}}
    )
    with pytest.raises(BlockedURLError):
        list(conn.fetch_customers())
    assert conn.test_connection() is False


def test_dig() -> None:
    rec = {"a": {"b": [{"c": 1}, {"c": 2}]}, "first": "Asha", "last": "Rao", "n": None}
    assert dig(rec, "a.b.1.c") == 2
    assert dig(rec, "a.b.9.c") is None
    assert dig(rec, "a.b.x") is None
    assert dig(rec, "first.x") is None
    assert dig(rec, "n.x") is None
    assert dig(rec, ["first", "last"]) == "Asha Rao"
    assert dig(rec, ["n"]) is None
    assert dig(rec, "") is None


def test_map_customer_fallbacks() -> None:
    opts = {"default_country": "91"}
    c = map_customer(
        {"phone": "9999900001", "name": "Asha"}, {"external_id": "id", "phone": "phone", "name": "name"}, opts
    )
    assert c is not None and c.external_id == "+919999900001"
    c = map_customer({"mail": "A@Example.com"}, {"email": "mail"}, opts)
    assert c is not None and c.external_id == "a@example.com"
    c = map_customer({"n": "Ravi Shah"}, {"name": "n"}, opts)
    assert c is not None and c.external_id == "Ravi Shah"
    assert map_customer({"p": "99******02"}, {"phone": "p"}, opts) is None
    assert map_customer({}, {"name": "n"}, opts) is None


def test_map_transaction_rules() -> None:
    f = {
        "external_id": "id",
        "occurred_at": "at",
        "amount": "amt",
        "status": "st",
        "doc_type": "doc",
        "customer_ref": "who",
        "currency": "cur",
        "attributes": ["note"],
        "store_id": "s",
        "channel": "ch",
        "line_items": {
            "path": "lines",
            "name": "n",
            "quantity": "q",
            "unit_price": "p",
            "total": "t",
            "sku": "sku",
            "category": "cat",
            "attributes": ["x"],
        },
    }
    opts = {"timezone": "Asia/Kolkata", "amount_divisor": 100}
    tx = map_transaction(
        {
            "id": "1",
            "at": "2026-01-01 10:00",
            "amt": 12345,
            "cur": "inr",
            "note": "hi",
            "s": "S1",
            "ch": "web",
            "who": "C1",
            "lines": [
                {"n": "Tea", "q": "2", "p": 5000, "t": 10000, "sku": "T1", "cat": "Hot", "x": 1},
                {"n": ""},
            ],
        },
        f,
        opts,
    )
    assert tx is not None
    assert tx.amount == Decimal("123.45") and tx.currency == "INR" and tx.attributes == {"note": "hi"}
    assert tx.occurred_at == datetime(2026, 1, 1, 4, 30, tzinfo=timezone.utc)
    assert len(tx.line_items) == 1 and tx.line_items[0].unit_price == Decimal(50)
    assert tx.line_items[0].quantity == Decimal(2) and tx.line_items[0].attributes == {"x": 1}
    r = map_transaction({"id": "2", "at": "2026-01-01", "amt": "500", "doc": "Credit Note"}, f)
    assert r is not None and r.kind == "refund" and r.amount == Decimal(-500) and not r.time_known
    assert map_transaction({"id": "3", "at": "2026-01-01", "amt": "x"}, f) is None
    assert map_transaction({"id": "4", "at": "2026-01-01", "amt": "5", "st": "VOID"}, f) is None
    assert map_transaction({"id": "5", "at": "??", "amt": "5"}, f) is None
    walk_in = map_transaction({"at": "2026-01-01", "amt": "5", "who": "98******10"}, f)
    assert (
        walk_in is not None and walk_in.customer_external_id == "" and walk_in.external_id.startswith("auto-")
    )
    no_lines = map_transaction({"id": "6", "at": "2026-01-01", "amt": "5", "lines": "oops"}, f)
    assert no_lines is not None and no_lines.line_items == []
