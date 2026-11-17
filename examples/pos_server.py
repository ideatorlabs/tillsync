"""A tiny paginated JSON "POS" API to try the HTTP connector against, with fictional data.

Run it on its own with ``python examples/pos_server.py`` (serves http://127.0.0.1:8765), or
start it in a background thread from Python with :func:`serve_in_background`.

Endpoints (both need ``Authorization: Bearer demo-token``):

* ``GET /api/customers?page=1&per_page=2&updated_since=<iso>`` - page-numbered, with a total
* ``GET /api/orders?limit=2&cursor=<token>&updated_since=<iso>`` - cursor-paginated
"""

from __future__ import annotations

import contextlib
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

TOKEN = "demo-token"


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(timezone.utc)


class Store:
    """In-memory customers and orders. Tests add records to see incremental syncs pick them up."""

    def __init__(self) -> None:
        self.customers: list[dict[str, Any]] = [
            {"id": "C-1", "first_name": "Asha", "last_name": "Rao", "contact": {"mobile": "919999900001",
             "email": "asha@example.com"}, "created_at": "2025-11-02T10:00:00+05:30",
             "updated_at": "2026-01-03T09:00:00+05:30"},
            {"id": "C-2", "first_name": "Ravi", "last_name": "Shah", "contact": {"mobile": "9999900002"},
             "created_at": "2025-12-15T18:20:00+05:30", "updated_at": "2026-01-04T12:00:00+05:30"},
            {"id": "C-3", "first_name": "Sam", "last_name": "", "contact": {"email": "SAM@example.com"},
             "created_at": "2026-01-05 08:00:00", "updated_at": "2026-01-05 08:00:00"},
            {"id": "", "first_name": "Guest", "last_name": "", "contact": {},
             "created_at": "2026-01-06T10:00:00Z", "updated_at": "2026-01-06T10:00:00Z"},
            {"id": "C-5", "first_name": "Meera", "last_name": "Nair", "contact": {"mobile": "9999900005"},
             "created_at": "2026-01-07T10:00:00Z", "updated_at": "2026-01-07T10:00:00Z"},
        ]  # fmt: skip
        self.orders: list[dict[str, Any]] = [
            {"number": "O-101", "customer": {"id": "C-1"}, "closed_at": "2026-01-03T09:15:00+05:30",
             "updated_at": "2026-01-03T09:15:00+05:30", "total": "1250.00", "state": "completed",
             "type": "sale", "items": [{"title": "Cappuccino", "qty": 2, "price": "180"},
                                       {"title": "Cheesecake", "qty": 1, "price": "260"}]},
            {"number": "O-102", "customer": {"id": "C-2"}, "closed_at": "2026-01-04 12:30:00",
             "updated_at": "2026-01-04T07:00:00Z", "total": "₹480", "state": "completed", "type": "sale",
             "items": []},
            {"number": "O-103", "customer": {"id": "C-1"}, "closed_at": 1767600000,
             "updated_at": "2026-01-05T08:00:00Z", "total": "250", "state": "completed",
             "type": "sales return", "items": []},
            {"number": "O-104", "customer": {"id": "C-3"}, "closed_at": "2026-01-06T13:00:00Z",
             "updated_at": "2026-01-06T13:00:00Z", "total": "640", "state": "cancelled", "type": "sale",
             "items": []},
            {"number": "O-105", "customer": {"id": "C-5"}, "closed_at": "yesterday-ish",
             "updated_at": "2026-01-07T10:00:00Z", "total": "300", "state": "completed", "type": "sale",
             "items": []},
            {"number": "O-106", "customer": None, "closed_at": "2026-01-07T19:45:00+05:30",
             "updated_at": "2026-01-07T14:15:00Z", "total": "99.50", "state": "completed", "type": "sale",
             "items": [{"title": "Latte", "qty": 1, "price": "99.50"}]},
        ]  # fmt: skip
        self.lock = threading.Lock()

    def add_order(self, order: dict[str, Any]) -> None:
        with self.lock:
            self.orders.append(order)


def _since_filter(rows: list[dict[str, Any]], since: str | None) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda r: (_t(_iso(r["updated_at"])), str(r.get("id") or r.get("number"))))
    if not since:
        return rows
    cut = _t(since)
    return [r for r in rows if _t(_iso(r["updated_at"])) >= cut]


def _iso(value: str) -> str:
    return value.replace("Z", "+00:00") if "T" in value or "+" in value else value + "+00:00"


def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # keep test output quiet
            pass

        def _send(self, status: int, body: Any) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._send(401, {"error": "unauthorized"})
                return
            url = urlsplit(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            with store.lock:
                if url.path == "/api/customers":
                    rows = _since_filter(store.customers, q.get("updated_since"))
                    page, per_page = int(q.get("page", 1)), int(q.get("per_page", 2))
                    chunk = rows[(page - 1) * per_page : page * per_page]
                    self._send(200, {"data": chunk, "meta": {"page": page, "total": len(rows)}})
                elif url.path == "/api/orders":
                    rows = _since_filter(store.orders, q.get("updated_since"))
                    start, limit = int(q.get("cursor", 0)), int(q.get("limit", 2))
                    chunk = rows[start : start + limit]
                    nxt = str(start + limit) if start + limit < len(rows) else None
                    self._send(200, {"orders": chunk, "next_cursor": nxt})
                else:
                    self._send(404, {"error": "not found"})

    return Handler


def make_server(store: Store | None = None, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(store or Store()))


def serve_in_background(store: Store | None = None) -> tuple[ThreadingHTTPServer, str]:
    """Start the server on a free port in a daemon thread. Returns (server, base_url)."""
    server = make_server(store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


if __name__ == "__main__":
    srv = make_server(port=8765)
    print("demo POS API on http://127.0.0.1:8765 (token: demo-token); Ctrl+C to stop")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()
