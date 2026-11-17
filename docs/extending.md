# Extending

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
