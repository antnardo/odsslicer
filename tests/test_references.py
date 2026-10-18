"""Formulas of the whole document following the cells an edit moves."""

import pytest

from conftest import empty_cells, formula_cell, number_cell, ods_with_sheet, table_row
from odsslicer import ODSReader

# ---------------------------------------------------------------------------
# delete_row / delete_column: formula references adjusted
# ---------------------------------------------------------------------------


def test_delete_row_shifts_a_formula_reference_below_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C5"].formula = "A6+A7"  # row 4, referencing rows 5 and 6 (0-indexed)
    s.delete_row(3)  # entirely above both references - both shift up by one
    assert s["C4"].formula_friendly == "=A5+A6"


def test_delete_row_leaves_an_unrelated_reference_untouched(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "A2+A3"
    s.delete_row(7)  # far below both references - nothing to adjust
    assert s["C1"].formula_friendly == "=A2+A3"


def test_delete_row_shrinks_a_range_spanning_the_deletion(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A5"].formula_friendly == "=SUM(A2:A3)"
    s.delete_row(1)  # A2, the exact start of the range
    # the start moves to the first row left, the end (past it) shifts up
    assert s["A4"].formula_friendly == "=SUM(A2:A2)"


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # rows 5 and 6 deleted; as LibreOffice rewrites them, through UNO
        ("SUM(A5:A11)", "=SUM(A5:A9)"),  # its first rows
        ("SUM(A2:A6)", "=SUM(A2:A4)"),  # its last rows
        ("SUM(A2:A5)", "=SUM(A2:A4)"),  # its last row, and the one after
        ("SUM(A5:A6)", "=SUM(#REF!)"),  # all of it
        ("SUM(A5:C5)", "=SUM(#REF!)"),  # its only row
        ("A5", "=#REF!"),
        ("A5+A8", "=#REF!+A6"),
        ("SUM(A4:A7)", "=SUM(A4:A5)"),
        ("SUM($A$2:$A$6)", "=SUM($A$2:$A$4)"),
    ],
)
def test_delete_rows_rewrites_references_as_libreoffice_does(formula, expected):
    # regression: a deleted start or end, or a deleted cell, kept its
    # address, and pointed at the cells taking its place
    s = ODSReader.new().sheet("Sheet1")
    for i in range(12):
        s[i, 0].value = i + 1
    s["B1"].formula = formula
    s.delete_rows([4, 5])
    assert s["B1"].formula_friendly == expected


def test_a_range_written_backwards_is_no_deleted_range():
    # its end before its start is how it was written, not a deletion
    s = ODSReader.new().sheet("Sheet1")
    s["C1"].formula = "SUM(A6:A2)"
    s.insert_rows(0)
    assert s["C2"].formula_friendly == "=SUM(A7:A3)"


def test_deleting_the_last_row_of_a_range_leaves_out_the_total_below():
    # regression: SUM(A1:A6) kept its end, and summed its own cell
    s = ODSReader.new().sheet("Sheet1")
    for i in range(6):
        s[i, 0].value = i + 1
    s["A7"].formula = "SUM(A1:A6)"
    s.delete_row(5)
    assert s["A6"].formula_friendly == "=SUM(A1:A5)"


def test_delete_column_rewrites_references_as_libreoffice_does(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")  # its columns are defined
    s["A9"].formula = "SUM(B1:D1)"
    s["B9"].formula = "D2+C2"
    s.delete_column(3)  # D, the last of the range
    assert s["A9"].formula_friendly == "=SUM(B1:C1)"
    assert s["B9"].formula_friendly == "=#REF!+C2"


def test_delete_column_shifts_a_formula_reference_right_of_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "x"
    s["D1"].formula = "C1"
    s.delete_column(1)  # column B, left of both C1 and D1
    assert s["C1"].formula_friendly == "=B1"


def test_delete_row_adjusts_a_cross_sheet_reference(writable_reader):
    r = writable_reader
    s1 = r.sheet("Sheet1")
    s2 = r.sheet("Sheet2Repeat")
    s2["A1"].formula = "Sheet1.A6+Sheet1.A7"
    s1.delete_row(3)  # above both A6 and A7 - both shift up by one
    assert s2["A1"].formula_friendly == "=Sheet1.A5+Sheet1.A6"


def test_delete_row_does_not_touch_another_sheets_own_reference(writable_reader):
    # Sheet2Repeat's own (unqualified) formula refers to ITS OWN sheet -
    # deleting a row from Sheet1 must not touch it
    r = writable_reader
    s1 = r.sheet("Sheet1")
    s2 = r.sheet("Sheet2Repeat")
    s2["B1"].formula = "A6+A7"
    s1.delete_row(3)
    assert s2["B1"].formula_friendly == "=A6+A7"


def test_delete_row_does_not_touch_a_reference_to_a_third_sheet(writable_reader):
    # deleting a row from Sheet2Repeat must not touch a formula (wherever
    # it lives) that references a completely unrelated third sheet
    r = writable_reader
    s1 = r.sheet("Sheet1")
    s2 = r.sheet("Sheet2Repeat")
    s1["C1"].formula = "SheetFusion.A1"
    s2.delete_row(0)
    assert s1["C1"].formula_friendly == "=SheetFusion.A1"


def test_save_round_trip_after_delete_row_adjusts_formulas(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["C5"].formula = "A6+A7"
    s.delete_row(3)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["C4"].formula_friendly == "=A5+A6"


_LIBREOFFICE_REFERENCES = ["of:=[$Data.A5]", "of:=SUM([$Data.A1:.A5])", "of:=[Élèves.A5]"]


def _with_references():
    r = ODSReader.new()
    data, pupils = r.add_sheet("Data"), r.add_sheet("Élèves")
    for i in range(6):
        data[i, 0].value = i + 1
        pupils[i, 0].value = 10 * (i + 1)
    for col, formula in enumerate(_LIBREOFFICE_REFERENCES):
        r.sheet("Sheet1")[0, col].formula = formula
    return r


# ---------------------------------------------------------------------------
# References to other sheets as LibreOffice writes them (issue #14): an
# absolute sheet, a name starting with an accent, a range's end on its
# start's sheet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("edit", "row", "expected"),
    [
        pytest.param(
            lambda r: r.sheet("Data").insert_rows(0),
            0,
            ["of:=[$Data.A6]", "of:=SUM([$Data.A2:.A6])", "of:=[Élèves.A5]"],
            id="insert_rows on Data",
        ),
        pytest.param(
            lambda r: r.sheet("Élèves").insert_rows(0),
            0,
            ["of:=[$Data.A5]", "of:=SUM([$Data.A1:.A5])", "of:=[Élèves.A6]"],
            id="insert_rows on Élèves",
        ),
        # the range's end is on Data, not on the formula's sheet
        pytest.param(
            lambda r: r.sheet("Sheet1").insert_rows(0), 1, _LIBREOFFICE_REFERENCES, id="insert_rows on Sheet1"
        ),
        pytest.param(
            lambda r: r.sheet("Data").delete_row(4),
            0,
            ["of:=[#REF!]", "of:=SUM([$Data.A1:.A4])", "of:=[Élèves.A5]"],
            id="delete_row on Data",
        ),
        pytest.param(
            lambda r: r.sheet("Data").insert_columns(0),
            0,
            ["of:=[$Data.B5]", "of:=SUM([$Data.B1:.B5])", "of:=[Élèves.A5]"],
            id="insert_columns on Data",
        ),
        pytest.param(
            lambda r: r.rename_sheet("Data", "Figures"),
            0,
            ["of:=[$Figures.A5]", "of:=SUM([$Figures.A1:.A5])", "of:=[Élèves.A5]"],
            id="rename Data",
        ),
        # unquoted, as LibreOffice writes a name starting with an accent
        pytest.param(
            lambda r: r.rename_sheet("Data", "Évolution"),
            0,
            ["of:=[$Évolution.A5]", "of:=SUM([$Évolution.A1:.A5])", "of:=[Élèves.A5]"],
            id="rename Data to Évolution",
        ),
        pytest.param(
            lambda r: r.rename_sheet("Élèves", "Mes élèves"),
            0,
            ["of:=[$Data.A5]", "of:=SUM([$Data.A1:.A5])", "of:=['Mes élèves'.A5]"],
            id="rename Élèves",
        ),
    ],
)
def test_references_to_other_sheets_as_libreoffice_writes_them_follow_edits(edit, row, expected):
    # regression: none of the three followed; the range's end moved with
    # rows inserted into the formula's own sheet
    r = _with_references()
    edit(r)
    assert [r.sheet("Sheet1")[row, col].formula for col in range(3)] == expected


def _braces(ref, cells, other):
    """Formulas holding braces as text and as an inline array, referencing
    `ref`, `cells` and `other`."""
    return [
        'of:="{"&[' + ref + ']&"}"',
        'of:="\\textbf{"&[' + ref + ']&"}"',
        "of:=SUMPRODUCT([" + cells + "];{1|2|3})",
        'of:="{{"&[' + other + ']&"}}"',
    ]


def _with_braces(tmp_path):
    """Sheet1 with `_braces` in B1:B4, as LibreOffice writes them, and 1, 2,
    3 in A6:A8; then an Other sheet, and C1:C4 to sort B1:B4 backwards by."""
    formulas = _braces(".A6", ".A6:.A8", "$Other.A10")
    xml = "".join(
        table_row(empty_cells(), formula_cell(formula), number_cell(4 - i))
        for i, formula in enumerate(formulas)
    )
    xml += table_row(empty_cells()) + "".join(table_row(number_cell(n)) for n in (1, 2, 3))
    r = ODSReader(ods_with_sheet(tmp_path / "braces.ods", xml))
    r.add_sheet("Other")
    return r


@pytest.mark.parametrize(
    ("edit", "cells", "expected"),
    [
        pytest.param(
            lambda r: r.sheet("Sheet1").insert_rows(0),
            ["B2", "B3", "B4", "B5"],
            _braces(".A7", ".A7:.A9", "$Other.A10"),
            id="insert_rows",
        ),
        pytest.param(
            lambda r: r.sheet("Sheet1").delete_rows([4]),
            ["B1", "B2", "B3", "B4"],
            _braces(".A5", ".A5:.A7", "$Other.A10"),
            id="delete_rows",
        ),
        pytest.param(
            lambda r: r.sheet("Sheet1").insert_columns(0),
            ["C1", "C2", "C3", "C4"],
            _braces(".B6", ".B6:.B8", "$Other.A10"),
            id="insert_columns",
        ),
        pytest.param(
            lambda r: r.sheet("Other").insert_rows(0),
            ["B1", "B2", "B3", "B4"],
            _braces(".A6", ".A6:.A8", "$Other.A11"),
            id="insert_rows on Other",
        ),
        pytest.param(
            lambda r: r.rename_sheet("Other", "Autre"),
            ["B1", "B2", "B3", "B4"],
            _braces(".A6", ".A6:.A8", "$Autre.A10"),
            id="rename_sheet",
        ),
        pytest.param(
            lambda r: r.sheet("Sheet1").copy("B1:B4", "D1"),
            ["D1", "D2", "D3", "D4"],
            _braces(".C6", ".C6:.C8", "$Other.C10"),
            id="copy",
        ),
        pytest.param(
            lambda r: [r.sheet("Sheet1")[f"B{row}"].fill_formula(f"D{row}") for row in range(1, 5)],
            ["D1", "D2", "D3", "D4"],
            _braces(".C6", ".C6:.C8", "$Other.C10"),
            id="fill_formula",
        ),
        # each formula moves, and its references with it, as in a copy
        pytest.param(
            lambda r: r.sheet("Sheet1").sort("B1:C4", by=2),
            ["B4", "B3", "B2", "B1"],
            [
                'of:="{"&[.A9]&"}"',
                'of:="\\textbf{"&[.A7]&"}"',
                "of:=SUMPRODUCT([.A5:.A7];{1|2|3})",
                'of:="{{"&[$Other.A7]&"}}"',
            ],
            id="sort",
        ),
    ],
)
def test_an_edit_leaves_the_braces_of_a_formula_as_they_are(tmp_path, edit, cells, expected):
    # regression: each edit wrote the formula it rewrote through the setter,
    # which took its braces for {r}/{c} placeholders: "{"&[.A6]&"}" became
    # the text "&[.A7]&", and an inline array raised SyntaxError
    r = _with_braces(tmp_path)
    edit(r)
    assert [r.sheet("Sheet1")[address].formula for address in cells] == expected


def test_insert_rows_clears_the_cached_value_of_a_rewritten_formula(writable_reader):
    # a rewritten formula goes through the same path as any formula written by
    # odsslicer: its cached result is dropped. Stretching a range can change
    # what it computes (ROWS, COUNTBLANK...), and LibreOffice displays a cached
    # value as-is on open - an empty cache is what makes it recompute.
    s = writable_reader.sheet("Sheet1")
    assert s["A5"].value == 6.4
    s.insert_row(2)  # inside A2:A3
    assert s["A6"].formula_friendly == "=SUM(A2:A4)"
    assert s["A6"].value is None


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (2, "=SUM(A2:A4)"),  # inside A2:A3: the range stretches
        (1, "=SUM(A3:A4)"),  # at its start: the range moves whole
        (3, "=SUM(A2:A3)"),  # just below its end: the range is untouched
    ],
)
def test_insert_row_and_a_formula_range(writable_reader, at, expected):
    s = writable_reader.sheet("Sheet1")
    assert s["A5"].formula_friendly == "=SUM(A2:A3)"
    s.insert_row(at)
    assert s["A6"].formula_friendly == expected


def test_insert_rows_shifts_locked_references_too(writable_reader):
    # a $ lock pins a reference against fills, not against the cell moving
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A$6+A$7+$A8"
    s.insert_rows(3, 2)
    assert s["C1"].formula_friendly == "=$A$8+A$9+$A10"


def test_insert_columns_shifts_a_formula_reference(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "B2+$B$3"
    s.insert_column(1)
    assert s["A1"].formula_friendly == "=C2+$C$3"


def test_insert_rows_adjusts_a_cross_sheet_reference_only(writable_reader):
    r = writable_reader
    s1, s2 = r.sheet("Sheet1"), r.sheet("Sheet2Repeat")
    s2["A1"].formula = "Sheet1.A6+A6"  # the bare A6 is Sheet2Repeat's own
    s1.insert_rows(3, 2)
    assert s2["A1"].formula_friendly == "=Sheet1.A8+A6"
