from __future__ import annotations

import pytest

from tillsync import automap
from tillsync.automap import normalize_header
from tillsync.tables import parse_csv_text


def test_normalize_header() -> None:
    assert normalize_header("Net Amount (₹)") == "netamount"
    assert normalize_header("Total (INR)") == "total"
    assert normalize_header("Mobile No.") == "mobileno"


def test_messy_headers_map_with_reasons() -> None:
    t = parse_csv_text(
        "Invoice #,Txn Date,Cust. Mobile,Party Name,Grand Total (Rs),Branch,Remarks\n"
        'INV/1,2026-01-03,9999900001,Asha Rao,"1,250",North Wing,first visit\n'
        "INV/2,2026-01-04,9999900002,Ravi Shah,480,North Wing,\n"
        "INV/3,2026-01-05,9999900003,Sam,90,South Wing,\n"
    )
    guess = automap(t.headers, t.rows)
    m = guess.mapping()
    assert m["transaction"]["external_id"] == "Invoice #"
    assert m["transaction"]["occurred_at"] == "Txn Date"
    assert m["transaction"]["amount"] == "Grand Total (Rs)"
    assert m["transaction"]["store_name"] == "Branch"  # values read like names, not codes
    assert m["customer"]["phone"] == "Cust. Mobile"
    assert m["customer"]["name"] == "Party Name"
    assert m["customer"]["external_id"] == "Cust. Mobile"
    assert guess.customer["external_id"].link_kind == "phone"
    assert guess.unmapped == ["Remarks"]
    assert guess.missing() == {}
    text = guess.explain()
    assert "Grand Total (Rs)" in text and "parse as money" in text and "kept as attributes: Remarks" in text
    for g in guess.transaction.values():
        assert 0 < g.confidence <= 1 and g.reasons


def test_values_can_reveal_an_unnamed_email_column() -> None:
    t = parse_csv_text(
        "Date,Col A,Amount\n2026-01-01,a@example.com,10\n2026-01-02,b@example.com,20\n"
        "2026-01-03,c@example.com,30\n"
    )
    guess = automap(t.headers, t.rows)
    assert guess.customer["email"].column == "Col A"
    assert guess.customer["email"].confidence < 0.6
    assert "header says nothing" in guess.customer["email"].reasons[0]


def test_contradicting_values_lower_confidence() -> None:
    t = parse_csv_text("Date,Phone,Amount\n2026-01-01,hello,10\n2026-01-02,world,20\n")
    guess = automap(t.headers, t.rows)
    assert "phone" not in guess.customer or guess.customer["phone"].confidence < 0.5


def test_override_and_missing() -> None:
    t = parse_csv_text("When,Customer Code,Value,Other Total\n2026-01-01,C1,10,11\n")
    guess = automap(t.headers, t.rows)
    assert guess.customer["external_id"].column == "Customer Code"
    fixed = guess.override("transaction", amount="Other Total", channel=None)
    assert fixed.transaction["amount"].column == "Other Total"
    assert fixed.transaction["amount"].confidence == 1.0
    assert fixed.transaction["amount"].reasons == ("set by hand",)
    assert guess.transaction.get("amount") is None or guess.transaction["amount"].column == "Value"
    removed = fixed.override("transaction", occurred_at=None)
    assert removed.missing() == {"transaction": ["occurred_at"]}
    assert "missing: transaction: occurred_at" in removed.explain()
    with pytest.raises(KeyError):
        guess.override("transaction", amount="Nope")
    with pytest.raises(ValueError):
        guess.override("bogus", amount="Value")
    cust = guess.override("customer", email="Other Total")
    assert cust.customer["email"].column == "Other Total"


def test_separate_customer_file_links_by_same_kind() -> None:
    sales = parse_csv_text("Bill No,Date,Mobile,Amount\nB1,2026-01-01,9999900001,10\n")
    custs = parse_csv_text("Name,Phone Number,Email\nAsha,9999900001,asha@example.com\n")
    guess = automap(sales.headers, sales.rows, customer_headers=custs.headers, customer_rows=custs.rows)
    assert guess.customer["external_id"].column == "Phone Number"
    assert guess.transaction["customer_ref"].column == "Mobile"
    assert guess.transaction["customer_ref"].link_kind == "phone"
    assert "matches the customer key" in guess.transaction["customer_ref"].reasons[-1]
    assert set(guess.unmapped) == set()


def test_separate_files_link_by_customer_id() -> None:
    sales = parse_csv_text("Order ID,Order Date,Customer ID,Total\nO1,2026-01-01,C-1,10\n")
    custs = parse_csv_text("Customer ID,Full Name\nC-1,Asha Rao\n")
    guess = automap(sales.headers, sales.rows, customer_headers=custs.headers, customer_rows=custs.rows)
    assert guess.transaction["customer_ref"].column == "Customer ID"
    assert guess.transaction["customer_ref"].link_kind == "external_id"
    assert guess.missing() == {}
    empty = automap(sales.headers, sales.rows, customer_headers=["Notes"], customer_rows=[{"Notes": "x"}])
    assert empty.missing() == {"customer": ["external_id"]}


def test_month_first_dates_detected() -> None:
    t = parse_csv_text("Date,Amount\n01/13/2026,5\n02/14/2026,6\n")
    assert automap(t.headers, t.rows).dayfirst is False


def test_nothing_mapped_explains_itself() -> None:
    guess = automap(["foo", "bar"], [{"foo": "1", "bar": "2"}])
    assert "(nothing mapped)" in guess.explain()
