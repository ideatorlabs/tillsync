"""Run a connector from a cursor and hand records to your code in batches.

There is no database here: your callback stores the records however you like (upsert by
``external_id``), and you persist the returned cursor for next time::

    result = sync(connector, save_batch, cursor=Cursor.from_json(saved))
    saved = result.cursor.to_json()

Delivery is at-least-once. The cursor only moves forward once a stream has been fully handed to
your callback, so if the callback raises, the old cursor stays valid and the next run repeats
the work. Records at exactly the cursor time that were already delivered are not repeated.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .base import Connector
from .models import Cursor, RawCustomer, RawTransaction, StreamCursor

__all__ = ["Batch", "SyncResult", "sync", "STREAMS"]

Stream = Literal["customers", "transactions"]
STREAMS: tuple[Stream, ...] = ("customers", "transactions")
Record = RawCustomer | RawTransaction


@dataclass
class Batch:
    """Up to ``batch_size`` records of one stream."""

    stream: Stream
    records: list[Record]

    def __len__(self) -> int:
        return len(self.records)


@dataclass
class SyncResult:
    cursor: Cursor
    delivered: dict[str, int] = field(default_factory=dict)
    already_seen: int = 0
    duplicates: int = 0
    older_than_cursor: int = 0
    batches: int = 0

    @property
    def customers(self) -> int:
        return self.delivered.get("customers", 0)

    @property
    def transactions(self) -> int:
        return self.delivered.get("transactions", 0)


def _fetch(connector: Connector, stream: Stream, since: datetime | None) -> Iterable[Record]:
    if stream == "customers":
        return connector.fetch_customers(since=since)
    return connector.fetch_transactions(since=since)


def sync(
    connector: Connector,
    on_batch: Callable[[Batch], object],
    *,
    cursor: Cursor | None = None,
    batch_size: int = 500,
    streams: Sequence[Stream] = STREAMS,
) -> SyncResult:
    """Pull every stream newer than ``cursor`` and call ``on_batch`` for each batch.

    Returns the counts and the new cursor. Customers are synced before transactions so a
    callback can link bills to customers it has already stored.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    start = cursor or Cursor()
    new = Cursor.from_dict(start.to_dict())
    result = SyncResult(cursor=new)
    for stream in streams:
        sc = start.stream(stream)
        since = sc.since
        seen_at_since = set(sc.seen_ids)
        high = since
        high_ids = set(seen_at_since)
        delivered_ids: set[str] = set()
        batch: list[Record] = []
        count = 0
        for rec in _fetch(connector, stream, since):
            t = rec.cursor_time
            if since is not None and t is not None:
                if t < since:
                    result.older_than_cursor += 1
                    continue
                if t == since and rec.external_id in seen_at_since:
                    result.already_seen += 1
                    continue
            if rec.external_id in delivered_ids:
                result.duplicates += 1
                continue
            delivered_ids.add(rec.external_id)
            batch.append(rec)
            count += 1
            if t is not None:
                if high is None or t > high:
                    high, high_ids = t, {rec.external_id}
                elif t == high:
                    high_ids.add(rec.external_id)
            if len(batch) >= batch_size:
                on_batch(Batch(stream, batch))
                result.batches += 1
                batch = []
        if batch:
            on_batch(Batch(stream, batch))
            result.batches += 1
        result.delivered[stream] = count
        setattr(new, stream, StreamCursor(since=high, seen_ids=sorted(high_ids)))
    return result
