from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tillsync import clean_person_name, detect_currency, parse_money, reads_like_name
from tillsync.names import clean_text, is_placeholder
from tillsync.phone import clean_phone, default_country, is_masked_phone, normalize_phone, phone_key
from tillsync.times import format_since, guess_dayfirst, has_time_of_day, parse_datetime, resolve_tz


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("₹1,20,000", Decimal("120000")),
        ("Rs. 250", Decimal("250")),
        ("INR 250.00", Decimal("250.00")),
        ("1,250/-", Decimal("1250")),
        ("$ 15.50", Decimal("15.50")),
        ("€1.234,56", Decimal("1234.56")),
        ("1 234,56", Decimal("1234.56")),
        ("120,000", Decimal("120000")),
        ("-250", Decimal("-250")),
        ("(250)", Decimal("-250")),
        ("250-", Decimal("-250")),
        ("- Rs 250", Decimal("-250")),
        ("-", Decimal("0")),
        ("£ 9.99", Decimal("9.99")),
        (3.5, Decimal("3.5")),
        (7, Decimal("7")),
        (Decimal("2.50"), Decimal("2.50")),
    ],
)
def test_parse_money_reads_common_formats(raw: object, expected: Decimal) -> None:
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "abc", "12 34", "two hundred", True, float("nan"), "250 CR"])
def test_parse_money_unreadable_is_none_never_zero(raw: object) -> None:
    assert parse_money(raw) is None


def test_detect_currency() -> None:
    assert detect_currency("₹ 250") == "INR"
    assert detect_currency("Rs. 250") == "INR"
    assert detect_currency("USD 10") == "USD"
    assert detect_currency("€5") == "EUR"
    assert detect_currency("Total (GBP)") == "GBP"
    assert detect_currency("250") is None


@pytest.mark.parametrize(
    ("raw", "cc", "expected"),
    [
        ("+91 99999 00001", "1", "+919999900001"),
        ("09999900001", "91", "+919999900001"),
        ("9999900001", "91", "+919999900001"),
        ("919999900001", "91", "+919999900001"),
        ("0044 20 7946 0000", "91", "+442079460000"),
        ("+1 (202) 555-0101", None, "+12025550101"),
        ("202-555-0101", "1", "+12025550101"),
        ("9*999900001", "91", "+919999900001"),
        ("99******02", "91", None),
        ("abcdefghij", "91", None),
        ("123", "91", None),
        ("", "91", None),
        ("000", "91", None),
        ("+", "91", None),
    ],
)
def test_normalize_phone(raw: str, cc: str | None, expected: str | None) -> None:
    assert normalize_phone(raw, cc) == expected


def test_default_country_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TILLSYNC_DEFAULT_COUNTRY", raising=False)
    assert default_country() == "1"
    monkeypatch.setenv("TILLSYNC_DEFAULT_COUNTRY", "+44")
    assert default_country() == "44"
    assert normalize_phone("07946 000000") == "+447946000000"


def test_phone_key_and_masks() -> None:
    assert phone_key("+1 (202) 555-0101") == phone_key("202 555 0101") == "2025550101"
    assert phone_key("12") is None
    assert is_masked_phone("XXXXXX3210")
    assert is_masked_phone("98******10")
    assert not is_masked_phone("9*999900001")
    assert not is_masked_phone("9999900001")
    assert clean_phone("XXXXXX3210") == ""
    assert clean_phone("+9*1999990000*1") == "+919999900001"
    assert clean_phone("x9y") == ""


def test_names() -> None:
    assert clean_person_name("  Asha   Rao ") == "Asha Rao"
    for junk in ("Guest", "walk-in", "A", "10", "N/A", "-", "Cash", "", None, "Mr."):
        assert clean_person_name(junk) == "", junk
        assert is_placeholder(junk)
    assert clean_text("—") == ""
    assert reads_like_name("Main Street")
    assert reads_like_name("Downtown")
    assert not reads_like_name("ST-004")
    assert not reads_like_name("S1")
    assert not reads_like_name("")


IST = timezone(timedelta(hours=5, minutes=30))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-03T09:15:00+05:30", datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)),
        ("2026-01-03T09:15:00Z", datetime(2026, 1, 3, 9, 15, tzinfo=timezone.utc)),
        ("2026-01-03T09:15:00+0530", datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)),
        ("2026-01-03 09:15", datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)),
        ("03/01/2026 09:15 AM", datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)),
        ("03-Jan-2026 9:15 pm", datetime(2026, 1, 3, 15, 45, tzinfo=timezone.utc)),
        ("03/01/2026", datetime(2026, 1, 2, 18, 30, tzinfo=timezone.utc)),
        ("20260103", datetime(2026, 1, 2, 18, 30, tzinfo=timezone.utc)),
        ("1767600000", datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)),
        ("1767600000000", datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)),
        (1767600000, datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)),
        (datetime(2026, 1, 3, 9, 15), datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)),
        (datetime(2026, 1, 3, 9, 15, tzinfo=IST), datetime(2026, 1, 3, 3, 45, tzinfo=timezone.utc)),
        (date(2026, 1, 3), datetime(2026, 1, 2, 18, 30, tzinfo=timezone.utc)),
    ],
)
def test_parse_datetime_is_timezone_correct(raw: object, expected: datetime) -> None:
    assert parse_datetime(raw, tz="Asia/Kolkata") == expected


@pytest.mark.parametrize("raw", ["not a date", "31/02/2026", "", None, True, "20261399", "99999999999999999"])
def test_parse_datetime_bad_values(raw: object) -> None:
    assert parse_datetime(raw) is None


def test_dayfirst_and_custom_formats() -> None:
    assert parse_datetime("03/04/2026") == datetime(2026, 4, 3, tzinfo=timezone.utc)
    assert parse_datetime("03/04/2026", dayfirst=False) == datetime(2026, 3, 4, tzinfo=timezone.utc)
    assert parse_datetime("2026|01|05", formats=["%Y|%m|%d"]) == datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert guess_dayfirst(["13/02/2026", "01/02/2026"]) is True
    assert guess_dayfirst(["02/13/2026"]) is False
    assert guess_dayfirst(["01/02/2026", "x"]) is None


def test_time_helpers() -> None:
    assert has_time_of_day("2026-01-01 10:00")
    assert not has_time_of_day("2026-01-01")
    assert has_time_of_day(datetime(2026, 1, 1))
    assert not has_time_of_day(date(2026, 1, 1))
    assert has_time_of_day(1767600000)
    assert resolve_tz(None) is timezone.utc
    assert resolve_tz(IST) is IST
    when = datetime(2026, 1, 5, 8, 0, 30, tzinfo=timezone.utc)
    assert format_since(when) == "2026-01-05T08:00:30+00:00"
    assert format_since(when, "iso_z") == "2026-01-05T08:00:30Z"
    assert format_since(when, "epoch") == "1767600030"
    assert format_since(when, "epoch_ms") == "1767600030000"
    assert format_since(when, "%Y-%m-%d") == "2026-01-05"
