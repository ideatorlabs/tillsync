from __future__ import annotations

import json
from pathlib import Path

import pytest

from tillsync.cli import main


def test_preview_table(examples_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "preview",
            str(examples_dir / "sample_sales.csv"),
            "--tz",
            "Asia/Kolkata",
            "--country",
            "91",
            "--limit",
            "3",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Column mapping" in out and "Net Amount" in out
    assert "First 3 of 7 transactions" in out and "B-1001" in out and "+919999900001" in out
    assert "Left out:" in out and "bad_date=1" in out and "hidden_phone=1" in out


def test_preview_json_and_mock(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preview", "mock:salon", "--json", "--limit", "2"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mapping"] is None and len(data["customers"]) == 2 and len(data["transactions"]) == 2


def test_preview_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preview", str(tmp_path / "missing.csv")]) == 2
    assert "error" in capsys.readouterr().err
    assert main(["preview", "http://127.0.0.1:1/sheet.csv"]) == 2
    assert "not on the public internet" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--version"])
