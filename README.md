<p align="center"><img src="https://raw.githubusercontent.com/ideatorlabs/.github/main/assets/avatar.png" width="72" alt="Ideator Labs"></p>
<h1 align="center">TillSync</h1>
<p align="center"><b>Pull customers and sales from POS systems, APIs and spreadsheets into one clean shape.</b><br><sub>An open-source package by <a href="https://github.com/ideatorlabs">Ideator Labs</a></sub></p>
<p align="center">
<a href="https://github.com/ideatorlabs/tillsync/actions/workflows/ci.yml"><img src="https://github.com/ideatorlabs/tillsync/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
<img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
</p>

## Why it exists

Small businesses keep their customer and sales history in whatever their point-of-sale system
exports: a CSV with a title line and `Net Amount (₹)` headers, a Google Sheet a billing app syncs
to, or a REST API with its own paging and date formats. Every product that wants to use that data
(loyalty, CRM, analytics) ends up rewriting the same fragile import code. TillSync does that part
once: it reads each source, maps it to one normalized record shape, tells you what it left out and
why, and supports incremental pulls.

## Features

- **One record shape**: `RawCustomer` and `RawTransaction` (with `LineItem`s), timezone-aware UTC
  timestamps, `Decimal` money, refunds as negative amounts with `kind="refund"`.
- **Connectors**: CSV/TSV and Excel files, Google Sheets by share link, any JSON API by config
  (bearer/basic/header/query auth; page, offset, cursor and next-link paging; dot-path fields),
  Square, and a deterministic demo generator.
- **automap**: guesses which column is which from headers *and* sample values, with a confidence
  score and readable reasons for every field; override anything by hand.
- **Messy-data rules**: `₹1,20,000`, `Rs. 250/-`, `(250)`, `€1.234,56`; phone numbers to E.164 with a
  default country; hidden marketplace numbers (`98******10`) never become customers; placeholder
  names (`Guest`, `Walk-in`) are not names; cancelled bills, duplicates, unreadable dates and
  amounts are skipped and counted with examples, never silently turned into zero or "now".
- **Incremental sync**: `sync()` runs a connector from a saved cursor and hands batches to your
  callback. No database required.
- **SSRF guard** for user-supplied URLs: loopback, private and link-local addresses are blocked
  unless you allow them, on every redirect hop.
- **CLI**: `tillsync preview sales.csv` shows the guessed mapping and the first mapped rows.
- Small footprint: `httpx` is the only required dependency; Excel support is an optional extra.

## Install

```bash
pip install tillsync            # core
pip install 'tillsync[excel]'   # adds .xlsx support (openpyxl)
```

> Until the first PyPI release, install straight from GitHub:
> `pip install "tillsync @ git+https://github.com/ideatorlabs/tillsync"`


## 60-second quickstart

Run from a checkout of this repository (the sample file is fictional):

```python
from tillsync import CsvConnector, sync

conn = CsvConnector("examples/sample_sales.csv", timezone="Asia/Kolkata", default_country="91")
print(conn.automapping.explain())  # what was guessed for each field, and why

stored = {}


def save(batch):  # your storage: upsert by external_id
    for record in batch.records:
        stored[(batch.stream, record.external_id)] = record


result = sync(conn, save)
print(result.customers, "customers,", result.transactions, "bills")
print("left out:", conn.report.skipped)  # bad dates, cancelled bills, duplicates...
saved_cursor = result.cursor.to_json()  # keep this and pass it back next time
```

Or from the shell:

```bash
tillsync preview examples/sample_sales.csv --tz Asia/Kolkata --country 91
```

## Concepts

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

### Reviewing and overriding the mapping

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

### A JSON API

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

## Configuration

| Option | Where | Meaning |
| --- | --- | --- |
| `TILLSYNC_DEFAULT_COUNTRY` | environment | Calling code for bare national phone numbers when a connector has no `default_country` (default `1`). |
| `timezone` | CSV, Sheets, HTTP | IANA zone for times without an offset (default UTC). |
| `default_country` | CSV, Sheets, HTTP, Square | Calling code such as `"91"` or `"44"` for bare numbers. |
| `date_formats`, `dayfirst` | CSV, Sheets, HTTP | Extra `strptime` patterns; whether `03/04/2026` is 3 April (automap detects this from the data when it can). |
| `currency` | CSV, Sheets, HTTP | Default currency code when neither the cell nor the header names one. |
| `mapping` | CSV, Sheets | Dict or `AutoMapping`; guessed when omitted. |
| `allow_private` | Sheets, HTTP | Allow loopback and private-network URLs (link-local stays blocked). |
| `auth` | HTTP | `bearer`, `basic`, `header` or `query`; secrets as `token_env`/`password_env`/`value_env` to read environment variables. |
| `pagination` | HTTP | `none`, `page`, `offset`, `cursor`, `next_url`. |
| `since_param`, `since_format` | HTTP | Query parameter for incremental pulls and its format (`iso`, `iso_z`, `epoch`, `epoch_ms`, or a strftime pattern). |
| `amount_divisor` | HTTP | Divide amounts, e.g. `100` for APIs that send cents. |
| `max_pages`, `timeout` | HTTP | Safety limits (default 10,000 pages, 30 s). |
| `base_url`, `location_ids` | Square | API host (production by default; `SANDBOX_URL` for the sandbox) and locations to read. |

## Extending

Subclass `BaseConnector`, register it, and yield records:

```python
from decimal import Decimal

from tillsync import BaseConnector, RawCustomer, RawTransaction, create, parse_datetime, register


@register("my-pos")
class MyPosConnector(BaseConnector):
    name = "my-pos"

    def __init__(self, rows):
        super().__init__()
        self.rows = rows  # in real life: an API client, a database cursor...

    def fetch_customers(self, since=None):
        for r in self.rows:
            yield RawCustomer(external_id=r["customer"], name=r["name"], source=self.name)

    def fetch_transactions(self, since=None):
        for r in self.rows:
            when = parse_datetime(r["at"], tz="Europe/London")
            if when is None:
                self.report.skip("bad_date", value=r["at"])
                continue
            if since is None or when >= since:
                yield RawTransaction(
                    external_id=r["id"],
                    customer_external_id=r["customer"],
                    occurred_at=when,
                    amount=Decimal(r["total"]),
                    currency="GBP",
                    source=self.name,
                )


rows = [{"id": "1", "customer": "c1", "name": "Sam", "at": "2026-03-01 12:00", "total": "9.50"}]
conn = create("my-pos", rows=rows)
print(list(conn.fetch_transactions()))
```

For webhooks, `map_customer(payload, fields)` and `map_transaction(payload, fields)` apply the same
field-path mapping as the HTTP connector to a single JSON object. For item-level exports,
`read_line_items()` groups rows by bill number and `attach_line_items()` puts them on bills.

The helpers are usable on their own:

```python
from tillsync import BlockedURLError, MockConnector, check_url, normalize_phone, parse_money

demo = MockConnector("cafe", customers=20, seed=1)  # deterministic, fictional data
print(len(list(demo.fetch_transactions())), "demo bills")

assert parse_money("₹1,20,000") == 120000
assert parse_money("(250)") == -250
assert parse_money("two hundred") is None  # unreadable is None, never zero
assert normalize_phone("099999 00001", "91") == "+919999900001"
try:
    check_url("http://169.254.169.254/latest/meta-data/")
except BlockedURLError as exc:
    print("blocked:", exc)
```

## Running the example

```bash
uv venv --python 3.11 .venv && uv pip install -p .venv -e '.[dev,excel]'
.venv/bin/python examples/run_demo.py           # CSV import + JSON API synced twice
.venv/bin/python examples/pos_server.py         # or just run the demo API on :8765
.venv/bin/tillsync preview examples/sample_sales.csv --tz Asia/Kolkata --country 91
```

`examples/sample_sales.csv` is deliberately messy: a title line, currency in the header, lakh
grouping, refunds, a cancelled bill, a duplicate row, an unreadable date and amount, a hidden phone
number and a totals row.

## Testing

```bash
.venv/bin/pytest --cov=tillsync
```

Tests never touch the network: HTTP is faked with `httpx.MockTransport` or the in-process demo
server. The end-to-end test runs the example script and every Python block in this README.

## Limitations and non-goals

- No storage, scheduling or deduplication across runs: that is your callback's job (upsert by
  `external_id`). Delivery is at-least-once.
- Customer-summary exports (one row per customer with lifetime totals instead of one row per bill)
  are not interpreted; map them yourself.
- Phone handling is deliberately light (no per-country numbering plans). Use `phonenumbers` if you
  need strict validation.
- `automap` is a heuristic. Review `explain()` before trusting it with a new export layout.
- Google Sheets must be readable by link; there is no OAuth. Legacy `.xls` files are not read.
- The SSRF guard checks resolved addresses but cannot stop DNS rebinding between the check and the
  connection.
- Square: customers and orders only (no Payments API); refunds are read from the order.
- No ready-made presets for specific POS products: describe their API with an HTTP config.

## Roadmap

- Customer-summary file support.
- Optional `phonenumbers` integration.
- Webhook receiver helpers (signature checks) on top of `map_transaction`.
- More connectors contributed as HTTP configs.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
