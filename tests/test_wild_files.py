# -*- coding: utf-8 -*-
"""Confront the API with real-world "wild" .ods files written by other
generators (Excel 16, LibreOffice 3.5 from 2012, recent LibreOffice on Linux
and Windows) - see tests/wild/README.md for provenance and licenses.

These files exercise what our own writer never produces: grid-filler rows and
cells declaring the sheet's full 16,384 x 1,048,576 extent, a missing
`settings.xml` (Excel ships none - it is optional in ODF), ragged row widths,
decade-old ODF dialects. The expected sheet sizes below are exact on purpose:
they pin down the filler-clamping behaviour of `Sheet.load`.
"""

import pytest
from conftest import FIXTURES_DIR, requires_soffice

from odsslicer import ODSReader

WILD_DIR = FIXTURES_DIR / "wild"

# filename -> (expected sheet sizes by name, spot checks [(sheet, (row, col), value)])
EXPECTED = {
    "excel16_uk_stats_2026.ods": (
        {
            "Cover_sheet": (10, 2),
            "Table_of_contents": (6, 5),
            "Notes_and_definitions": (17, 3),
            "OIC_01": (27, 10),
        },
        [
            ("OIC_01", (6, 0), "2023"),
            ("OIC_01", (6, 2), 259.0),
            ("OIC_01", (9, 3), 47.0),
        ],
    ),
    "excel16_uk_stats_2020.ods": (
        {
            "Cover_sheet": (21, 3),
            "Contents": (14, 6),
            "Notes": (13, 8),
            "S67_01": (22, 2),
            "S67_02": (13, 2),
            "S67_03": (18, 8),
            "S67_04": (17, 2),
            "S67_05": (48, 2),
        },
        [
            ("S67_05", (4, 0), "Hammersmith and Fulham"),
            ("S67_05", (4, 1), 23.0),
        ],
    ),
    "libreoffice35_casinos_2015.ods": (
        {"CASINOS AUTORISES": (266, 2)},
        [
            ("CASINOS AUTORISES", (0, 0), "DEPARTEMENT"),
            ("CASINOS AUTORISES", (2, 0), "01 - AIN"),
            ("CASINOS AUTORISES", (265, 1), "SAINT-PIERRE"),
        ],
    ),
    "libreoffice26_linux_streets.ods": (
        {"Feuille1": (277, 5)},
        [
            ("Feuille1", (0, 0), "Voies"),
            ("Feuille1", (1, 0), "Allée de Spa"),
            ("Feuille1", (4, 2), "P BRABOIS"),
        ],
    ),
    "libreoffice26_windows_procurement.ods": (
        {"DECP - Lots": (17, 21)},
        [
            ("DECP - Lots", (4, 1), "VRD – espaces verts"),
            ("DECP - Lots", (4, 2), "2026-ERL-LOT-01"),
        ],
    ),
}

WILD_FILES = sorted(EXPECTED)


def test_every_wild_file_is_covered():
    """A fixture dropped into tests/wild/ without an EXPECTED entry (or the
    reverse) is a mistake - fail loudly rather than silently skipping it."""
    on_disk = {p.name for p in WILD_DIR.glob("*.ods")}
    assert on_disk == set(EXPECTED)


@pytest.fixture(scope="module")
def wild_readers():
    # module-scoped: the read-only tests below share one parse per file
    return {name: ODSReader(WILD_DIR / name) for name in WILD_FILES}


@pytest.mark.parametrize("name", WILD_FILES)
def test_sheets_and_sizes(wild_readers, name):
    sizes, _ = EXPECTED[name]
    reader = wild_readers[name]
    assert reader.sheets_names == list(sizes)
    for sheet_name, size in sizes.items():
        assert reader.sheet(sheet_name).size == size


@pytest.mark.parametrize("name", WILD_FILES)
def test_spot_values(wild_readers, name):
    _, spots = EXPECTED[name]
    for sheet_name, (row, col), value in spots:
        assert wild_readers[name].sheet(sheet_name)[row, col].value == value


@pytest.mark.parametrize("name", WILD_FILES)
def test_full_read_is_rectangular(wild_readers, name):
    for sheet in wild_readers[name].sheets:
        values = sheet[:, :].to_list()
        assert len(values) == sheet.n_rows
        assert all(len(row) == sheet.n_cols for row in values)


@pytest.mark.parametrize("name", WILD_FILES)
def test_save_roundtrip_preserves_values(tmp_path, name):
    reader = ODSReader(WILD_DIR / name)
    before = {n: reader.sheet(n)[:, :].to_list() for n in reader.sheets_names}
    out = tmp_path / name
    reader.save(out)
    reopened = ODSReader(out)
    assert reopened.sheets_names == reader.sheets_names
    for n, values in before.items():
        assert reopened.sheet(n)[:, :].to_list() == values


@pytest.mark.parametrize("name", WILD_FILES)
def test_write_into_wild_file(tmp_path, name):
    """Writing must work on foreign XML too: overwrite an existing cell and
    write past the sheet's extent (auto-growth), then read both back."""
    reader = ODSReader(WILD_DIR / name)
    sheet = reader.sheets[-1]
    grown_row = sheet.n_rows + 2
    sheet[0, 0].value = "odsslicer was here"
    sheet[grown_row, 1].value = 42.5
    out = tmp_path / name
    reader.save(out)
    reopened_sheet = ODSReader(out).sheets[-1]
    assert reopened_sheet[0, 0].value == "odsslicer was here"
    assert reopened_sheet[grown_row, 1].value == 42.5


@requires_soffice
@pytest.mark.parametrize("name", WILD_FILES)
def test_libreoffice_accepts_saved_wild_file(tmp_path, libreoffice_export, name):
    """The strongest check: after odsslicer rewrites a wild file, a real
    LibreOffice must still open it and find the same data in it."""
    reader = ODSReader(WILD_DIR / name)
    sizes, spots = EXPECTED[name]
    # save into a subdir so the ods -> ods conversion cannot collide with its source
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out = src_dir / name
    reader.save(out)
    converted = libreoffice_export(out, "ods")
    reopened = ODSReader(converted)
    assert reopened.sheets_names == list(sizes)
    for sheet_name, (row, col), value in spots:
        assert reopened.sheet(sheet_name)[row, col].value == value
