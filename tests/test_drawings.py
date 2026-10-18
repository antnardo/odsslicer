"""Charts and shapes following structural edits and renamed sheets (issue #15)."""

import zipfile

import pytest
from lxml import etree

from conftest import (
    chart_frame,
    chart_ranges,
    empty_cells,
    ods_with_sheet,
    rebind_prefix,
    table_row,
    text_cell,
    with_chart,
)
from odsslicer import MalformedDocumentError, ODSReader

# a header in A1:B1, days in A2:A7, visits in B2:B7, a chart anchored at D1
# and ending in F10 plotting them - the report's layout - and a footer in
# A12, so that the grid reaches the chart's end
_CHART_SHEET_XML = (
    '<table:table-column table:number-columns-repeated="6"/>'
    + table_row(
        text_cell("Day"),
        text_cell("Visits"),
        empty_cells(),
        "<table:table-cell>"
        + chart_frame("Sheet1.A2:Sheet1.A7 Sheet1.B1:Sheet1.B1 Sheet1.B2:Sheet1.B7", "Sheet1.F10")
        + "</table:table-cell>",
        empty_cells(2),
    )
    + "".join(
        table_row(text_cell(day), text_cell(visits), empty_cells(4))
        for day, visits in zip(
            ("mon", "tue", "wed", "thu", "fri", "sat"),
            ("10", "20", "30", "40", "50", "60"),
            strict=True,
        )
    )
    + table_row(empty_cells(6), repeat=4)
    + table_row(text_cell("footer"), empty_cells(5))
)


def _chart_workbook(tmp_path, sheet="Sheet1"):
    xml = _CHART_SHEET_XML.replace("Sheet1.", f"{sheet}.")
    path = ods_with_sheet(tmp_path / "chart.ods", xml)
    return with_chart(
        path,
        plot=f"{sheet}.A1:{sheet}.B7",
        categories=f"{sheet}.A2:{sheet}.A7",
        values=f"{sheet}.B2:{sheet}.B7",
        label=f"{sheet}.B1:{sheet}.B1",
    )


def _ranges(plot, categories, values, label, end):
    return {
        "table:cell-range-address": plot,
        "categories": categories,
        "chart:values-cell-range-address": values,
        "chart:label-cell-address": label,
        "notify": f"{categories} {label} {values}",
        "end": end,
    }


# edit -> the ranges LibreOffice gives the chart for the same edit, through
# its UNO API, on the report this sheet reproduces
_CHART_EDITS = {
    "insert_rows(4)": (
        lambda s: s.insert_rows(4),  # inside the days: they stretch
        _ranges(
            "Sheet1.A1:Sheet1.B8",
            "Sheet1.A2:Sheet1.A8",
            "Sheet1.B2:Sheet1.B8",
            "Sheet1.B1:Sheet1.B1",
            "Sheet1.F11",
        ),
    ),
    "insert_rows(0)": (
        lambda s: s.insert_rows(0),  # above everything: all moves down
        _ranges(
            "Sheet1.A2:Sheet1.B8",
            "Sheet1.A3:Sheet1.A8",
            "Sheet1.B3:Sheet1.B8",
            "Sheet1.B2:Sheet1.B2",
            "Sheet1.F11",
        ),
    ),
    "delete_rows([3])": (
        lambda s: s.delete_rows([3]),
        _ranges(
            "Sheet1.A1:Sheet1.B6",
            "Sheet1.A2:Sheet1.A6",
            "Sheet1.B2:Sheet1.B6",
            "Sheet1.B1:Sheet1.B1",
            "Sheet1.F9",
        ),
    ),
    # every day deleted: a chart has no #REF!, its ranges keep their address
    "delete_rows(days)": (
        lambda s: s.delete_rows(range(1, 7)),
        _ranges(
            "Sheet1.A1:Sheet1.B1",
            "Sheet1.A2:Sheet1.A7",
            "Sheet1.B2:Sheet1.B7",
            "Sheet1.B1:Sheet1.B1",
            "Sheet1.F4",
        ),
    ),
    "insert_columns(1)": (
        lambda s: s.insert_columns(1),
        _ranges(
            "Sheet1.A1:Sheet1.C7",
            "Sheet1.A2:Sheet1.A7",
            "Sheet1.C2:Sheet1.C7",
            "Sheet1.C1:Sheet1.C1",
            "Sheet1.G10",
        ),
    ),
    "delete_column(1)": (
        lambda s: s.delete_column(1),  # the visits, deleted whole
        _ranges(
            "Sheet1.A1:Sheet1.A7",
            "Sheet1.A2:Sheet1.A7",
            "Sheet1.B2:Sheet1.B7",
            "Sheet1.B1:Sheet1.B1",
            "Sheet1.E10",
        ),
    ),
    # the chart's end cell deleted: it keeps its address, the cell taking its
    # place, as LibreOffice keeps it when the row or the column goes
    "delete_rows([9])": (
        lambda s: s.delete_rows([9]),
        _ranges(
            "Sheet1.A1:Sheet1.B7",
            "Sheet1.A2:Sheet1.A7",
            "Sheet1.B2:Sheet1.B7",
            "Sheet1.B1:Sheet1.B1",
            "Sheet1.F10",
        ),
    ),
    "delete_column(5)": (
        lambda s: s.delete_column(5),
        _ranges(
            "Sheet1.A1:Sheet1.B7",
            "Sheet1.A2:Sheet1.A7",
            "Sheet1.B2:Sheet1.B7",
            "Sheet1.B1:Sheet1.B1",
            "Sheet1.F10",
        ),
    ),
    # two edits in a row: the chart is read once, and the second starts
    # where the first left it
    "insert_rows(4), insert_columns(1)": (
        lambda s: (s.insert_rows(4), s.insert_columns(1)),
        _ranges(
            "Sheet1.A1:Sheet1.C8",
            "Sheet1.A2:Sheet1.A8",
            "Sheet1.C2:Sheet1.C8",
            "Sheet1.C1:Sheet1.C1",
            "Sheet1.G11",
        ),
    ),
}


# ---------------------------------------------------------------------------
# Charts follow structural edits (issue #15): their ranges, in their own part
# of the package and listed on their object, and the cell their frame ends in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("edit", list(_CHART_EDITS))
def test_a_chart_follows_rows_and_columns_as_libreoffice_has_it(tmp_path, edit):
    # regression: its ranges and its end stayed as they were - an inserted
    # day was left out of the chart, which shrank by a row on open
    change, expected = _CHART_EDITS[edit]
    path = _chart_workbook(tmp_path)
    r = ODSReader(path)
    change(r.sheet("Sheet1"))
    r.save()
    assert chart_ranges(path) == expected


def test_a_chart_follows_its_sheet_renamed(tmp_path):
    # regression: it pointed at the old name, and LibreOffice found no range
    path = _chart_workbook(tmp_path)
    r = ODSReader(path)
    r.rename_sheet("Sheet1", "Report")
    r.save()
    assert chart_ranges(path) == _ranges(
        "Report.A1:Report.B7",
        "Report.A2:Report.A7",
        "Report.B2:Report.B7",
        "Report.B1:Report.B1",
        "Report.F10",
    )


def test_a_chart_follows_the_rows_of_another_sheet(tmp_path):
    # the chart stands on Sheet1, its data on Data: an edit of Data moves it
    path = _chart_workbook(tmp_path, sheet="Data")
    r = ODSReader(path)
    data = r.add_sheet("Data")
    data.insert_rows(0)
    r.save()
    ranges = chart_ranges(path)
    assert ranges["chart:values-cell-range-address"] == "Data.B3:Data.B8"
    assert ranges["end"] == "Data.F11"


def test_a_chart_is_saved_as_it_was_when_no_edit_moves_it(tmp_path):
    path = _chart_workbook(tmp_path)
    before = chart_ranges(path)
    r = ODSReader(path)
    r.sheet("Sheet1")["A9"].value = "no move"
    r.save()
    assert chart_ranges(path) == before


def test_a_chart_part_no_edit_moves_is_copied_through_byte_for_byte(tmp_path):
    # its ranges being the same is not enough: a chart holds plenty odsslicer
    # does not model, and serialising its tree would reflow all of it (see
    # test_reader.py, "what a save leaves alone")
    path = _chart_workbook(tmp_path)
    with zipfile.ZipFile(path) as package:
        before = package.read("Object 1/content.xml")

    r = ODSReader(path)
    r.sheet("Sheet1")["A9"].value = "no move"
    r.save()

    with zipfile.ZipFile(path) as package:
        assert package.read("Object 1/content.xml") == before


def _chart_part_edited(path, edit):
    """`path` with its chart part, `Object 1/content.xml`, through `edit`
    (bytes -> bytes), in place."""
    with zipfile.ZipFile(path) as package:
        members = [(item, package.read(item.filename)) for item in package.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for item, data in members:
            package.writestr(item, edit(data) if item.filename == "Object 1/content.xml" else data)
    return path


_CHART_NS = "urn:oasis:names:tc:opendocument:xmlns:chart:1.0"
_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def test_a_chart_with_other_prefixes_follows_and_keeps_its_own(tmp_path):
    # a chart's part is on lxml: its ranges are found by namespace whatever
    # prefixes it binds, and it is written back with the same prefixes
    path = _chart_part_edited(
        _chart_workbook(tmp_path),
        lambda data: rebind_prefix(
            rebind_prefix(rebind_prefix(data, _CHART_NS, "c"), _TABLE_NS, "t"), _OFFICE_NS, "o"
        ),
    )
    with zipfile.ZipFile(path) as package:
        assert b"<c:series " in package.read("Object 1/content.xml")
    r = ODSReader(path)
    r.sheet("Sheet1").insert_rows(4)
    r.save()

    with zipfile.ZipFile(path) as package:
        saved = package.read("Object 1/content.xml")
    assert b'<c:plot-area t:cell-range-address="Sheet1.A1:Sheet1.B8">' in saved
    assert b'<c:categories t:cell-range-address="Sheet1.A2:Sheet1.A8"/>' in saved
    assert b'c:values-cell-range-address="Sheet1.B2:Sheet1.B8"' in saved
    assert b"xmlns:chart=" not in saved and b"xmlns:table=" not in saved and b"<office:" not in saved


def test_a_chart_part_rewritten_with_its_ranges_unchanged_says_what_it_said(tmp_path):
    # its data on Data, an edit of Sheet1: the part is serialised by lxml,
    # nothing in it having moved - the same document in canonical form,
    # under the XML declaration it was read with
    path = _chart_workbook(tmp_path, sheet="Data")
    with zipfile.ZipFile(path) as package:
        before = package.read("Object 1/content.xml")
    r = ODSReader(path)
    r.sheet("Sheet1").insert_rows(0)
    r.save()

    with zipfile.ZipFile(path) as package:
        after = package.read("Object 1/content.xml")
    assert after != before, "a structural edit is expected to rewrite every chart"
    assert etree.tostring(etree.fromstring(after), method="c14n") == etree.tostring(
        etree.fromstring(before), method="c14n"
    )
    assert after.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content')


def test_an_embedded_object_that_is_no_chart_is_copied_through_a_structural_edit(tmp_path):
    # a formula object, say: its part has no office:chart, and is not ours -
    # an empty element and an escape in it, which serialising would rewrite
    formula = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<math xmlns="http://www.w3.org/1998/Math/MathML"><semantics>'
        b"<mi>x</mi><mtext>&apos;</mtext><mspace></mspace></semantics></math>"
    )
    path = _chart_part_edited(_chart_workbook(tmp_path), lambda data: formula)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_rows(4)
    r.save()

    with zipfile.ZipFile(path) as package:
        assert package.read("Object 1/content.xml") == formula


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r.sheet("Sheet1").insert_rows(4),
        lambda r: r.sheet("Sheet1").insert_columns(1),
        lambda r: r.sheet("Sheet1").delete_rows([3]),
        lambda r: r.sheet("Sheet1").delete_column(1),
        lambda r: r.rename_sheet("Sheet1", "Report"),
    ],
    ids=["insert_rows", "insert_columns", "delete_rows", "delete_column", "rename_sheet"],
)
def test_a_structural_edit_refuses_a_malformed_chart_before_changing_anything(tmp_path, edit):
    # strict parsing, as for every part: bs4 recovered a truncated
    # chart and save() wrote the guess back; the edit now raises, the sheet
    # left as it was rather than edited but for its chart
    path = _chart_part_edited(_chart_workbook(tmp_path), lambda data: data[: len(data) // 2])
    r = ODSReader(path)
    before = etree.tostring(r.data)

    for _ in range(2):  # and again: a refusal is not forgotten
        with pytest.raises(MalformedDocumentError, match=r"Object 1/content\.xml"):
            edit(r)

    assert etree.tostring(r.data) == before
    assert r.sheets_names == ["Sheet1"]


def test_reading_the_charts_before_an_edit_leaves_the_drawings_to_the_edit(tmp_path):
    # the charts are parsed before a structural edit, to refuse a malformed
    # one; looking them up through their `<draw:object>` would also record
    # the drawings before the edit - and deleting the row of a chart
    # anchored past the grid's columns (P1 here, past 13 empty columns, as
    # LibreOffice's own ser_labels.ods has it) discards it, which the remap
    # then tripped on (TypeError)
    xml = table_row(
        text_cell("Day"),
        text_cell("Visits"),
        empty_cells(13),
        "<table:table-cell>" + chart_frame("Sheet1.B2:Sheet1.B3", "Sheet1.T10") + "</table:table-cell>",
    ) + "".join(
        table_row(text_cell(day), text_cell(visits)) for day, visits in (("mon", "10"), ("tue", "20"))
    )
    path = with_chart(
        ods_with_sheet(tmp_path / "chart.ods", xml),
        plot="Sheet1.A1:Sheet1.B3",
        categories="Sheet1.A2:Sheet1.A3",
        values="Sheet1.B2:Sheet1.B3",
        label="Sheet1.B1:Sheet1.B1",
    )
    r = ODSReader(path)
    assert r.sheet("Sheet1").n_cols == 2, "the chart is expected to be anchored past the grid"

    r.sheet("Sheet1").delete_rows([0])
    r.save()

    assert ODSReader(path).sheet("Sheet1")["A1"].value == "mon"
