"""A deterministic demo source: believable customers and bills with no real data behind them.

Useful for demos, UI work and tests. The same ``seed`` always gives the same data. Customers
fall into a few behaviour patterns (regulars, occasional visitors, lapsed customers, one-time
buyers) so downstream analytics have something to find. Every name, email and phone number is
made up; phone numbers use the ``+9199999`` prefix, which is not assigned to anyone.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..base import BaseConnector, register
from ..models import REFUND, SALE, LineItem, RawCustomer, RawTransaction

__all__ = ["MockConnector", "CATALOGS"]

CATALOGS: dict[str, list[tuple[str, int]]] = {
    "cafe": [
        ("Cappuccino", 180), ("Latte", 200), ("Cold Brew", 240), ("Masala Chai", 90), ("Croissant", 150),
        ("Veg Sandwich", 170), ("Pasta", 320), ("Brownie", 140), ("Cheesecake", 260), ("Lemonade", 120),
    ],
    "retail": [
        ("T-Shirt", 699), ("Jeans", 1499), ("Jacket", 2299), ("Sneakers", 2799), ("Cap", 399),
        ("Socks", 299), ("Hoodie", 1399), ("Shirt", 1199), ("Backpack", 1599), ("Watch", 2499),
    ],
    "salon": [
        ("Haircut", 400), ("Beard Trim", 200), ("Hair Colour", 1800), ("Facial", 1200), ("Manicure", 600),
        ("Pedicure", 700), ("Hair Spa", 1500), ("Head Massage", 350),
    ],
}  # fmt: skip

_FIRST = ["Asha", "Ravi", "Sam", "Meera", "Arjun", "Nina", "Kabir", "Lena", "Omar", "Priya", "Dev", "Tara",
          "Ivan", "Zoya", "Rohan", "Maya", "Neel", "Sara", "Aditi", "Leo"]  # fmt: skip
_LAST = ["Rao", "Shah", "Iyer", "Khan", "Das", "Mehta", "Nair", "Singh", "Patel", "Bose", "Menon", "Gill"]
_CHANNELS = ["in_store", "takeaway", "delivery"]

# (pattern, share, days since last visit, days between visits, max visits)
_PATTERNS = [
    ("regular", 0.25, (1, 10), (5, 12), 40),
    ("occasional", 0.20, (10, 45), (20, 45), 8),
    ("lapsed", 0.20, (90, 200), (10, 30), 12),
    ("new", 0.10, (0, 15), (5, 10), 3),
    ("one_time", 0.25, None, None, 1),
]


@register("mock")
class MockConnector(BaseConnector):
    """Generate ``customers`` customers and their bills for a ``sector`` (cafe, retail, salon)."""

    name = "mock"

    def __init__(
        self,
        sector: str = "cafe",
        customers: int = 50,
        *,
        seed: int = 7,
        now: datetime | None = None,
        currency: str = "INR",
        refund_rate: float = 0.02,
    ) -> None:
        super().__init__()
        if sector not in CATALOGS:
            raise ValueError(f"unknown sector {sector!r}; choose from {sorted(CATALOGS)}")
        self.sector = sector
        self.currency = currency
        self.now = (now or datetime(2026, 1, 31, 18, 0, tzinfo=timezone.utc)).astimezone(timezone.utc)
        rng = random.Random(f"{seed}:{sector}:{customers}")
        self._customers: list[RawCustomer] = []
        self._transactions: list[RawTransaction] = []
        for i in range(customers):
            self._make_customer(rng, i, refund_rate)
        self._transactions.sort(key=lambda t: t.occurred_at)

    def _pattern(self, rng: random.Random) -> tuple[str, tuple[int, int] | None, tuple[int, int] | None, int]:
        roll, acc = rng.random(), 0.0
        for name, share, last_gap, cadence, max_visits in _PATTERNS:
            acc += share
            if roll <= acc:
                return name, last_gap, cadence, max_visits
        name, _, last_gap, cadence, max_visits = _PATTERNS[-1]
        return name, last_gap, cadence, max_visits

    def _make_customer(self, rng: random.Random, i: int, refund_rate: float) -> None:
        pattern, last_gap, cadence, max_visits = self._pattern(rng)
        cid = f"C{i + 1:04d}"
        name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        visits: list[datetime] = []
        if last_gap is None or cadence is None:
            visits.append(self.now - timedelta(days=rng.randint(1, 300), hours=rng.randint(0, 10)))
        else:
            t = self.now - timedelta(days=rng.randint(*last_gap), hours=rng.randint(0, 10))
            for _ in range(rng.randint(1, max_visits)):
                visits.append(t)
                t -= timedelta(days=rng.randint(*cadence), hours=rng.randint(-3, 3))
        for k, when in enumerate(sorted(visits)):
            self._transactions.extend(self._bill(rng, cid, k, when, refund_rate))
        self._customers.append(
            RawCustomer(
                external_id=cid,
                name=name,
                phone=f"+9199999{i + 1:05d}",
                email=f"{name.lower().replace(' ', '.')}.{i + 1}@example.com",
                first_seen_at=min(visits),
                updated_at=max(visits),
                source="mock",
                attributes={"pattern": pattern},
            )
        )

    def _bill(
        self, rng: random.Random, cid: str, k: int, when: datetime, refund_rate: float
    ) -> list[RawTransaction]:
        picks = [rng.choice(CATALOGS[self.sector]) for _ in range(rng.randint(1, 3))]
        items = [
            LineItem(name=n, quantity=Decimal(1), unit_price=Decimal(p), total=Decimal(p)) for n, p in picks
        ]
        total = sum((i.total or Decimal(0) for i in items), Decimal(0))
        bill = RawTransaction(
            external_id=f"{cid}-B{k + 1:03d}",
            customer_external_id=cid,
            occurred_at=when,
            amount=total,
            kind=SALE,
            currency=self.currency,
            channel=rng.choice(_CHANNELS),
            store_id="S1",
            store_name="Demo Store",
            line_items=items,
            source="mock",
        )
        out = [bill]
        if rng.random() < refund_rate:
            out.append(
                RawTransaction(
                    external_id=f"{bill.external_id}-R",
                    customer_external_id=cid,
                    occurred_at=when + timedelta(hours=2),
                    amount=-items[0].unit_price if items[0].unit_price is not None else None,
                    kind=REFUND,
                    currency=self.currency,
                    store_id="S1",
                    store_name="Demo Store",
                    source="mock",
                    attributes={"refund_of": bill.external_id},
                )
            )
        return out

    def test_connection(self) -> bool:
        return True

    def fetch_customers(self, since: datetime | None = None) -> Iterator[RawCustomer]:
        for c in self._customers:
            if since is None or (c.cursor_time is not None and c.cursor_time >= since):
                yield c

    def fetch_transactions(self, since: datetime | None = None) -> Iterator[RawTransaction]:
        for t in self._transactions:
            if since is None or t.cursor_time >= since:
                yield t
