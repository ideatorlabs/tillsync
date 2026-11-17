# Development

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
