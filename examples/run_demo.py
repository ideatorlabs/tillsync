"""End-to-end demo: a CSV export and a paginated JSON API, synced incrementally.

Run from the repository root:

    python examples/run_demo.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from tillsync import Batch, CsvConnector, Cursor, HttpJsonConnector, sync

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pos_server import Store, serve_in_background  # noqa: E402


def csv_demo() -> dict[str, Any]:
    conn = CsvConnector(HERE / "sample_sales.csv", timezone="Asia/Kolkata", default_country="91")
    print("== CSV export ==")
    assert conn.automapping is not None
    print(conn.automapping.explain())
    stored: list[Any] = []
    result = sync(conn, lambda batch: stored.extend(batch.records))
    print(f"{result.customers} customers, {result.transactions} transactions")
    print("left out:", conn.report.skipped, "changed:", conn.report.warnings)
    return {
        "customers": result.customers,
        "transactions": result.transactions,
        "skipped": conn.report.skipped,
    }


def http_demo() -> dict[str, Any]:
    print("\n== JSON API ==")
    store = Store()
    server, base_url = serve_in_background(store)
    try:
        config = json.loads((HERE / "http_config.json").read_text())
        config["base_url"] = base_url
        os.environ.setdefault("DEMO_POS_TOKEN", "demo-token")

        db: dict[str, dict[str, Any]] = {"customers": {}, "transactions": {}}

        def save(batch: Batch) -> None:  # an "upsert by external_id" into a dict
            for rec in batch.records:
                db[batch.stream][rec.external_id] = rec

        first = sync(HttpJsonConnector(config), save, batch_size=2)
        saved_cursor = first.cursor.to_json()
        print(f"first run: {first.customers} customers, {first.transactions} transactions")

        store.add_order(
            {
                "number": "O-107",
                "customer": {"id": "C-2"},
                "closed_at": "2026-01-09T11:00:00Z",
                "updated_at": "2026-01-09T11:00:00Z",
                "total": "720",
                "state": "completed",
                "type": "sale",
                "items": [],
            }
        )
        second = sync(HttpJsonConnector(config), save, cursor=Cursor.from_json(saved_cursor))
        print(f"second run: {second.customers} customers, {second.transactions} transactions")
        print("stored:", len(db["customers"]), "customers,", len(db["transactions"]), "transactions")
        return {"first": first, "second": second, "db": db}
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    csv_demo()
    http_demo()


if __name__ == "__main__":
    main()
