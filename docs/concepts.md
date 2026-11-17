# Concepts

```
 sources                       connectors                  your code
 ---------------------------   -------------------------   -----------------------------
 sales.csv / .tsv / .xlsx ---> CsvConnector  --+
 Google Sheet link        ---> GoogleSheets  --+
 JSON API + config        ---> HttpJson      --+--> sync(connector, on_batch, cursor)
 Square                   ---> Square        --+        |  Batch("customers", [RawCustomer...])
 demo data                ---> Mock          --+        |  Batch("transactions", [RawTransaction...])
 your POS                 ---> @register(...) -+        v
                                                     your callback (upsert by external_id)
                                                     result.cursor -> save for next run
```

- **Connector**: anything with `fetch_customers(since)` and `fetch_transactions(since)` returning
  records whose `cursor_time` is at or after `since`. Each connector has a `report`
  (`ImportReport`) with counts and examples of what it skipped (`skipped`) or changed (`warnings`).
- **Customer key**: a customer's `external_id` is the source's id if it has one, otherwise the
  strongest identity on the row: normalized phone, then email, then name. Bills point at the same
  key in `customer_external_id` (empty for walk-in bills).
- **Time**: values with an offset or `Z` are respected; values without one are read in the
  connector's `timezone`; date-only values are local midnight with `time_known=False`.
- **Cursor**: per stream, the newest record time delivered plus the ids delivered at exactly that
  time. Sources are asked for `>= since`, and `sync()` drops what was already delivered.
  Delivery is at-least-once; upsert by `external_id`.

## Reviewing and overriding the mapping

```python
from tillsync import CsvConnector, automap, read_table

table = read_table("examples/sample_sales.csv")
guess = automap(table.headers, table.rows)
for field, g in guess.transaction.items():
    print(field, "<-", g.column, f"{g.confidence:.0%}", "|", "; ".join(g.reasons))

guess = guess.override("transaction", store_name=None)  # drop a field, or set field="Column"
conn = CsvConnector(table=table, mapping=guess, timezone="Asia/Kolkata", default_country="91")
assert all(t.store_name is None for t in conn.fetch_transactions())

# or skip automap entirely with a plain dict
plain = CsvConnector(
    table=table,
    mapping={
        "customer": {"external_id": "Mobile No.", "name": "Customer Name"},
        "transaction": {"external_id": "Bill No.", "occurred_at": "Bill Date", "amount": "Net Amount (₹)"},
    },
    default_country="91",
)
```

Customer fields: `external_id`, `name`, `phone`, `email`, `first_seen_at`. Transaction fields:
`external_id`, `customer_ref` (separate customer file only), `occurred_at` (required),
`occurred_time`, `amount`, `currency`, `status`, `doc_type`, `channel`, `store_id`, `store_name`.
Every unmapped column is kept in `attributes`.

## A JSON API

A connector for a REST API is a config, not code. `examples/http_config.json` describes the demo
API in `examples/pos_server.py` (page-numbered customers, cursor-paginated orders, bearer token):

```python
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "examples")
from pos_server import serve_in_background  # a local demo API with fictional data

from tillsync import Cursor, HttpJsonConnector, sync

server, base_url = serve_in_background()
config = json.loads(Path("examples/http_config.json").read_text())
config["base_url"] = base_url
os.environ.setdefault("DEMO_POS_TOKEN", "demo-token")  # the config reads the token from here

first = sync(HttpJsonConnector(config), lambda batch: print(batch.stream, len(batch)))
cursor = first.cursor.to_json()
again = sync(HttpJsonConnector(config), lambda batch: None, cursor=Cursor.from_json(cursor))
print("new since last run:", again.customers, again.transactions)
server.shutdown()
```

The demo API runs on `127.0.0.1`, so the config sets `"allow_private": true`. Without it the SSRF
guard refuses the request.
