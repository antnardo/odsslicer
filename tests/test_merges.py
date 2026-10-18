"""Merged cells: reading spans, merging, unmerging, and writing into a merge."""

import pytest

from odsslicer import ODSReader
from odsslicer.xmltree import qn

# ---------------------------------------------------------------------------
# Merged and covered cells (SheetFusion)
# ---------------------------------------------------------------------------


def test_merged_and_hidden_cells(sheet_fusion):
    assert sheet_fusion.size == (9, 4)
    assert sheet_fusion["A4"].value == 5  # hidden in cols
    assert sheet_fusion["B4"].value == 7  # not hidden
    assert sheet_fusion["C1"].value == 3  # hidden in rows
    assert sheet_fusion["C9"].value == 1  # hidden and repeated


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda cell: setattr(cell, "value", "nouveau maitre"), id="value"),
        pytest.param(lambda cell: setattr(cell, "formula", "1+1"), id="formula"),
        pytest.param(lambda cell: setattr(cell.style, "bold", True), id="style"),
    ],
)
def test_writing_a_merge_master_cell_keeps_the_merge(writable_reader, write):
    # as LibreOffice keeps it when one types into the merged cell: undoing it
    # made a long date written into a title merged across columns show ###
    s = writable_reader.sheet("SheetFusion")
    assert s.size == (9, 4)
    write(s["A1"])
    assert s["A1"].merge_range == "A1:C1"
    assert s["B1"].is_covered and s["C1"].is_covered
    assert s["B1"].value == 2.0  # still hidden under the merge, as it was
    assert s["D1"].value is None  # unrelated neighbour untouched
    assert s.size == (9, 4)


def test_write_covered_cell_unmerges_the_whole_range(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s["C1"].value = "valeur cachee"
    assert s["C1"].value == "valeur cachee"
    assert s["A1"].value == "1 (hidden 2, 3)"  # master keeps its own value, now standalone
    assert s["A1"].cell.get(qn("table:number-columns-spanned")) is None
    assert s["B1"].value == 2.0  # other covered sibling reveals its residual value
    assert s.size == (9, 4)


def test_write_vertical_merge_covered_cell(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s["A5"].value = "bas de fusion verticale"
    assert s["A5"].value == "bas de fusion verticale"
    assert s["A3"].value == "hidden as col, 5, 6"
    assert s["A3"].cell.get(qn("table:number-rows-spanned")) is None
    assert s["A4"].value == 5.0


def test_write_rectangular_merge_covered_cell(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s["C7"].value = "coin de fusion rectangulaire"
    assert s["C7"].value == "coin de fusion rectangulaire"
    assert s["A6"].value == "Hidden empty"
    assert s["A6"].cell.get(qn("table:number-rows-spanned")) is None
    for addr in ("B6", "C6", "D6", "A7", "B7", "D7"):
        assert s[addr].value is None
    assert s.size == (9, 4)


def test_write_covered_cell_that_is_also_column_repeated(writable_reader):
    # the hardest combined case: C9 is a covered cell (merged under A8) whose
    # underlying XML element is ALSO shared via table:number-columns-repeated="4"
    # across A9/B9/C9/D9 - both layers must be resolved before writing.
    s = writable_reader.sheet("SheetFusion")
    s["C9"].value = "triple resolution"
    assert s["C9"].value == "triple resolution"
    assert s["A9"].value == 1.0 and s["B9"].value == 1.0 and s["D9"].value == 1.0
    assert s["A8"].value == "Hidden empty with repetition"
    assert s["A8"].cell.get(qn("table:number-rows-spanned")) is None
    assert s.size == (9, 4)


def test_save_round_trip_after_unrepeat_and_unmerge(writable_reader, tmp_path):
    s = writable_reader.sheet("SheetFusion")
    s["C9"].value = "triple resolution"
    s["C1"].value = "cachee"
    s["B1"].value = "maitre modifie apres coup"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    s2 = reread.sheet("SheetFusion")
    assert s2.size == (9, 4)
    assert s2["C9"].value == "triple resolution"
    assert s2["A9"].value == 1.0 and s2["B9"].value == 1.0 and s2["D9"].value == 1.0
    assert s2["C1"].value == "cachee"
    assert s2["B1"].value == "maitre modifie apres coup"

    # other sheets are entirely unaffected
    assert reread.sheet("Sheet1")["A1"].value == "texte simple"
    sr = reread.sheet("Sheet2Repeat")
    assert sr.size == (9, 6) and sr["A1"].value == 1.0


# ---------------------------------------------------------------------------
# Merged cells, read: is_merged / is_merge_master / is_covered /
# merge_span / merge_master / merge_range (SheetFusion)
# ---------------------------------------------------------------------------


def test_unmerged_cell_reports_no_merge(sheet_fusion):
    c = sheet_fusion["D1"]
    assert c.is_merged is False
    assert c.is_merge_master is False
    assert c.is_covered is False
    assert c.merge_span is None
    assert c.merge_master is None
    assert c.merge_range is None


def test_horizontal_merge_master_and_covered(sheet_fusion):
    master = sheet_fusion["A1"]
    assert master.is_merge_master is True
    assert master.is_covered is False
    assert master.merge_span == (1, 3)
    assert master.merge_range == "A1:C1"
    assert master.merge_master is master

    covered = sheet_fusion["C1"]
    assert covered.is_covered is True
    assert covered.is_merge_master is False
    assert covered.is_merged is True
    assert covered.merge_span == (1, 3)
    assert covered.merge_range == "A1:C1"
    assert covered.merge_master.address == "A1"


def test_vertical_merge_span(sheet_fusion):
    assert sheet_fusion["A3"].merge_span == (3, 1)
    assert sheet_fusion["A5"].merge_range == "A3:A5"


def test_rectangular_merge_span(sheet_fusion):
    assert sheet_fusion["A6"].merge_span == (2, 4)
    assert sheet_fusion["D7"].merge_range == "A6:D7"
    assert sheet_fusion["D7"].merge_master.address == "A6"


# ---------------------------------------------------------------------------
# Merged cells, written: Sheet.merge / Sheet.unmerge
# ---------------------------------------------------------------------------


def test_merge_creates_a_master_and_covered_cells(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "top-left"
    s["C2"].value = "hidden"
    s.merge("C1:D2")
    assert s["C1"].is_merge_master
    assert s["C1"].merge_span == (2, 2)
    assert s["C1"].value == "top-left"
    assert s["D2"].is_covered
    assert s["D2"].merge_master.address == "C1"
    # the covered cell's own value is still there, just hidden
    assert s["C2"].is_covered
    assert s["C2"].value == "hidden"


def test_merge_grows_the_sheet_if_needed(writable_reader):
    s = writable_reader.sheet("Sheet1")
    size_before = s.size
    s.merge("Z10:AA11")
    assert s.size[0] >= size_before[0] and s.size[1] >= size_before[1]
    assert s.size[0] >= 11 and s.size[1] >= 27
    assert s["Z10"].merge_range == "Z10:AA11"


def test_merge_single_cell_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s.merge("A1")


@pytest.mark.parametrize("address", ["A1:B2", "A1:B1", "B1:D1", "A1:D1"])
def test_merge_already_merged_cell_raises(writable_reader, address):
    s = writable_reader.sheet("SheetFusion")
    with pytest.raises(ValueError):
        s.merge(address)  # A1 is already the master of A1:C1


def test_merging_a_range_merged_the_same_way_again_leaves_it_as_it_is(writable_reader):
    # what 0.13 had to do after writing into a merge's master, which undid it
    s = writable_reader.sheet("SheetFusion")
    s["A1"].value = "title"
    s.merge("A1:C1")
    s.merge((0, slice(0, 3)))
    assert s["A1"].merge_range == "A1:C1" and s["A1"].value == "title"
    assert s["B1"].is_covered and s["B1"].value == 2.0
    assert s["C1"].is_covered and s["C1"].value == 3.0


def test_unmerge_from_any_cell_in_the_range(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s.unmerge("C1")  # C1 is a covered cell of the A1:C1 merge, not the master
    assert s["A1"].is_merged is False
    assert s["B1"].is_merged is False and s["B1"].value == 2.0
    assert s["C1"].is_merged is False and s["C1"].value == 3.0


def test_unmerge_non_merged_cell_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s.unmerge("A1")


def test_unmerge_requires_a_single_cell_address(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    with pytest.raises(ValueError):
        s.unmerge("A1:B1")


def test_save_round_trip_after_merge(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "master"
    s.merge("C1:D2")
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["C1"].merge_range == "C1:D2"
    assert reread["D2"].is_covered
