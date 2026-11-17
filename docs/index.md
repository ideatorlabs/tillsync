---
hide:
  - navigation
---

<div class="il-hero" markdown>
<img src="assets/brand/icon-512.png" alt="" width="112" height="112">
<div markdown>
# TillSync
<p class="il-tagline">Every POS, API and spreadsheet, one clean shape</p>
<p class="il-by">An open-source package by <a href="https://ideatorlabs.github.io/">Ideator Labs</a></p>
[Get started](#install){ .md-button .md-button--primary }
[GitHub](https://github.com/ideatorlabs/tillsync){ .md-button }
</div>
</div>

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
