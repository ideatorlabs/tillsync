"""The normalized record types every connector produces."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

__all__ = [
    "LineItem",
    "RawCustomer",
    "RawTransaction",
    "ImportReport",
    "StreamCursor",
    "Cursor",
    "to_jsonable",
]

SALE = "sale"
REFUND = "refund"


@dataclass
class LineItem:
    """One product or service line on a bill."""

    name: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal | None = None
    total: Decimal | None = None
    sku: str | None = None
    category: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawCustomer:
    """A customer as the source describes it, cleaned up.

    ``external_id`` is the source's own id when it has one, otherwise the strongest identity the
    row carries (normalized phone, then email, then name). ``phone`` is E.164 when it could be
    normalized. Timestamps are timezone-aware UTC.
    """

    external_id: str
    name: str = ""
    phone: str | None = None
    email: str | None = None
    first_seen_at: datetime | None = None
    updated_at: datetime | None = None
    source: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def cursor_time(self) -> datetime | None:
        """The timestamp incremental syncs order this record by."""
        return self.updated_at or self.first_seen_at


@dataclass
class RawTransaction:
    """A bill, order or refund.

    ``occurred_at`` is timezone-aware UTC. When the source gave only a date, it is midnight in the
    connector's time zone and ``time_known`` is False. ``amount`` is negative for refunds
    (``kind == "refund"``) and None when the source has no amount at all (a plain visit list).
    ``customer_external_id`` is empty for walk-in bills with no known customer.
    """

    external_id: str
    customer_external_id: str
    occurred_at: datetime
    amount: Decimal | None
    kind: str = SALE
    currency: str | None = None
    time_known: bool = True
    channel: str | None = None
    store_id: str | None = None
    store_name: str | None = None
    updated_at: datetime | None = None
    line_items: list[LineItem] = field(default_factory=list)
    source: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def cursor_time(self) -> datetime:
        """The timestamp incremental syncs order this record by."""
        return self.updated_at or self.occurred_at


@dataclass
class ImportReport:
    """What a connector read, and what it left out and why.

    ``skipped`` counts rows left out per reason (``bad_date``, ``bad_amount``, ``cancelled``,
    ``duplicate``...). ``warnings`` counts rows kept with a change (``hidden_phone``: a masked
    number was dropped and the bill kept as a walk-in). ``examples`` keeps the first few
    ``(row, value)`` pairs per reason so a person can find and fix them.
    """

    rows: int = 0
    customers: int = 0
    transactions: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    warnings: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[tuple[int, str]]] = field(default_factory=dict)

    max_examples = 5

    def _example(self, reason: str, row: int, value: object) -> None:
        bucket = self.examples.setdefault(reason, [])
        if len(bucket) < self.max_examples:
            bucket.append((row, str(value)[:60]))

    def skip(self, reason: str, row: int = 0, value: object = "") -> None:
        """Count a row that was left out."""
        self.skipped[reason] = self.skipped.get(reason, 0) + 1
        self._example(reason, row, value)

    def warn(self, reason: str, row: int = 0, value: object = "") -> None:
        """Count a row that was kept but changed (for example a hidden phone dropped from a bill)."""
        self.warnings[reason] = self.warnings.get(reason, 0) + 1
        self._example(reason, row, value)

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped.values())


@dataclass
class StreamCursor:
    """Where one stream (customers or transactions) got to.

    ``since`` is the newest record time already delivered; ``seen_ids`` are the ids delivered at
    exactly that time, so a source that filters with ``>=`` does not deliver them twice.
    """

    since: datetime | None = None
    seen_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"since": self.since.isoformat() if self.since else None, "seen_ids": list(self.seen_ids)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StreamCursor:
        data = data or {}
        since = data.get("since")
        return cls(
            since=datetime.fromisoformat(since) if since else None,
            seen_ids=[str(x) for x in data.get("seen_ids") or []],
        )


@dataclass
class Cursor:
    """Resume point for ``tillsync.sync``. Store ``to_json()`` and pass it back next time."""

    customers: StreamCursor = field(default_factory=StreamCursor)
    transactions: StreamCursor = field(default_factory=StreamCursor)

    def stream(self, name: str) -> StreamCursor:
        if name not in ("customers", "transactions"):
            raise ValueError(f"unknown stream {name!r}")
        cur: StreamCursor = getattr(self, name)
        return cur

    def to_dict(self) -> dict[str, Any]:
        return {"customers": self.customers.to_dict(), "transactions": self.transactions.to_dict()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Cursor:
        data = data or {}
        return cls(
            customers=StreamCursor.from_dict(data.get("customers")),
            transactions=StreamCursor.from_dict(data.get("transactions")),
        )

    @classmethod
    def from_json(cls, text: str | None) -> Cursor:
        return cls.from_dict(json.loads(text) if text else None)


def to_jsonable(value: Any) -> Any:
    """Convert records (and anything inside them) to plain JSON-friendly values."""
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
