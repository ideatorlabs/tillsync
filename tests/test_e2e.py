"""End to end: the example JSON "POS" server over real HTTP, the example CSV, and the README."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pos_server import Store, serve_in_background

from tillsync import BlockedURLError, Cursor, HttpJsonConnector, sync


@pytest.fixture
def pos(examples_dir: Path) -> Any:
    store = Store()
    server, base_url = serve_in_background(store)
    config = json.loads((examples_dir / "http_config.json").read_text())
    config["base_url"] = base_url
    config["auth"] = {"type": "bearer", "token": "demo-token"}
    yield store, config
    server.shutdown()
    server.server_close()


def test_http_pulls_every_page_and_resumes_from_cursor(pos: Any) -> None:
    store, config = pos
    db: dict[str, dict[str, Any]] = {"customers": {}, "transactions": {}}
    batches: list[int] = []

    def save(batch: Any) -> None:
        batches.append(len(batch))
        for rec in batch.records:
            db[batch.stream][rec.external_id] = rec

    conn = HttpJsonConnector(config)
    assert conn.test_connection()
    first = sync(conn, save, batch_size=2)
    assert sorted(db["customers"]) == ["C-1", "C-2", "C-3", "C-5"]  # the anonymous "Guest" has no identity
    assert sorted(db["transactions"]) == ["O-101", "O-102", "O-103", "O-106"]
    assert batches == [2, 2, 2, 2]
    assert conn.report.skipped == {"no_identity": 1, "cancelled": 1, "bad_date": 1}

    c2 = db["customers"]["C-2"]
    assert c2.name == "Ravi Shah" and c2.phone == "+919999900002"
    assert db["customers"]["C-3"].email == "sam@example.com"
    # a naive time is read in the configured zone (Asia/Kolkata), stored as UTC
    assert db["customers"]["C-3"].first_seen_at == datetime(2026, 1, 5, 2, 30, tzinfo=timezone.utc)
    o101 = db["transactions"]["O-101"]
    assert o101.occurred_at == datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)
    assert [li.name for li in o101.line_items] == ["Cappuccino", "Cheesecake"]
    assert db["transactions"]["O-102"].occurred_at == datetime(2026, 1, 4, 7, 0, tzinfo=timezone.utc)
    assert db["transactions"]["O-102"].amount == Decimal(480)
    refund = db["transactions"]["O-103"]
    assert refund.kind == "refund" and refund.amount == Decimal(-250)
    assert refund.occurred_at == datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)  # epoch seconds
    assert db["transactions"]["O-106"].customer_external_id == ""

    saved = first.cursor.to_json()
    assert first.cursor.transactions.since == datetime(2026, 1, 7, 14, 15, tzinfo=timezone.utc)

    # nothing new: nothing delivered, even though the API returns the boundary record again (>=)
    again = sync(HttpJsonConnector(config), save, cursor=Cursor.from_json(saved))
    assert again.customers == 0 and again.transactions == 0 and again.already_seen >= 2

    # new orders, one at exactly the cursor time, then resume from the saved cursor
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
    store.add_order(
        {
            "number": "O-108",
            "customer": {"id": "C-1"},
            "closed_at": "2026-01-07T14:15:00Z",
            "updated_at": "2026-01-07T14:15:00Z",
            "total": "10",
            "state": "completed",
            "type": "sale",
            "items": [],
        }
    )
    store.customers.append(
        {
            "id": "C-6",
            "first_name": "Nina",
            "last_name": "Gill",
            "contact": {},
            "created_at": "2026-01-09T10:00:00Z",
            "updated_at": "2026-01-09T10:00:00Z",
        }
    )
    resumed = sync(HttpJsonConnector(config), save, cursor=Cursor.from_json(saved))
    assert resumed.transactions == 2 and resumed.customers == 1
    assert sorted(db["transactions"]) == ["O-101", "O-102", "O-103", "O-106", "O-107", "O-108"]
    assert resumed.cursor.transactions.since == datetime(2026, 1, 9, 11, 0, tzinfo=timezone.utc)


def test_http_server_rejects_bad_token(pos: Any) -> None:
    _, config = pos
    config["auth"] = {"type": "bearer", "token": "wrong"}
    conn = HttpJsonConnector(config)
    assert conn.test_connection() is False


def test_localhost_is_blocked_without_the_allow_flag(pos: Any) -> None:
    _, config = pos
    config.pop("allow_private")
    with pytest.raises(BlockedURLError):
        list(HttpJsonConnector(config).fetch_customers())
    assert len(list(HttpJsonConnector(config, allow_private=True).fetch_customers())) == 4


def test_example_script_runs(capsys: pytest.CaptureFixture[str]) -> None:
    import run_demo

    csv_result = run_demo.csv_demo()
    assert csv_result == {
        "customers": 6,
        "transactions": 7,
        "skipped": {"cancelled": 1, "duplicate": 1, "bad_date": 1, "bad_amount": 1, "total_row": 1},
    }
    http_result = run_demo.http_demo()
    assert http_result["first"].transactions == 4 and http_result["second"].transactions == 1
    run_demo.main()
    out = capsys.readouterr().out
    assert "second run: 0 customers, 1 transactions" in out


def test_readme_python_blocks_run(root_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    readme = (root_dir / "README.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", readme, re.S)
    assert len(blocks) >= 3
    monkeypatch.chdir(root_dir)
    monkeypatch.setitem(os.environ, "DEMO_POS_TOKEN", "demo-token")
    namespace: dict[str, Any] = {"__name__": "readme"}
    for block in blocks:
        exec(compile(block, "README.md", "exec"), namespace)
