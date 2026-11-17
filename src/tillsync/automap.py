"""Guess which column holds which field, from the header row and a sample of values.

Every guess comes with a confidence between 0 and 1 and human-readable reasons, so a person can
review the result and override any field before importing::

    guess = automap(headers, rows)
    print(guess.explain())
    guess = guess.override("transaction", amount="Grand Total")
    mapping = guess.mapping()   # {"customer": {...}, "transaction": {...}}

Scoring has two parts. The header is compared with known synonyms after normalizing both
(lower case, letters and digits only): an exact match scores 1.0, a containment scores less the
more the lengths differ. Then the sample values are checked against what the field should hold
(emails look like emails, amounts parse as money, dates parse as dates...). A column with a
matching header but contradicting values loses confidence; a column whose header says nothing but
whose values are unmistakable (every value is an email) can still be picked, at lower confidence.
Each field takes its best column and each column serves one field.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .money import parse_money
from .names import clean_person_name, clean_text, reads_like_name
from .phone import clean_phone, digits, is_masked_phone
from .times import guess_dayfirst, parse_datetime

__all__ = ["FieldGuess", "AutoMapping", "automap", "normalize_header", "REQUIRED", "SYNONYMS"]

# field -> header synonyms (already normalized), most specific first
SYNONYMS: dict[str, dict[str, list[str]]] = {
    "customer": {
        "external_id": [
            "customerid",
            "custid",
            "customercode",
            "custcode",
            "customerno",
            "clientid",
            "memberid",
            "membershipid",
            "membershipno",
            "loyaltyid",
            "cardno",
            "partycode",
            "accountid",
        ],
        "name": [
            "customername",
            "fullname",
            "clientname",
            "contactname",
            "membername",
            "partyname",
            "guestname",
            "name",
            "customer",
            "client",
            "party",
            "guest",
        ],
        "phone": [
            "phonenumber",
            "mobilenumber",
            "mobileno",
            "phoneno",
            "contactnumber",
            "contactno",
            "whatsappnumber",
            "customerphone",
            "customermobile",
            "cellphone",
            "telephone",
            "mobile",
            "phone",
            "whatsapp",
            "cell",
            "tel",
            "contact",
        ],
        "email": ["emailaddress", "emailid", "email", "mail"],
        "first_seen_at": [
            "createdat",
            "createdon",
            "createddate",
            "customersince",
            "membersince",
            "joindate",
            "joinedon",
            "signupdate",
            "registrationdate",
            "firstvisit",
            "dateadded",
        ],
    },
    "transaction": {
        "external_id": [
            "invoicenumber",
            "invoiceno",
            "billnumber",
            "billno",
            "ordernumber",
            "orderno",
            "orderid",
            "receiptno",
            "receiptnumber",
            "transactionid",
            "txnid",
            "voucherno",
            "ticketno",
            "checkno",
            "documentno",
            "refno",
            "billid",
            "invoiceid",
            "invoice",
            "bill",
        ],
        "occurred_at": [
            "invoicedate",
            "billdate",
            "orderdate",
            "transactiondate",
            "txndate",
            "saledate",
            "visitdate",
            "paymentdate",
            "datetime",
            "timestamp",
            "createdat",
            "createdon",
            "closedat",
            "date",
        ],
        "occurred_time": ["billtime", "ordertime", "invoicetime", "transactiontime", "time"],
        "amount": [
            "netamount",
            "grandtotal",
            "nettotal",
            "billamount",
            "invoiceamount",
            "totalamount",
            "amountpaid",
            "paidamount",
            "ordervalue",
            "billvalue",
            "saleamount",
            "total",
            "amount",
            "value",
        ],
        "store_id": [
            "storeid",
            "outletid",
            "branchid",
            "locationid",
            "storecode",
            "outletcode",
            "store",
            "outlet",
            "branch",
            "location",
            "shop",
        ],
        "store_name": ["storename", "outletname", "branchname", "locationname", "shopname"],
        "channel": ["channel", "ordertype", "saletype", "ordersource", "source", "platform", "mode"],
        "status": ["billstatus", "orderstatus", "invoicestatus", "paymentstatus", "status"],
        "doc_type": ["documenttype", "vouchertype", "doctype", "transactiontype", "billtype", "document"],
        "currency": ["currency", "currencycode"],
    },
}

# columns a transaction can use to point at its customer, strongest identity first
LINK_KINDS = ("external_id", "phone", "email", "name")

REQUIRED = {"customer": ["external_id"], "transaction": ["occurred_at"]}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_CODE = re.compile(r"^[A-Za-z]{0,6}[-_/#]?\d[\w\-/#]*$")
_MIN_CONFIDENCE = 0.45
_VALUE_ONLY_FIELDS = {"email", "phone", "occurred_at", "amount"}
_CURRENCY_HEADER = re.compile(r"\(\s*(?:inr|rs\.?|usd|eur|gbp|[₹$€£])\s*\)", re.IGNORECASE)


def normalize_header(header: str) -> str:
    """Lower case, letters and digits only; a currency tag such as ``(INR)`` is dropped."""
    return "".join(ch for ch in _CURRENCY_HEADER.sub("", header or "").lower() if ch.isalnum())


def _header_score(norm: str, synonyms: Sequence[str]) -> tuple[float, str]:
    best, why = 0.0, ""
    for rank, syn in enumerate(synonyms):
        tiebreak = rank * 0.001  # earlier synonyms win ties
        if norm == syn:
            score = 1.0 - tiebreak
        elif len(syn) >= 3 and (syn in norm or (len(norm) >= 3 and norm in syn)):
            lo, hi = sorted((len(norm), len(syn)))
            score = 0.9 * lo / hi - tiebreak
        else:
            continue
        if score > best:
            best, why = score, syn
    return best, why


def _frac(values: Sequence[str], test: Callable[[str], bool]) -> float:
    return sum(1 for v in values if test(v)) / len(values) if values else 0.0


def _looks_phone(v: str) -> bool:
    if is_masked_phone(v):
        return True
    c = clean_phone(v)
    return bool(c) and not re.search(r"[A-Za-z@]", c) and 7 <= len(digits(c)) <= 15


def _looks_date(v: str) -> bool:
    return parse_datetime(v) is not None and not re.fullmatch(r"\d{1,7}", v.strip())


def _looks_time(v: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm])?", v.strip()))


def _looks_code(v: str) -> bool:
    return bool(_CODE.match(v.strip())) or v.strip().isdigit()


def _value_check(section: str, fld: str) -> tuple[Callable[[str], bool], str] | None:
    """(test, what it means) for the values a field should hold; None when anything goes."""
    checks: dict[str, tuple[Callable[[str], bool], str]] = {
        "email": (lambda v: bool(_EMAIL.match(v.strip())), "look like email addresses"),
        "phone": (_looks_phone, "look like phone numbers"),
        "name": (lambda v: bool(clean_person_name(v)) or not v.strip(), "read like names"),
        "first_seen_at": (_looks_date, "parse as dates"),
        "occurred_at": (_looks_date, "parse as dates"),
        "occurred_time": (_looks_time, "look like times of day"),
        "amount": (lambda v: parse_money(v) is not None, "parse as money"),
    }
    if fld == "external_id":
        return (lambda v: not _EMAIL.match(v.strip()), "are not email addresses")
    return checks.get(fld)


@dataclass(frozen=True)
class FieldGuess:
    """One field's chosen column, how sure we are, and why."""

    field: str
    column: str
    confidence: float
    reasons: tuple[str, ...] = ()
    link_kind: str | None = None  # for a transaction's customer link: external_id, phone, email or name


@dataclass(frozen=True)
class AutoMapping:
    """The result of :func:`automap`. Immutable; :meth:`override` returns a new one."""

    customer: dict[str, FieldGuess] = field(default_factory=dict)
    transaction: dict[str, FieldGuess] = field(default_factory=dict)
    headers: tuple[str, ...] = ()
    dayfirst: bool | None = None
    customer_headers: tuple[str, ...] | None = None  # set when customers come from their own file

    def section(self, name: str) -> dict[str, FieldGuess]:
        if name not in ("customer", "transaction"):
            raise ValueError(f"unknown section {name!r}; use 'customer' or 'transaction'")
        sec: dict[str, FieldGuess] = getattr(self, name)
        return sec

    def mapping(self) -> dict[str, dict[str, str]]:
        """Plain ``{"customer": {field: column}, "transaction": {field: column}}``."""
        return {
            "customer": {f: g.column for f, g in self.customer.items()},
            "transaction": {f: g.column for f, g in self.transaction.items()},
        }

    def _columns(self, section: str) -> tuple[str, ...]:
        if section == "customer" and self.customer_headers is not None:
            return self.customer_headers
        return self.headers

    @property
    def unmapped(self) -> list[str]:
        used = {g.column for g in self.customer.values()} | {g.column for g in self.transaction.values()}
        cols = list(self.headers) + [h for h in self.customer_headers or () if h not in self.headers]
        return [h for h in cols if h not in used]

    def missing(self) -> dict[str, list[str]]:
        """Required fields that have no column yet, per section."""
        out: dict[str, list[str]] = {}
        if self.customer_headers is not None:
            gap = [f for f in REQUIRED["customer"] if f not in self.customer]
            if gap:
                out["customer"] = gap
        if self.headers:
            gap = [f for f in REQUIRED["transaction"] if f not in self.transaction]
            if gap:
                out["transaction"] = gap
        return out

    def override(self, section: str, **columns: str | None) -> AutoMapping:
        """Set (or with None, remove) fields by hand. Overrides get confidence 1.0."""
        current = dict(self.section(section))
        for fld, col in columns.items():
            if col is None:
                current.pop(fld, None)
                continue
            cols = self._columns(section)
            if cols and col not in cols:
                raise KeyError(f"column {col!r} is not in the file; columns are {list(cols)}")
            kind = current[fld].link_kind if fld in current else None
            current[fld] = FieldGuess(fld, col, 1.0, ("set by hand",), kind)
        if section == "customer":
            return replace(self, customer=current)
        return replace(self, transaction=current)

    def explain(self) -> str:
        """A readable summary: each field, its column, confidence and reasons."""
        lines: list[str] = []
        for sec in ("customer", "transaction"):
            guesses = self.section(sec)
            lines.append(f"{sec}:")
            if not guesses:
                lines.append("  (nothing mapped)")
            for f, g in guesses.items():
                why = "; ".join(g.reasons)
                lines.append(f"  {f:<14} <- {g.column!r:<24} {g.confidence:4.0%}  {why}")
        if self.unmapped:
            lines.append("kept as attributes: " + ", ".join(self.unmapped))
        if self.dayfirst is not None:
            lines.append("dates read as " + ("day/month/year" if self.dayfirst else "month/day/year"))
        miss = self.missing()
        if miss:
            lines.append("missing: " + "; ".join(f"{s}: {', '.join(f)}" for s, f in miss.items()))
        return "\n".join(lines)


def _column_values(
    rows: Iterable[Mapping[str, Any]], headers: Sequence[str], limit: int
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {h: [] for h in headers}
    for i, row in enumerate(rows):
        if i >= limit:
            break
        for h in headers:
            v = clean_text(row.get(h))
            if v:
                values[h].append(v)
    return values


def _candidates(
    section: str, headers: Sequence[str], values: Mapping[str, list[str]], skip: Iterable[str] = ()
) -> list[tuple[float, str, str, tuple[str, ...]]]:
    out: list[tuple[float, str, str, tuple[str, ...]]] = []
    for fld, synonyms in SYNONYMS[section].items():
        if fld in skip:
            continue
        check = _value_check(section, fld)
        for h in headers:
            norm = normalize_header(h)
            hscore, syn = _header_score(norm, synonyms)
            sample = values.get(h, [])
            reasons: list[str] = []
            frac = _frac(sample, check[0]) if check and sample else None
            label = check[1] if check else ""
            if hscore > 0:
                kind = "exactly" if norm == syn else "partly"
                reasons.append(f'header matches "{syn}" {kind}')
                score = hscore
                if frac is not None:
                    score = hscore * (0.4 + 0.6 * frac)
                    reasons.append(f"{frac:.0%} of {len(sample)} sample values {label}")
            elif fld in _VALUE_ONLY_FIELDS and frac is not None and frac >= 0.9 and len(sample) >= 3:
                score = 0.5 * frac
                reasons.append(f"header says nothing, but {frac:.0%} of {len(sample)} values {label}")
            else:
                continue
            out.append((round(score, 3), fld, h, tuple(reasons)))
    return out


def _assign(
    candidates: list[tuple[float, str, str, tuple[str, ...]]], taken: set[str]
) -> dict[str, FieldGuess]:
    chosen: dict[str, FieldGuess] = {}
    for score, fld, col, reasons in sorted(candidates, key=lambda c: -c[0]):
        if score < _MIN_CONFIDENCE or fld in chosen or col in taken:
            continue
        chosen[fld] = FieldGuess(fld, col, score, reasons)
        taken.add(col)
    return chosen


def _prefer_store_names(txn: dict[str, FieldGuess], values: Mapping[str, list[str]]) -> None:
    """An outlet column full of names ("Main Street") is a store name, not an id."""
    g = txn.get("store_id")
    if g is None or "store_name" in txn:
        return
    sample = values.get(g.column, [])
    if sample and sum(reads_like_name(v) for v in sample) * 2 > len(sample):
        del txn["store_id"]
        txn["store_name"] = replace(g, field="store_name", reasons=(*g.reasons, "values read like names"))


def automap(
    headers: Sequence[str],
    rows: Iterable[Mapping[str, Any]] = (),
    *,
    sample_size: int = 200,
    customer_headers: Sequence[str] | None = None,
    customer_rows: Iterable[Mapping[str, Any]] = (),
) -> AutoMapping:
    """Guess a mapping for a sales file (``headers``/``rows``).

    When customers come from a separate file, pass its ``customer_headers``/``customer_rows``;
    otherwise the customer fields are read from the sales file itself.
    """
    headers = [h for h in headers if h and str(h).strip()]
    values = _column_values(rows, headers, sample_size)
    separate = customer_headers is not None
    c_headers = [h for h in (customer_headers or []) if h and str(h).strip()] if separate else headers
    c_values = _column_values(customer_rows, c_headers, sample_size) if separate else values

    taken: set[str] = set()
    # in a single sales file, a "created at" column is the bill's time, not the customer's
    skip = () if separate else ("first_seen_at",)
    customer = _assign(_candidates("customer", c_headers, c_values, skip), set() if separate else taken)
    taken = set() if separate else {g.column for g in customer.values()}
    transaction: dict[str, FieldGuess] = {}
    if headers:
        # in a single file the bill's id must not be the customer's id column
        transaction = _assign(_candidates("transaction", headers, values), set(taken))
        _prefer_store_names(transaction, values)

    # the customer's key: its own id column, else the strongest identity present
    for kind in LINK_KINDS:
        if kind in customer:
            g = customer[kind]
            if kind != "external_id":
                customer["external_id"] = FieldGuess(
                    "external_id",
                    g.column,
                    g.confidence,
                    (*g.reasons, f"used as the customer key ({kind})"),
                    kind,
                )
            else:
                customer["external_id"] = replace(g, link_kind="external_id")
            break

    # how each bill points at its customer
    if headers and customer:
        if not separate:
            key = customer.get("external_id")
            if key is not None:
                transaction["customer_ref"] = FieldGuess(
                    "customer_ref",
                    key.column,
                    key.confidence,
                    ("same column as the customer key",),
                    key.link_kind,
                )
        else:
            link = _link_in_sales(headers, values, customer)
            if link is not None:
                transaction["customer_ref"] = link

    dayfirst = None
    date_col = transaction.get("occurred_at")
    if date_col is not None:
        dayfirst = guess_dayfirst(values.get(date_col.column, []))
    return AutoMapping(
        customer, transaction, tuple(headers), dayfirst, tuple(c_headers) if separate else None
    )


def _link_in_sales(
    headers: Sequence[str], values: Mapping[str, list[str]], customer: Mapping[str, FieldGuess]
) -> FieldGuess | None:
    """In a separate sales file, the column that names the customer, preferring the same kind of key."""
    found: dict[str, FieldGuess] = {}
    for kind in LINK_KINDS:
        cands = [c for c in _candidates("customer", headers, values) if c[1] == kind]
        if kind == "external_id":
            cands = [
                c for c in cands if any(_looks_code(v) for v in values.get(c[2], [])[:20]) or c[0] >= 0.99
            ]
        best = _assign(cands, set())
        if kind in best:
            found[kind] = best[kind]
    key_kind = customer["external_id"].link_kind if "external_id" in customer else None
    order = [key_kind] if key_kind else []
    order += [k for k in LINK_KINDS if k != key_kind]
    for kind in order:
        if kind in found:
            g = found[kind]
            note = "matches the customer key" if kind == key_kind else f"links by {kind}"
            return FieldGuess("customer_ref", g.column, g.confidence, (*g.reasons, note), kind)
    return None
