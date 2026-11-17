from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tillsync import CsvConnector, MappingError, automap, create, parse_csv_text, read_table
from tillsync.tables import Table, parse_rows


def sample(examples_dir: Path) -> CsvConnector:
    return CsvConnector(examples_dir / "sample_sales.csv", timezone="Asia/Kolkata", default_country="91")


def test_sample_export_end_to_end(examples_dir: Path) -> None:
    conn = sample(examples_dir)
    txs = {t.external_id: t for t in conn.fetch_transactions()}
    assert list(txs) == ["B-1001", "B-1002", "B-1003", "B-1004", "B-1005", "B-1008", "B-1010"]

    b1 = txs["B-1001"]
    assert b1.occurred_at == datetime(
        2026, 1, 3, 3, 45, tzinfo=timezone.utc
    )  # 09:15 IST, from the Time column
    assert b1.time_known and b1.amount == Decimal("1250.00") and b1.currency == "INR"
    assert b1.customer_external_id == "+919999900001" and b1.store_name == "Main Street"
    assert b1.attributes == {"Payment Mode": "UPI"}
    assert txs["B-1003"].amount == Decimal("120000")  # lakh grouping
    # refunds: a negative amount and an accounting "(120.00)" on a refunded bill are kept, as refunds
    assert (txs["B-1004"].kind, txs["B-1004"].amount) == ("refund", Decimal("-250"))
    assert (txs["B-1005"].kind, txs["B-1005"].amount) == ("refund", Decimal("-120.00"))
    # a hidden marketplace phone: the sale is kept, as a walk-in
    assert txs["B-1008"].customer_external_id == ""
    assert txs["B-1010"].currency == "USD"

    rep = conn.report
    assert rep.skipped == {"cancelled": 1, "duplicate": 1, "bad_date": 1, "bad_amount": 1, "total_row": 1}
    assert rep.warnings == {"hidden_phone": 1}
    assert rep.examples["bad_date"] == [(10, "not a date")]
    assert rep.examples["duplicate"] == [(9, "B-1002")]
    assert rep.total_skipped == 5

    custs = {c.external_id: c for c in conn.fetch_customers()}
    assert set(custs) == {f"+91999990000{i}" for i in range(1, 7)}
    asha = custs["+919999900001"]
    assert asha.name == "Asha Rao" and asha.email == "asha@example.com"
    assert asha.first_seen_at == datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)
    assert asha.updated_at == datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc)
    assert custs["+919999900004"].name == ""  # "Guest" is not a name
    assert custs["+919999900006"].first_seen_at is None  # bill date was unreadable


def test_since_filters_both_streams(examples_dir: Path) -> None:
    conn = sample(examples_dir)
    since = datetime(2026, 1, 6, tzinfo=timezone.utc)
    assert [t.external_id for t in conn.fetch_transactions(since)] == ["B-1005", "B-1008", "B-1010"]
    ids = {c.external_id for c in conn.fetch_customers(since)}
    assert "+919999900003" not in ids and "+919999900002" in ids and "+919999900006" in ids


def test_explicit_mapping_and_tsv(tmp_path: Path) -> None:
    f = tmp_path / "visits.tsv"
    f.write_text("ref\twhen\twho\n1\t2026-02-01 10:00\tc-1\n2\t2026-02-02\t\n")
    conn = CsvConnector(
        f,
        mapping={
            "customer": {"external_id": "who"},
            "transaction": {"external_id": "ref", "occurred_at": "when"},
        },
    )
    txs = list(conn.fetch_transactions())
    assert [t.amount for t in txs] == [None, None]  # a visit list: no amount column
    assert txs[1].customer_external_id == "" and not txs[1].time_known
    assert [c.external_id for c in conn.fetch_customers()] == ["c-1"]
    assert conn.automapping is None and conn.test_connection()


def test_bills_without_numbers_get_stable_content_ids() -> None:
    text = "Date,Phone,Amount\n2026-01-01 10:00,9999900001,100\n2026-01-01 10:00,9999900001,100\n"
    a = [t.external_id for t in CsvConnector(text=text, default_country="91").fetch_transactions()]
    b = [t.external_id for t in CsvConnector(text=text, default_country="91").fetch_transactions()]
    assert a == b and len(set(a)) == 2 and all(x.startswith("auto-") for x in a)


def test_return_documents_become_refunds() -> None:
    text = "Bill No,Date,Doc Type,Amount\nR1,2026-01-01,Sales Return,300\nS1,2026-01-01,Sale,300\n"
    txs = list(CsvConnector(text=text).fetch_transactions())
    assert [(t.external_id, t.kind, t.amount) for t in txs] == [
        ("R1", "refund", Decimal("-300")),
        ("S1", "sale", Decimal("300")),
    ]


def test_separate_customers_file(tmp_path: Path) -> None:
    custs = tmp_path / "customers.csv"
    custs.write_text(
        "Customer ID,Full Name,Phone Number,Email,Customer Since,Loyalty Tier\n"
        "C-1,Asha Rao,+1 202 555 0101,ASHA@example.com,2025-06-01,Gold\n"
        "C-2,Ravi Shah,,ravi@example.com,2025-07-01,\n"
        "C-1,,,,,Silver\n"
        ",Walk In,,,,\n"
        ",Hidden,XXXXXX0199,,,\n"
    )
    sales = tmp_path / "sales.csv"
    sales.write_text("Order ID,Order Date,Customer ID,Total\nO1,2026-01-01,C-1,10\nO2,2026-01-02,C-9,5\n")
    conn = CsvConnector(sales, customers_path=custs, default_country="1")
    cs = list(conn.fetch_customers())
    assert [c.external_id for c in cs] == ["C-1", "C-2"]
    asha = cs[0]
    assert asha.phone == "+12025550101" and asha.email == "asha@example.com"
    assert asha.attributes == {"Loyalty Tier": "Gold"}
    assert asha.first_seen_at == datetime(2025, 6, 1, tzinfo=timezone.utc)
    assert conn.report.skipped == {"duplicate": 1, "no_identity": 1, "hidden_phone": 1}
    txs = list(conn.fetch_transactions())
    assert [t.customer_external_id for t in txs] == ["C-1", "C-9"]
    assert list(conn.fetch_customers(datetime(2025, 6, 15, tzinfo=timezone.utc)))[0].external_id == "C-2"


def test_customers_only_file() -> None:
    conn = CsvConnector(customers_text="Name,Mobile\nAsha,9999900001\n", default_country="91")
    assert [c.phone for c in conn.fetch_customers()] == ["+919999900001"]
    assert list(conn.fetch_transactions()) == []


def test_mapping_errors() -> None:
    with pytest.raises(MappingError, match="date column"):
        CsvConnector(text="Name,Amount\nAsha,10\n")
    with pytest.raises(MappingError, match="not in the file"):
        CsvConnector(text="Date,Amount\n2026-01-01,1\n", mapping={"transaction": {"occurred_at": "Day"}})
    with pytest.raises(MappingError, match="customer mapping"):
        CsvConnector(
            text="Date\n2026-01-01\n",
            mapping={"customer": {"name": "Who"}, "transaction": {"occurred_at": "Date"}},
        )
    with pytest.raises(ValueError):
        CsvConnector()


def test_automapping_override_feeds_connector() -> None:
    text = "Date,Total,Net\n2026-01-01,110,100\n"
    t = parse_csv_text(text)
    guess = automap(t.headers, t.rows).override("transaction", amount="Net")
    conn = CsvConnector(table=t, mapping=guess)
    assert [x.amount for x in conn.fetch_transactions()] == [Decimal("100")]
    assert conn.automapping is guess


def test_registry_creates_csv() -> None:
    conn = create("csv", text="Date,Amount\n2026-01-01,5\n")
    assert isinstance(conn, CsvConnector)


def test_excel_file(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Daily sales"])
    ws.append([])
    ws.append(["Invoice No", "Invoice Date", "Mobile", "Amount"])
    ws.append(["X1", datetime(2026, 1, 3, 9, 15), "9999900001", 250])
    ws.append(["X2", datetime(2026, 1, 4), 9999900002, "1,000"])
    path = tmp_path / "sales.xlsx"
    wb.save(path)
    table = read_table(path)
    assert table.headers == ["Invoice No", "Invoice Date", "Mobile", "Amount"] and table.header_row == 3
    conn = CsvConnector(path, timezone="Asia/Kolkata", default_country="91")
    txs = list(conn.fetch_transactions())
    assert txs[0].occurred_at == datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)
    assert [t.amount for t in txs] == [Decimal(250), Decimal(1000)]
    assert txs[1].customer_external_id == "+919999900002"
    assert read_table(path, sheet=0).headers == table.headers
    assert read_table(path, sheet=wb.active.title).headers == table.headers


def test_table_helpers(tmp_path: Path) -> None:
    t = parse_csv_text("﻿a;b;b;\n1;2;3;4\n\n")
    assert t.headers == ["a", "b", "b (2)", "column_4"]
    assert t.rows == [{"a": "1", "b": "2", "b (2)": "3", "column_4": "4"}]
    assert parse_csv_text("").headers == []
    assert parse_rows([]).headers == []
    short = parse_rows([["x", "y"], ["1"]])
    assert short.rows == [{"x": "1", "y": None}]
    latin = tmp_path / "latin.csv"
    latin.write_bytes("Name,City\nJos\xe9,Sa\xf5\n".encode("cp1252"))
    assert read_table(latin).rows[0]["Name"] == "José"
    assert isinstance(Table(headers=["a"]).row_number(0), int)
