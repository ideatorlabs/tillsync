"""Reading CSV, TSV and Excel files into a header list and row dicts.

Exports are messy: a title line above the header, a byte-order mark, semicolons instead of
commas, blank or repeated column names. ``parse_csv_text`` and ``read_table`` deal with
those so connectors only see clean ``{header: value}`` rows.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Table", "parse_csv_text", "parse_rows", "read_table", "read_excel"]

_DELIMITERS = ",;\t|"
_EXCEL_SUFFIXES = (".xlsx", ".xlsm")


@dataclass
class Table:
    headers: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    header_row: int = 1  # 1-based line number of the header, for error messages

    def row_number(self, index: int) -> int:
        """The 1-based line of ``rows[index]`` in the original file."""
        return self.header_row + 1 + index


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in _DELIMITERS}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


def _find_header(grid: list[list[Any]]) -> int:
    """Index of the header row: the first row about as wide as the widest of the first few rows."""
    head = grid[:15]
    widths = [sum(1 for c in row if str(c if c is not None else "").strip()) for row in head]
    widest = max(widths, default=0)
    for i, w in enumerate(widths):
        if w >= 2 and w >= 0.6 * widest:
            return i
    return 0


def _unique_headers(raw: list[Any]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, h in enumerate(raw):
        name = " ".join(str(h if h is not None else "").split()) or f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        out.append(name)
    return out


def parse_rows(grid: list[list[Any]]) -> Table:
    """Turn a list of rows (first meaningful one is the header) into a ``Table``."""
    grid = [list(r) for r in grid]
    if not grid:
        return Table(headers=[])
    h = _find_header(grid)
    headers = _unique_headers(grid[h])
    rows: list[dict[str, Any]] = []
    for raw in grid[h + 1 :]:
        if not any(str(c if c is not None else "").strip() for c in raw):
            rows.append({})  # keep blank lines so row numbers stay true; connectors skip them
            continue
        row = {headers[i]: raw[i] if i < len(raw) else None for i in range(len(headers))}
        rows.append(row)
    while rows and not rows[-1]:
        rows.pop()
    return Table(headers=headers, rows=rows, header_row=h + 1)


def parse_csv_text(text: str, delimiter: str | None = None) -> Table:
    """Parse CSV/TSV text. The delimiter is sniffed when not given."""
    text = text.lstrip("﻿")
    if not text.strip():
        return Table(headers=[])
    delim = delimiter or _sniff_delimiter(text)
    grid = list(csv.reader(io.StringIO(text), delimiter=delim))
    return parse_rows(grid)


def read_excel(path: str | Path, sheet: str | int | None = None) -> Table:
    """Read one worksheet of an ``.xlsx`` file. Needs the ``excel`` extra (openpyxl)."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("reading Excel files needs openpyxl: pip install 'tillsync[excel]'") from exc
    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        if sheet is None:
            ws = wb.worksheets[0]
        elif isinstance(sheet, int):
            ws = wb.worksheets[sheet]
        else:
            ws = wb[sheet]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return parse_rows(grid)


def read_table(path: str | Path, *, delimiter: str | None = None, sheet: str | int | None = None) -> Table:
    """Read a ``.csv``, ``.tsv``/``.txt`` or ``.xlsx`` file."""
    p = Path(path)
    if p.suffix.lower() in _EXCEL_SUFFIXES:
        return read_excel(p, sheet)
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if delimiter is None and p.suffix.lower() == ".tsv":
        delimiter = "\t"
    return parse_csv_text(text, delimiter)
