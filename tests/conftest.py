# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# Make the `odsslicer` package importable when running `pytest` straight from a
# checkout, without an editable install. This file lives at <repo>/tests/conftest.py
# -> parents[1] is the repo root, and the package lives at <repo>/src/odsslicer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from odsslicer import ODSReader

FIXTURES_DIR = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def test_ods_path():
    return FIXTURES_DIR / "TEST.ods"


@pytest.fixture(scope="session")
def reader(test_ods_path):
    return ODSReader(test_ods_path)


@pytest.fixture(scope="session")
def sheet1(reader):
    return reader.sheet("Sheet1")


@pytest.fixture(scope="session")
def sheet_repeat(reader):
    return reader.sheet("Sheet2Repeat")


@pytest.fixture(scope="session")
def sheet_empty(reader):
    return reader.sheet("SheetEmpty")


@pytest.fixture(scope="session")
def sheet_fusion(reader):
    return reader.sheet("SheetFusion")


@pytest.fixture()
def writable_reader(test_ods_path):
    # function-scoped: writes must never leak into the session-scoped
    # `reader`/`sheet1`/... fixtures used by the read-only tests.
    return ODSReader(test_ods_path)
