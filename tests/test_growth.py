"""Growing the grid, and what the file holds past it, which every edit carries along."""

import zipfile

import pytest
from lxml import etree

from conftest import (
    _FORMATTED_PAST_DATA_XML,
    _GAP_COLUMNS_XML,
    _YELLOW_XML,
    _data_then_row_5,
    _is_note,
    _is_shape,
    _is_yellow,
    _repeat,
    addresses_holding,
    cells_with_content,
    chart_frame,
    empty_cells,
    every,
    first,
    note_cell,
    number_cell,
    ods_with_sheet,
    saved_table,
    shape_cell,
    table_row,
    text_cell,
    with_chart,
)
from odsslicer import ODSReader, Sheet
from odsslicer.constants import TAG_CELL
from odsslicer.xmltree import qn


def test_write_widens_existing_rows(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s.size == (9, 2)
    s["C1"].value = "nouvelle colonne"
    assert s.size == (9, 3)
    assert s["C1"].value == "nouvelle colonne"
    assert s["A1"].value == "texte simple" and s["B1"].value == "seconde colonne"
    assert s["C2"].value is None and s["C9"].value is None  # widened rows are blank


def test_write_appends_new_rows(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A10"].value = "nouvelle ligne"
    assert s.size == (10, 2)
    assert s["A10"].value == "nouvelle ligne"
    assert s["B10"].value is None
    assert s["A1"].value == "texte simple"


def test_write_grows_both_row_and_column_at_once(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["E12"].value = "coin"
    assert s.size == (12, 5)
    assert s["E12"].value == "coin"
    assert s["A1"].value == "texte simple"
    assert s["C1"].value is None and s["E1"].value is None


def test_write_grows_a_fully_empty_sheet(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    assert s.size == (0, 0)
    s["B3"].value = "from scratch"
    assert s.size == (3, 2)
    assert s["B3"].value == "from scratch"
    assert s["A1"].value is None and s["A3"].value is None and s["B1"].value is None


def test_write_growth_is_incremental_and_read_only_access_does_not_grow(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["Z1"].value = "far right"
    assert s.size == (9, 26)  # Sheet1 already had 9 rows; only the width grew
    s[50]  # read-only access must not trigger growth
    assert s.size == (9, 26)
    s["A100"].value = "far down"
    assert s.size == (100, 26)
    assert s["Z1"].value == "far right"
    assert s["A100"].value == "far down"
    assert s["A1"].value == "texte simple"


def test_growing_a_sheet_keeps_its_repeated_and_merged_cells_writable(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    assert s.size == (9, 4)
    s["F12"].value = "grown area"
    assert s.size == (12, 6)
    assert s["F12"].value == "grown area"
    # pre-existing repeated/merged data is still there and still writable afterwards
    assert s["A9"].value == 1.0
    s["C9"].value = "still works after growth"
    assert s["C9"].value == "still works after growth"
    assert s["A9"].value == 1.0 and s["B9"].value == 1.0 and s["D9"].value == 1.0


def test_growing_the_only_sheet_with_nothing_to_copy_from_writes_the_cells():
    # regression: growing a fully empty sheet that is the *only* sheet in the
    # whole document (nothing elsewhere to fall back on as a namespace
    # template) used to fail two different ways:
    # 1. Sheet.grow_to discarded the sheet's own lone "phantom" blank row
    #    *before* using it as a row/cell template, leaving nothing to copy.
    # 2. Writing a string value needs a text:p template, and a sheet this
    #    minimal (a single bare <table:table-cell/>, no text:p anywhere at
    #    all) has none anywhere in the document either.
    # Both now fall back to building a correctly-namespaced tag from scratch
    # (`xmltree.new`, declaring its namespace) instead of raising NotImplementedError.
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<table:table table:name="Sheet1">'
        "<table:table-column/><table:table-row><table:table-cell/></table:table-row>"
        "</table:table></root>"
    )
    table = first(xml, "table:table")
    sheet = Sheet(table)
    assert sheet.size == (0, 0)

    sheet["A1"].value = "hello"
    sheet["C3"].value = 42
    assert sheet.size == (3, 3)
    assert sheet["A1"].value == "hello"
    assert sheet["C3"].value == 42


def test_a_grown_sheet_reads_back_after_a_save(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "nouvelle colonne"
    s["A10"].value = "nouvelle ligne"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread.size == (10, 3)
    assert reread["C1"].value == "nouvelle colonne"
    assert reread["A10"].value == "nouvelle ligne"
    assert reread["A1"].value == "texte simple" and reread["B1"].value == "seconde colonne"
    assert reread["A2"].value == 3.4  # untouched original data intact


def test_growing_a_run_of_repeated_rows_widens_its_element_once(repeated_run_ods):
    # the shared element used to get the new cells once per row of the run
    s = ODSReader(repeated_run_ods).sheet("Sheet1")
    s.grow_to(0, 4)
    run = s.rows[1][0].cell.getparent()
    assert run.get(qn("table:number-rows-repeated")) == "5"
    cells = [cell for name in TAG_CELL for cell in every(run, name)]
    assert sum(_repeat(c, "table:number-columns-repeated") for c in cells) == 5
    # one new cell for the whole run, as load() would have it
    assert all(s.rows[r][4].cell is s.rows[1][4].cell for r in range(2, 6))


@pytest.mark.parametrize("address", ["A3", "B5", "A6", "A7", "C9"])
def test_writing_into_or_below_a_trailing_run_of_empty_rows_lands_where_written(tmp_path, address):
    # regression: IndexError inside the run, and one row too low below it -
    # load() left the run's last row out of the grid, not out of the element.
    # Valid ODF, though LibreOffice itself writes such a run's last row apart.
    xml = (
        '<table:table-column table:number-columns-repeated="2"/>'
        + table_row(text_cell("a"), text_cell("b"))
        + table_row(empty_cells(2), repeat=5)
    )
    path = ods_with_sheet(tmp_path / "tail.ods", xml)
    r = ODSReader(path)
    r.sheet("Sheet1")[address].value = "x"
    r.save()
    expected = {"A1": "a", "B1": "b", address: "x"}
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == expected


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("B1", {"A1": "a", "B1": "x", "Z1": "z"}),
        ("Z1", {"A1": "a", "Z1": "x"}),
        ("AB1", {"A1": "a", "Z1": "z", "AB1": "x"}),
    ],
)
def test_writing_around_empty_columns_between_data_lands_where_written(tmp_path, address, expected):
    # regression: B1 raised IndexError, and Z1 landed in AX1
    path = ods_with_sheet(tmp_path / "gap.ods", _GAP_COLUMNS_XML)
    r = ODSReader(path)
    r.sheet("Sheet1")[address].value = "x"
    r.save()
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == expected


@pytest.mark.parametrize(
    ("address", "formatted"),
    [("D1", True), ("E2", True), ("R1", True), ("S1", False), ("T2", False)],
)
def test_writing_past_the_data_lands_where_written_keeping_the_formatting_there(tmp_path, address, formatted):
    path = ods_with_sheet(tmp_path / "formatted.ods", _FORMATTED_PAST_DATA_XML, _YELLOW_XML)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    assert s.size == (2, 3)
    s[address].value = 1
    r.save()
    reread = ODSReader(path).sheet("Sheet1")
    data = {f"{col}{r}": text for r, text in ((1, "x"), (2, "y")) for col in "ABC"}
    assert cells_with_content(reread) == {**data, address: 1.0}
    assert (reread[address].style.background_color == "#fff2cc") is formatted
    # every formatted cell is still in the file, though past the grid
    assert _logical_cells_styled(path, "yellow") == 30


def _logical_cells_styled(path, style_name):
    """How many cells of the first sheet of `path` have the style named
    `style_name`, counting each repetition of a repeated element."""
    table = ODSReader(path).tables[0]
    return sum(
        _repeat(r, "table:number-rows-repeated") * _repeat(cell, "table:number-columns-repeated")
        for r in every(table, "table:table-row")
        for cell in r
        if cell.tag in {qn(name) for name in TAG_CELL} and cell.get(qn("table:style-name")) == style_name
    )


# ---------------------------------------------------------------------------
# What the file holds below the grid (issue #10): a shape or a chart anchored
# to a cell, a formatted cell, in rows the grid leaves out since no cell of
# theirs holds content. It stays in the file whatever the edit, where
# LibreOffice would have it. A note is content: the grid reaches it.
# ---------------------------------------------------------------------------


# edit -> where LibreOffice has a shape anchored at A5, a note on A5 and a
# yellow C5 after the same edit, as its own UNO API makes it
_ROW_5_EDITS = {
    "write A8": (lambda s: setattr(s["A8"], "value", 8), "A5", "A5", "C5"),
    "insert_rows(1)": (lambda s: s.insert_rows(1), "A6", "A6", "C6"),
    "insert_rows(4)": (lambda s: s.insert_rows(4), "A6", "A6", "C6"),
    "insert_rows(1, 3)": (lambda s: s.insert_rows(1, 3), "A8", "A8", "C8"),
    "insert_columns(0)": (lambda s: s.insert_columns(0), "B5", "B5", "D5"),
    "insert_columns(1, 2)": (lambda s: s.insert_columns(1, 2), "A5", "A5", "E5"),
    # a shape stays at its address, in the cell taking the deleted one's
    # place, and a note goes with its cell
    "delete_column(0)": (lambda s: s.delete_column(0), "A5", None, "B5"),
    "delete_rows([0])": (lambda s: s.delete_rows([0]), "A4", "A4", "C4"),
}


def _is_chart(cell):
    return cell.find("draw:frame", recursive=False) is not None


def _drawing_past_the_grid(tmp_path, drawing):
    """Three rows of two columns of data, with `drawing` anchored in the
    second row's 15th column (O2), past the grid: LibreOffice writes a chart
    there when the data stops short of it, and the empty columns between are
    too many to stay in the grid."""
    xml = (
        table_row(text_cell("1"), number_cell(1))
        + table_row(text_cell("2"), number_cell(2), empty_cells(12), drawing)
        + table_row(text_cell("3"), number_cell(3))
    )
    return ods_with_sheet(tmp_path / "past-the-grid.ods", xml)


def test_a_shape_anchored_past_the_grid_survives_the_deletion_of_its_row(tmp_path):
    # regression: the rescue of issue #24 only walked the grid's columns, so
    # deleting the row a chart was anchored in past them deleted the chart
    path = _drawing_past_the_grid(tmp_path, shape_cell("Box"))
    r = ODSReader(path)
    sheet = r.sheet("Sheet1")
    assert sheet.size == (3, 2)
    assert addresses_holding(saved_table(path), _is_shape) == ["O2"]
    sheet.delete_rows([1])
    r.save()
    assert addresses_holding(saved_table(path), _is_shape) == ["O2"]
    assert [row[0].value for row in ODSReader(path).sheet("Sheet1").rows] == ["1", "3"]


def test_two_structural_edits_in_a_row_follow_a_chart_past_the_grid(tmp_path):
    # regression: the first edit cached the elements recording a chart's
    # cells; the second deleted the row holding one, and remapping what was
    # left of it raised TypeError
    path = _drawing_past_the_grid(
        tmp_path, f"<table:table-cell>{chart_frame('Sheet1.B1:Sheet1.B3', 'Sheet1.T9')}</table:table-cell>"
    )
    r = ODSReader(path)
    sheet = r.sheet("Sheet1")
    sheet.insert_columns(1)
    sheet.delete_rows([1])
    r.save()
    assert addresses_holding(saved_table(path), _is_chart) == ["P2"]
    frame = saved_table(path).find("draw:frame")
    assert frame["table:end-cell-address"] == "Sheet1.U8"


def test_a_chart_deleted_with_every_row_does_not_break_the_edit_after(tmp_path):
    # with no row left to take it, a chart goes with the last one: the
    # elements an earlier edit cached for it are gone, and the edits that
    # follow must not try to remap them (they raised TypeError)
    path = _drawing_past_the_grid(
        tmp_path, f"<table:table-cell>{chart_frame('Sheet1.B1:Sheet1.B3', 'Sheet1.T9')}</table:table-cell>"
    )
    r = ODSReader(path)
    sheet = r.sheet("Sheet1")
    sheet.insert_columns(1)
    sheet.delete_rows([0, 1, 2])
    sheet.insert_columns(0)
    r.save()
    assert addresses_holding(saved_table(path), _is_chart) == []


def test_a_chart_deleted_with_its_cells_stops_following_the_edits(tmp_path):
    # its frame is out of the document, its content an orphan of the package:
    # nothing draws it, and the edits after leave its ranges as they were -
    # as with bs4, which emptied a deleted element. lxml keeps a detached one
    # readable, and the drawings an earlier edit cached would have followed
    path = with_chart(
        _drawing_past_the_grid(
            tmp_path,
            f"<table:table-cell>{chart_frame('Sheet1.B1:Sheet1.B3', 'Sheet1.T9')}</table:table-cell>",
        ),
        plot="Sheet1.A1:Sheet1.B3",
        categories="Sheet1.A1:Sheet1.A3",
        values="Sheet1.B1:Sheet1.B3",
        label="Sheet1.B1:Sheet1.B1",
    )
    r = ODSReader(path)
    sheet = r.sheet("Sheet1")
    sheet.insert_columns(1)  # caches the drawings, and moves the chart's ranges right
    sheet.delete_rows([0, 1, 2])  # the chart goes with the last row
    sheet.insert_columns(0)
    r.save()
    with zipfile.ZipFile(path) as package:
        chart = etree.fromstring(package.read("Object 1/content.xml"))
    plot = first(chart, "chart:plot-area")
    assert plot.get(qn("table:cell-range-address")) == "Sheet1.A1:Sheet1.C3"


_WHOLE_ROW_YELLOW = (
    '<style:style style:name="yellow" style:family="table-cell">'
    '<style:table-cell-properties fo:background-color="#ffff00"/></style:style>'
)


def _whole_row_yellow_sheet(tmp_path, name="wholerow.ods"):
    """A sheet whose first row is yellow as a whole, as LibreOffice writes it:
    its three cells, then one repeated to the sheet's last column."""
    xml = (
        '<table:table-column table:number-columns-repeated="3"/>'
        + table_row(text_cell("a"), text_cell("b"), text_cell("c"), empty_cells(16381, style="yellow"))
        + table_row(text_cell("d"), empty_cells(2))
    )
    return ods_with_sheet(tmp_path / name, xml, _WHOLE_ROW_YELLOW)


def _yellow_count(path):
    return len(addresses_holding(saved_table(path), _is_yellow))


def test_a_whole_rows_formatting_survives_a_plain_save(tmp_path):
    # regression (issue #23): loading clamps a row to the sheet's width and
    # used to delete what followed - a formatted cell holding nothing counted
    # as empty, so reading a sheet and saving it dropped the formatting of a
    # row formatted as a whole. No edit needed: reading was enough.
    path = _whole_row_yellow_sheet(tmp_path)
    assert _yellow_count(path) == 16381
    table = ODSReader(path)
    assert table.sheet("Sheet1").size == (2, 3)  # the formatting stays out of the grid
    table.save()
    assert _yellow_count(path) == 16381


def test_a_whole_rows_formatting_comes_back_into_the_grid_when_it_widens(tmp_path):
    # the counterpart: what the file keeps past the grid is taken back with
    # its formatting when the sheet widens (issue #6's machinery)
    path = _whole_row_yellow_sheet(tmp_path, "widen.ods")
    table = ODSReader(path)
    table.sheet("Sheet1")["E1"].value = "x"
    table.save()
    reread = ODSReader(path).sheet("Sheet1")
    assert reread.size == (2, 5)
    assert reread["D1"].style.background_color == "#ffff00"
    assert _yellow_count(path) == 16381


@pytest.mark.parametrize("edit", list(_ROW_5_EDITS))
def test_a_shape_below_the_data_stays_where_libreoffice_has_it(tmp_path, edit):
    # regression: writing past the data or inserting rows deleted it
    change, shape_at, _, _ = _ROW_5_EDITS[edit]
    xml = _data_then_row_5(shape_cell("Box"), empty_cells(2))
    path = ods_with_sheet(tmp_path / "shape.ods", xml)
    r = ODSReader(path)
    assert r.sheet("Sheet1").size == (4, 3)  # row 5 holds no content
    change(r.sheet("Sheet1"))
    r.save()
    assert addresses_holding(saved_table(path), _is_shape) == [shape_at]


@pytest.mark.parametrize("edit", list(_ROW_5_EDITS))
def test_a_formatted_cell_below_the_data_stays_where_libreoffice_has_it(tmp_path, edit):
    change, _, _, yellow_at = _ROW_5_EDITS[edit]
    xml = _data_then_row_5(empty_cells(2), empty_cells(style="yellow"))
    path = ods_with_sheet(tmp_path / "yellow.ods", xml, _YELLOW_XML)
    r = ODSReader(path)
    change(r.sheet("Sheet1"))
    r.save()
    assert addresses_holding(saved_table(path), _is_yellow) == [yellow_at]


@pytest.mark.parametrize("edit", list(_ROW_5_EDITS))
def test_a_note_below_the_data_stays_where_libreoffice_has_it(tmp_path, edit):
    change, _, note_at, _ = _ROW_5_EDITS[edit]
    xml = _data_then_row_5(note_cell("My note"), empty_cells(2))
    path = ods_with_sheet(tmp_path / "note.ods", xml)
    r = ODSReader(path)
    change(r.sheet("Sheet1"))
    r.save()
    expected = [note_at] if note_at else []
    assert addresses_holding(saved_table(path), _is_note) == expected


def test_writing_past_the_data_takes_back_the_rows_below_it(tmp_path):
    xml = _data_then_row_5(shape_cell("Box"), empty_cells(2))
    path = ods_with_sheet(tmp_path / "shape.ods", xml)
    r = ODSReader(path)
    r.sheet("Sheet1")["B5"].value = "beside"
    r.sheet("Sheet1")["A8"].value = 8
    r.save()
    reread = ODSReader(path)
    data = {"A1": "1", "A2": "2", "A3": "3"}
    assert cells_with_content(reread.sheet("Sheet1")) == {**data, "B5": "beside", "A8": 8.0}
    assert addresses_holding(saved_table(path), _is_shape) == ["A5"]


def test_writing_past_the_data_keeps_what_follows_the_rows_after_them(tmp_path):
    # the new rows go right after the grid's last row, before what a table
    # holds after its rows - here its named ranges
    xml = "<table:table-column/>" + table_row(text_cell("a")) + "<table:named-expressions/>"
    path = ods_with_sheet(tmp_path / "named.ods", xml)
    r = ODSReader(path)
    r.sheet("Sheet1")["A3"].value = "c"
    r.sheet("Sheet1")["A4"].value = "d"
    r.save()
    children = [child.name for child in saved_table(path).find_all(True, recursive=False)]
    assert children == ["table-column", *["table-row"] * 4, "named-expressions"]
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == {"A1": "a", "A3": "c", "A4": "d"}


def test_writing_a_column_past_the_data_never_places_a_row_by_position(positional):
    # regression (0.14.0 to 0.14.3): each new row went in with bs4's
    # insert_after, whose parent.index() scans the table from its first row,
    # and writing a column past the data grows the sheet one row per cell -
    # quadratic: generate+save of benchmarks/bench.py took 246 s at 100,000 rows, not 32 s
    sheet = ODSReader.new().sheet("Sheet1")
    sheet[0:200, 0].value = [[float(i)] for i in range(200)]
    assert sheet.size == (200, 1)
    assert "table-row" not in positional


# more than 1,000 empty rows between the data and a shape, which LibreOffice
# writes as one element: the grid ends above them
_FAR_BELOW_XML = (
    "<table:table-column/>"
    + table_row(text_cell("a"))
    + table_row(empty_cells(), repeat=1498)
    + table_row(shape_cell("Box"))
)


@pytest.mark.parametrize(
    ("edit", "size", "shape_at"),
    [
        pytest.param(lambda s: s.insert_rows(0, 2), (3, 1), "A1502", id="insert_rows(0, 2)"),
        # at the grid's end, before the rows it leaves out
        pytest.param(lambda s: s.insert_rows(1, 2), (3, 1), "A1502", id="insert_rows(1, 2)"),
        # taking back only the rows needed from the run of empty rows
        pytest.param(lambda s: setattr(s["A10"], "value", "b"), (10, 1), "A1500", id="write A10"),
        pytest.param(lambda s: setattr(s["A1500"], "value", "b"), (1500, 1), "A1500", id="write A1500"),
    ],
)
def test_a_shape_far_below_the_data_stays_where_libreoffice_has_it(tmp_path, edit, size, shape_at):
    # regression: deleted; and the empty rows above it must not give back the
    # rows inserted, which would move it up: the sheet is far from full
    path = ods_with_sheet(tmp_path / "far.ods", _FAR_BELOW_XML)
    r = ODSReader(path)
    assert r.sheet("Sheet1").size == (1, 1)
    edit(r.sheet("Sheet1"))
    assert r.sheet("Sheet1").size == size
    r.save()
    assert addresses_holding(saved_table(path), _is_shape) == [shape_at]


@pytest.mark.parametrize("grouped", ["the grid's last row", "the rows past it"])
@pytest.mark.parametrize(
    ("edit", "shape_at", "content"),
    [
        pytest.param(
            lambda s: s.insert_rows(1), "A6", {"A1": "1", "A3": "2", "A4": "3"}, id="insert_rows(1)"
        ),
        pytest.param(
            lambda s: setattr(s["A8"], "value", 8),
            "A5",
            {"A1": "1", "A2": "2", "A3": "3", "A8": 8.0},
            id="write A8",
        ),
        pytest.param(
            lambda s: s.insert_columns(0),
            "B5",
            {"B1": "1", "B2": "2", "B3": "3"},
            id="insert_columns(0)",
        ),
    ],
)
def test_a_shape_below_the_data_in_a_group_of_rows_stays_where_libreoffice_has_it(
    tmp_path, grouped, edit, shape_at, content
):
    # LibreOffice writes rows grouped with Data > Group inside an element of
    # their own: what lies past the grid is looked for across groups
    data = "".join(table_row(text_cell(str(v)), empty_cells(2)) for v in (1, 2, 3))
    row_4, row_5 = table_row(empty_cells(3)), table_row(shape_cell("Box"), empty_cells(2))
    if grouped == "the grid's last row":
        rows = data + f"<table:table-row-group>{row_4}</table:table-row-group>" + row_5
    else:
        rows = data + row_4 + f"<table:table-row-group>{row_5}</table:table-row-group>"
    xml = '<table:table-column table:number-columns-repeated="3"/>' + rows
    path = ods_with_sheet(tmp_path / "grouped.ods", xml)
    r = ODSReader(path)
    assert r.sheet("Sheet1").size == (4, 3)
    edit(r.sheet("Sheet1"))
    r.save()
    assert addresses_holding(saved_table(path), _is_shape) == [shape_at]
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == content


def test_write_formula_beyond_the_current_extent_grows_the_sheet(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s.size == (9, 2)
    s["E5"].formula = "=[.A1]"
    assert s.size == (9, 5)
    assert s["E5"].is_formula
