from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
sys.path.insert(0, str(EXAMPLES))


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def root_dir() -> Path:
    return ROOT
