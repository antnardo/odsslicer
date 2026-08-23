# -*- coding: utf-8 -*-
import shutil
import subprocess
import sys
from pathlib import Path

# Make the `odsslicer` package importable when running `pytest` straight from a
# checkout, without an editable install. This file lives at <repo>/tests/conftest.py
# -> parents[1] is the repo root, and the package lives at <repo>/src/odsslicer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from odsslicer import ODSReader

FIXTURES_DIR = Path(__file__).resolve().parent

# A real, local LibreOffice install used for consistency checks (see
# test_libreoffice_consistency.py) - the strongest available signal that a
# file odsslicer wrote is genuinely valid ODF, not just something our own
# (comparatively lenient) BeautifulSoup-based reader happens to parse back.
# Not installed in CI by default, so tests using it skip automatically.
SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")

requires_soffice = pytest.mark.skipif(
    SOFFICE is None, reason="LibreOffice CLI (soffice/libreoffice) not found on PATH"
)


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


def convert_with_libreoffice(src_path, fmt, outdir):
    """Convert `src_path` to `fmt` via `soffice --headless --convert-to` -
    a real LibreOffice actually opening and re-exporting the file, not
    just odsslicer reading its own output back. Raises if the conversion
    fails or produces nothing (a strong "this file is invalid ODF"
    signal); returns the produced file's path otherwise.

    `fmt="fods"` (Flat ODF, plain readable XML) is the most useful target
    for inspecting *what* survived - values, formulas (LibreOffice
    recomputes them on export), styles, merges - since it's a single
    human-diffable file rather than another zip. Note LibreOffice quietly
    rounds some measurements to its own internal precision on export
    (e.g. `0.5pt` -> `0.51pt`, `5cm` -> `5.001cm`) - assert on presence/
    prefix rather than exact numeric strings.
    """
    result = subprocess.run(
        [SOFFICE, "--headless", "--convert-to", fmt, "--outdir", str(outdir), str(src_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"soffice --convert-to {fmt} failed:\n{result.stdout}\n{result.stderr}")
    out_path = Path(outdir) / f"{Path(src_path).stem}.{fmt}"
    if not out_path.exists():
        raise RuntimeError(f"soffice did not produce {out_path}:\n{result.stdout}\n{result.stderr}")
    return out_path


@pytest.fixture()
def libreoffice_export(tmp_path):
    def _export(ods_path, fmt="fods"):
        return convert_with_libreoffice(ods_path, fmt, tmp_path)

    return _export
