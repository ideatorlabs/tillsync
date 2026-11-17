"""Command line: ``tillsync preview <file-or-url>`` shows how a source maps before you import it."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

import httpx

from . import __version__
from .automap import AutoMapping
from .base import BaseConnector
from .connectors import CsvConnector, GoogleSheetsConnector, MockConnector
from .models import RawCustomer, RawTransaction, to_jsonable


def _connector(args: argparse.Namespace) -> BaseConnector:
    src: str = args.source
    common: dict[str, Any] = {"timezone": args.tz, "default_country": args.country}
    if src.startswith("mock:") or src == "mock":
        sector = src.partition(":")[2] or "cafe"
        return MockConnector(sector, customers=max(args.limit, 5))
    if src.startswith(("http://", "https://")):
        return GoogleSheetsConnector(
            src, customers_url=args.customers, allow_private=args.allow_private, **common
        )
    return CsvConnector(src, customers_path=args.customers, **common)


def _tx_row(t: RawTransaction) -> list[str]:
    return [
        t.external_id[:18],
        t.occurred_at.strftime("%Y-%m-%d %H:%M") if t.time_known else t.occurred_at.strftime("%Y-%m-%d"),
        t.customer_external_id[:20] or "(walk-in)",
        "" if t.amount is None else f"{t.amount:.2f}",
        t.kind,
    ]


def _cust_row(c: RawCustomer) -> list[str]:
    return [c.external_id[:20], c.name[:22], c.phone or "", c.email or ""]


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=False))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)) for r in rows)
    return "\n".join(lines)


def preview(args: argparse.Namespace) -> int:
    conn = _connector(args)
    # read everything so the report covers the whole source, then show the first rows
    all_customers = list(conn.fetch_customers())
    all_transactions = list(conn.fetch_transactions())
    customers, transactions = all_customers[: args.limit], all_transactions[: args.limit]
    mapping: AutoMapping | None = getattr(conn, "automapping", None)
    if args.json:
        out = {
            "mapping": mapping.mapping() if mapping else None,
            "customers": to_jsonable(customers),
            "transactions": to_jsonable(transactions),
            "report": to_jsonable(conn.report),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    if mapping is not None:
        print("Column mapping (guessed; pass an explicit mapping in code to change it)")
        print(mapping.explain())
        print()
    print(f"First {len(customers)} of {len(all_customers)} customers")
    print(_table(["id", "name", "phone", "email"], [_cust_row(c) for c in customers]))
    print()
    print(f"First {len(transactions)} of {len(all_transactions)} transactions")
    print(_table(["bill", "when (UTC)", "customer", "amount", "kind"], [_tx_row(t) for t in transactions]))
    if conn.report.skipped or conn.report.warnings:
        print()
        print("Left out: " + (", ".join(f"{k}={v}" for k, v in conn.report.skipped.items()) or "nothing"))
        if conn.report.warnings:
            print("Changed: " + ", ".join(f"{k}={v}" for k, v in conn.report.warnings.items()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tillsync", description="Preview customers and sales from a source."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preview", help="show the guessed mapping and the first mapped rows")
    p.add_argument("source", help="a .csv/.tsv/.xlsx file, a Google Sheets or CSV URL, or mock[:sector]")
    p.add_argument("--customers", help="a separate customers file or URL")
    p.add_argument("--limit", type=int, default=10, help="rows to show per stream (default 10)")
    p.add_argument("--tz", default=None, help="time zone for times without one, e.g. Asia/Kolkata")
    p.add_argument("--country", default=None, help="calling code for bare phone numbers, e.g. 91")
    p.add_argument("--allow-private", action="store_true", help="allow URLs on localhost or private networks")
    p.add_argument("--json", action="store_true", help="print JSON instead of tables")
    p.set_defaults(func=preview)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code: int = args.func(args)
        return code
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"tillsync: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
