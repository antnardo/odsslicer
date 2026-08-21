# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# Make the `odsslicer` package importable when running `pytest` from anywhere,
# without requiring the caller to set PYTHONPATH manually.
# This file lives at <repo>/odsslicer/tests/conftest.py -> parents[2] is the
# folder *containing* the `odsslicer` package folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
