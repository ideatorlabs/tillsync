from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tillsync import (
    BaseConnector,
    Batch,
    Connector,
    Cursor,
    MockConnector,
    RawCustomer,
    RawTransaction,
    attach_line_items,
    available,
    create,
    read_line_items,
    read_table,
    register,
    sync,
    to_jsonable,
)
from tillsync.base import get_connector_class
from tillsync.models import ImportReport, LineItem, StreamCursor

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def tx(i: int, minutes: int, cust: str = "c1") -> RawTransaction:
    return RawTransaction(f"t{i}", cust, T0 + timedelta(minutes=minutes), Decimal(i))


class ListConnector(BaseConnector):
    """A connector over in-memory lists that honours ``since`` with >=, like most APIs."""

    def __init__(self, txs: list[RawTransaction], custs: list[RawCustomer] | None = None) -> None:
        super().__init__()
        self.txs, self.custs = txs, custs or []

    def fetch_customers(self, since: datetime | None = None) -> Iterator[RawCustomer]:
        yield from self.custs

    def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
        for t in self.txs:
            if since is None or t.cursor_time >= since:
                yield t


def test_sync_batches_and_resumes_without_repeats() -> None:
    txs = [tx(1, 0), tx(2, 5), tx(3, 5), tx(4, 1)]
    got: list[Batch] = []
    res = sync(ListConnector(txs), got.append, batch_size=3)
    assert [len(b) for b in got] == [3, 1] and res.batches == 2 and res.transactions == 4
    assert res.cursor.transactions.since == T0 + timedelta(minutes=5)
    assert res.cursor.transactions.seen_ids == ["t2", "t3"]

    # a new bill at the same minute as the cursor, and one later
    txs += [tx(5, 5), tx(6, 9)]
    cursor = Cursor.from_json(res.cursor.to_json())
    got2: list[Batch] = []
    res2 = sync(ListConnector(txs), got2.append, cursor=cursor)
    assert [r.external_id for b in got2 for r in b.records] == ["t5", "t6"]
    assert res2.already_seen == 2 and res2.older_than_cursor == 0
    assert res2.cursor.transactions.seen_ids == ["t6"]

    res3 = sync(ListConnector(txs), lambda b: None, cursor=res2.cursor)
    assert res3.transactions == 0 and res3.already_seen == 1


def test_sync_drops_duplicates_and_old_records() -> None:
    class Sloppy(ListConnector):
        def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
            yield from self.txs  # ignores since and repeats itself

    txs = [tx(1, 0), tx(2, 10), tx(2, 10)]
    res = sync(
        Sloppy(txs), lambda b: None, cursor=Cursor(transactions=StreamCursor(since=T0 + timedelta(minutes=1)))
    )
    assert res.older_than_cursor == 1 and res.duplicates == 1 and res.transactions == 1


def test_sync_failure_keeps_old_cursor() -> None:
    start = Cursor()

    def explode(batch: Batch) -> None:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        sync(ListConnector([tx(1, 0)]), explode, cursor=start)
    assert start.transactions.since is None


def test_sync_options() -> None:
    custs = [RawCustomer("c1", "Asha", updated_at=T0), RawCustomer("c2")]
    seen: list[str] = []
    res = sync(ListConnector([tx(1, 0)], custs), lambda b: seen.append(b.stream), streams=["customers"])
    assert seen == ["customers"] and res.customers == 2 and res.transactions == 0
    assert res.cursor.customers.since == T0
    with pytest.raises(ValueError):
        sync(ListConnector([]), lambda b: None, batch_size=0)
    with pytest.raises(ValueError):
        Cursor().stream("orders")


def test_cursor_round_trip() -> None:
    c = Cursor(transactions=StreamCursor(since=T0, seen_ids=["a"]))
    again = Cursor.from_json(c.to_json())
    assert again == c
    assert Cursor.from_json(None) == Cursor()
    assert json.loads(c.to_json())["transactions"]["since"] == "2026-01-01T00:00:00+00:00"


def test_mock_is_deterministic_and_believable() -> None:
    a = MockConnector("retail", 30, seed=1)
    b = MockConnector("retail", 30, seed=1)
    assert [t.external_id for t in a.fetch_transactions()] == [t.external_id for t in b.fetch_transactions()]
    custs = list(a.fetch_customers())
    txs = list(a.fetch_transactions())
    assert len(custs) == 30 and all(c.phone and c.phone.startswith("+9199999") for c in custs)
    assert all(c.email and c.email.endswith("@example.com") for c in custs)
    assert {c.attributes["pattern"] for c in custs} >= {"regular", "one_time"}
    assert all(t.occurred_at <= a.now for t in txs if t.kind == "sale")
    assert [t.occurred_at for t in txs] == sorted(t.occurred_at for t in txs)
    sales = [t for t in txs if t.kind == "sale"]
    assert all(t.amount == sum(li.total for li in t.line_items if li.total) for t in sales)
    refunds = [
        t for t in MockConnector("cafe", 200, refund_rate=0.5).fetch_transactions() if t.kind == "refund"
    ]
    assert refunds and all(r.amount is not None and r.amount < 0 for r in refunds)
    late = a.now - timedelta(days=20)
    assert all(t.cursor_time >= late for t in a.fetch_transactions(late))
    assert all(c.cursor_time and c.cursor_time >= late for c in a.fetch_customers(late))
    assert a.test_connection()
    with pytest.raises(ValueError):
        MockConnector("spaceship")


def test_registry_and_protocol() -> None:
    assert {"csv", "gsheets", "http", "mock", "square"} <= set(available())

    @register("Tiny")
    class Tiny(BaseConnector):
        name = "tiny"

        def __init__(self, n: int = 1) -> None:
            super().__init__()
            self.n = n

        def fetch_transactions(self, since: datetime | None = None) -> list[RawTransaction]:
            return [tx(i, i) for i in range(self.n)]

    conn = create("tiny", n=3)
    assert isinstance(conn, Connector)
    assert len(list(conn.fetch_transactions())) == 3 and list(conn.fetch_customers()) == []
    assert conn.test_connection()
    assert get_connector_class("TINY") is Tiny
    register("tiny")(Tiny)  # re-registering the same class is fine
    with pytest.raises(ValueError):
        register("tiny")(MockConnector)
    with pytest.raises(KeyError, match="available"):
        create("nope")


def test_line_items(examples_dir: Path) -> None:
    lines = read_line_items(read_table(examples_dir / "sample_items.csv"))
    assert set(lines) == {"B-1001", "B-1002", "B-9999"}
    cap = lines["B-1001"][0]
    assert (cap.name, cap.quantity, cap.unit_price, cap.total, cap.sku) == (
        "Cappuccino",
        Decimal(2),
        Decimal(180),
        Decimal(360),
        "ITM-01",
    )
    bills = [tx(1, 0), tx(2, 0)]
    bills[0].external_id, bills[1].external_id = "B-1001", "B-1002"
    assert attach_line_items(bills, lines) == ["B-9999"]
    assert len(bills[0].line_items) == 2
    attach_line_items(bills, lines)  # attaching twice never doubles
    assert len(bills[0].line_items) == 2
    with pytest.raises(ValueError):
        read_line_items(read_table(examples_dir / "sample_items.csv"), {"bill": "Bill No"})


def test_report_and_jsonable() -> None:
    rep = ImportReport()
    for i in range(8):
        rep.skip("bad_date", i, "x" * 100)
    rep.warn("hidden_phone", 1, "99**")
    assert rep.skipped == {"bad_date": 8} and len(rep.examples["bad_date"]) == 5
    assert rep.examples["bad_date"][0][1] == "x" * 60 and rep.total_skipped == 8
    t = RawTransaction("b1", "", T0, Decimal("1.50"), line_items=[LineItem("Tea")])
    data = to_jsonable(t)
    assert data["amount"] == "1.50" and data["occurred_at"] == "2026-01-01T00:00:00+00:00"
    assert data["line_items"][0]["quantity"] == "1"
    assert to_jsonable((1, {2: T0.date()})) == [1, {"2": "2026-01-01"}]
