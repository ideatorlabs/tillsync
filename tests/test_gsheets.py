from __future__ import annotations

import httpx
import pytest

from tillsync import GoogleSheetsConnector, to_csv_url
from tillsync.connectors.gsheets import SheetNotPublicError

SHEET = "https://docs.google.com/spreadsheets/d/AbC_123-xyz/edit#gid=42"
CSV = "Order No,Order Date,Phone,Total\nG1,2026-01-01 10:00,9999900001,100\nG2,2026-01-02,9999900002,50\n"


def test_to_csv_url() -> None:
    assert to_csv_url(SHEET) == "https://docs.google.com/spreadsheets/d/AbC_123-xyz/export?format=csv&gid=42"
    assert to_csv_url("https://docs.google.com/spreadsheets/d/AbC/edit").endswith("export?format=csv&gid=0")
    pub = "https://docs.google.com/spreadsheets/d/e/2PACX-1vX/pubhtml?gid=7"
    assert to_csv_url(pub) == "https://docs.google.com/spreadsheets/d/e/2PACX-1vX/pub?output=csv&gid=7"
    already = "https://docs.google.com/spreadsheets/d/AbC/export?format=csv&gid=1"
    assert to_csv_url(already) == already
    assert to_csv_url("https://files.example.com/sales.csv") == "https://files.example.com/sales.csv"
    assert to_csv_url("") == ""


def mock_client(body: str, seen: list[str]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reads_sheet_through_csv_connector() -> None:
    seen: list[str] = []
    conn = GoogleSheetsConnector(SHEET, client=mock_client(CSV, seen), default_country="91")
    assert conn.automapping is None
    assert conn.test_connection()
    txs = list(conn.fetch_transactions())
    assert [t.external_id for t in txs] == ["G1", "G2"]
    assert [c.external_id for c in conn.fetch_customers()] == ["+919999900001", "+919999900002"]
    assert conn.automapping is not None and conn.report.transactions == 2
    assert seen[0].endswith("/export?format=csv&gid=42")


def test_separate_customer_sheet() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if "gid=1" in str(req.url):
            return httpx.Response(200, text="Customer ID,Name\nC1,Asha\n")
        return httpx.Response(200, text="Bill No,Date,Customer ID\nB1,2026-01-01,C1\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    url = "https://docs.google.com/spreadsheets/d/S1/edit#gid=0"
    conn = GoogleSheetsConnector(url, customers_url=url.replace("gid=0", "gid=1"), client=client)
    assert [c.name for c in conn.fetch_customers()] == ["Asha"]
    assert [t.customer_external_id for t in conn.fetch_transactions()] == ["C1"]


def test_private_sheet_is_reported_clearly() -> None:
    conn = GoogleSheetsConnector(SHEET, client=mock_client("<!DOCTYPE html><html>Sign in</html>", []))
    with pytest.raises(SheetNotPublicError):
        list(conn.fetch_transactions())
    assert conn.test_connection() is False


def test_ssrf_guard_on_sheet_links() -> None:
    conn = GoogleSheetsConnector("http://127.0.0.1:1/sales.csv")
    assert conn.test_connection() is False
