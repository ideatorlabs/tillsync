"""Item-level exports: one row per product sold, keyed by bill number.

Many POS systems export bills and their items separately. ``read_line_items`` groups an item
file by bill number and ``attach_line_items`` puts the items onto bills you already read.
Attaching replaces a bill's items, so re-running it never doubles them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from .automap import normalize_header
from .models import LineItem, RawTransaction
from .money import parse_money
from .names import clean_text
from .tables import Table

__all__ = ["LINE_SYNONYMS", "guess_line_mapping", "read_line_items", "attach_line_items"]

LINE_SYNONYMS: dict[str, list[str]] = {
    "bill": ["billno", "billnumber", "invoiceno", "invoicenumber", "orderno", "orderid", "ordernumber",
             "receiptno", "transactionid"],
    "name": ["itemname", "productname", "item", "product", "description", "service"],
    "quantity": ["quantity", "qty", "totalqty", "units"],
    "unit_price": ["unitprice", "price", "rate", "saleprice", "sellingprice", "mrp"],
    "total": ["linetotal", "lineamount", "subtotal", "netamount", "amount", "total"],
    "sku": ["sku", "itemcode", "productcode", "barcode", "itemid", "productid"],
    "category": ["category", "itemcategory", "group", "department"],
}  # fmt: skip


def guess_line_mapping(headers: Iterable[str]) -> dict[str, str]:
    """``{field: column}`` for an item file, by exact (normalized) header match."""
    norm = {normalize_header(h): h for h in headers}
    out: dict[str, str] = {}
    for fld, synonyms in LINE_SYNONYMS.items():
        for syn in synonyms:
            col = norm.get(syn)
            if col and col not in out.values():
                out[fld] = col
                break
    return out


def read_line_items(table: Table, mapping: Mapping[str, str] | None = None) -> dict[str, list[LineItem]]:
    """Group an item file into ``{bill number: [LineItem, ...]}``."""
    m = dict(mapping or guess_line_mapping(table.headers))
    if "bill" not in m or "name" not in m:
        raise ValueError("an item file needs a bill number column and an item name column")
    used = set(m.values())
    out: dict[str, list[LineItem]] = {}
    for row in table.rows:
        bill = clean_text(row.get(m["bill"]))
        name = clean_text(row.get(m["name"]))
        if not bill or not name:
            continue
        qty = parse_money(row.get(m["quantity"])) if "quantity" in m else None
        unit = parse_money(row.get(m["unit_price"])) if "unit_price" in m else None
        total = parse_money(row.get(m["total"])) if "total" in m else None
        quantity = qty if qty is not None else Decimal(1)
        if total is None and unit is not None:
            total = unit * quantity
        out.setdefault(bill, []).append(
            LineItem(
                name=name,
                quantity=quantity,
                unit_price=unit,
                total=total,
                sku=clean_text(row.get(m["sku"])) or None if "sku" in m else None,
                category=clean_text(row.get(m["category"])) or None if "category" in m else None,
                attributes={k: clean_text(v) for k, v in row.items() if k not in used and clean_text(v)},
            )
        )
    return out


def attach_line_items(
    transactions: Iterable[RawTransaction], lines: Mapping[str, list[LineItem]]
) -> list[str]:  # fmt: skip
    """Set each bill's ``line_items`` from ``lines``; return bill numbers that matched no bill."""
    matched: set[str] = set()
    for tx in transactions:
        items = lines.get(tx.external_id)
        if items is not None:
            tx.line_items = list(items)
            matched.add(tx.external_id)
    return [bill for bill in lines if bill not in matched]
