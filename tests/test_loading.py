"""Loading a sheet: the grid built from the XML, and the padding left out of it."""

import datetime as dt
import warnings

import pytest

from conftest import (
    _GAP_COLUMNS_XML,
    FIXTURES_DIR,
    FRAMED_TABLE_CELL,
    _data_then_row_5,
    _is_note,
    _repeat,
    _saved_with_raw_values,
    addresses_holding,
    cells_with_content,
    empty_cells,
    list_note_cell,
    note_cell,
    number_cell,
    ods_with_sheet,
    saved_table,
    table_row,
    text_cell,
)
from odsslicer import Cell, ODSReader, string_address
from odsslicer.columns import _column_default_cell_style
from odsslicer.constants import TAG_CELL

# ---------------------------------------------------------------------------
# Repeated rows and columns (ODF's "number-rows/columns-repeated")
# ---------------------------------------------------------------------------


def test_repeated_rows_and_cols_shape(sheet_repeat):
    assert sheet_repeat.to_numpy().shape == sheet_repeat.size == (9, 6)


def test_repeated_rows_and_cols_values(sheet_repeat):
    assert sheet_repeat["A1:D2"].to_list() == [[1, 1, 1, 1], [1, 1, 1, 1]]
    assert sheet_repeat["F9"].value == 5
    assert sheet_repeat["F8"].value is None


def test_column_out_of_bounds_returns_empty_not_indexerror(sheet_repeat):
    # regression: get_col, which 1.0 removed, compared against n_rows instead
    # of n_cols, raising IndexError whenever n_rows > n_cols for an
    # out-of-range column
    col = sheet_repeat[:, sheet_repeat.n_cols + 3]
    assert col.size == (sheet_repeat.n_rows, 1)
    assert col.to_list() == [[None]] * sheet_repeat.n_rows


class TestChildrenNotDescendants:
    """What the sheet holds is found among children, not anything under
    them: step 3 of the lxml migration (ROADMAP.md)."""

    def test_a_table_in_a_cells_frame_is_no_sheet_and_no_column(self, tmp_path):
        reader = ODSReader(
            ods_with_sheet(tmp_path / "framed.ods", table_row(FRAMED_TABLE_CELL, number_cell(2)))
        )
        assert reader.sheets_names == ["Sheet1"]
        sheet = reader.sheet("Sheet1")
        assert (sheet.size, sheet["A1"].value, sheet["B1"].value) == ((1, 2), "v", 2)

    def test_rows_in_header_rows_and_nested_groups_are_the_sheets(self, tmp_path):
        xml = (
            f"<table:table-header-rows>{table_row(text_cell('head'))}</table:table-header-rows>"
            f"<table:table-row-group>{table_row(text_cell('a'))}"
            f"<table:table-row-group>{table_row(text_cell('b'))}</table:table-row-group>"
            f"</table:table-row-group>{table_row(text_cell('c'))}"
        )
        sheet = ODSReader(ods_with_sheet(tmp_path / "grouped.ods", xml)).sheet("Sheet1")
        assert [row[0].value for row in sheet.rows] == ["head", "a", "b", "c"]

    def test_a_note_set_as_a_list_reads_its_items(self, tmp_path):
        sheet = ODSReader(ods_with_sheet(tmp_path / "note.ods", table_row(list_note_cell("a", "b")))).sheet(
            "Sheet1"
        )
        assert sheet["A1"].comment.text == "a\nb"

    def test_rewriting_a_note_set_as_a_list_drops_the_list(self, tmp_path):
        reader = ODSReader(
            ods_with_sheet(tmp_path / "note.ods", table_row(list_note_cell("a", "b", author="me")))
        )
        reader.sheet("Sheet1")["A1"].comment.text = "new"
        out = tmp_path / "out.ods"
        reader.save(out)
        note = saved_table(out).find("office:annotation")
        assert note.find("text:list") is None
        assert [p.get_text() for p in note.find_all("text:p")] == ["new"]
        assert ODSReader(out).sheet("Sheet1")["A1"].comment.author == "me"

    def test_an_author_given_to_a_note_set_as_a_list_goes_before_it(self, tmp_path):
        reader = ODSReader(ods_with_sheet(tmp_path / "note.ods", table_row(list_note_cell("a"))))
        reader.sheet("Sheet1")["A1"].comment.author = "me"
        out = tmp_path / "out.ods"
        reader.save(out)
        names = [
            f"{c.prefix}:{c.name}"
            for c in saved_table(out).find("office:annotation").find_all(True, recursive=False)
        ]
        assert names == ["dc:creator", "text:list"]


# An element of another namespace that shares a local name with ODF's: what
# a file may hold where it writes elements of its own (ODF 1.3, 3.17)
OTHER_NAMESPACE = "urn:example:other"


class TestNamesByNamespace:
    """An element is what its namespace and local name say: since 1.0, one
    of another namespace is not taken for a row, a cell or a column because
    its local name is the same - LibreOffice skips it."""

    def test_a_row_of_another_namespace_is_not_a_row(self, tmp_path):
        xml = (
            table_row(text_cell("a"))
            + f'<x:table-row xmlns:x="{OTHER_NAMESPACE}">{text_cell("foreign")}</x:table-row>'
            + table_row(text_cell("c"))
        )
        sheet = ODSReader(ods_with_sheet(tmp_path / "foreign.ods", xml)).sheet("Sheet1")
        assert sheet.to_list() == [["a"], ["c"]]

    def test_a_row_group_of_another_namespace_holds_no_row(self, tmp_path):
        xml = (
            table_row(text_cell("a"))
            + f'<x:table-row-group xmlns:x="{OTHER_NAMESPACE}">'
            + table_row(text_cell("b"))
            + "</x:table-row-group>"
            + table_row(text_cell("c"))
        )
        sheet = ODSReader(ods_with_sheet(tmp_path / "foreign.ods", xml)).sheet("Sheet1")
        assert sheet.to_list() == [["a"], ["c"]]

    def test_a_column_of_another_namespace_is_not_a_column(self, tmp_path):
        xml = (
            f'<x:table-column xmlns:x="{OTHER_NAMESPACE}" table:default-cell-style-name="other"/>'
            '<table:table-column table:default-cell-style-name="mine"/>' + table_row(text_cell("a"))
        )
        sheet = ODSReader(ods_with_sheet(tmp_path / "foreign.ods", xml)).sheet("Sheet1")
        assert _column_default_cell_style(sheet.table, 0) == "mine"


@pytest.mark.parametrize(
    "path",
    [
        FIXTURES_DIR / "TEST.ods",
        FIXTURES_DIR / "WHOLEROW.ods",
        *sorted((FIXTURES_DIR / "wild").glob("*.ods")),
    ],
    ids=lambda path: path.name,
)
def test_every_position_of_the_grid_reads_as_its_element_read_afresh(path):
    # `load` copies the first reading of a repeated cell to its other
    # positions (`Cell._at`): each must be what a `Cell` built there reads
    for sheet in ODSReader(path).sheets:
        for r, row in enumerate(sheet.rows):
            for c, cell in enumerate(row):
                assert vars(cell) == vars(Cell(cell.cell, r, c, sheet)), (r, c)


class TestLoadingWideSheets:
    """A sheet formatted far past its data, as LibreOffice writes one."""

    WIDE_XML = table_row(
        text_cell("a"),
        number_cell(1),
        '<table:table-cell table:style-name="Default" table:number-columns-repeated="1000"/>',
        repeat=50,
    ) + table_row(
        '<table:table-cell table:number-columns-repeated="2"/>',
        note_cell("far right"),
        '<table:table-cell table:style-name="Default" table:number-columns-repeated="999"/>',
    )

    def test_no_cell_is_built_for_the_columns_left_out(self, tmp_path, monkeypatch):
        path = ods_with_sheet(tmp_path / "wide.ods", self.WIDE_XML)
        built = []
        original, copied = Cell.__init__, Cell._at

        def recording(cell, tag, row=0, col=0, sheet=None):
            if sheet is not None:
                built.append(col)
            original(cell, tag, row, col, sheet)

        def recording_copy(cell, row, col):  # a repeated cell's other positions
            built.append(col)
            return copied(cell, row, col)

        monkeypatch.setattr(Cell, "__init__", recording)
        monkeypatch.setattr(Cell, "_at", recording_copy)
        sheet = ODSReader(path).sheet("Sheet1")
        assert sheet.size == (51, 3)
        assert max(built) == 2
        assert len(built) == 51 * 3

    def test_the_columns_left_out_stay_in_the_file(self, tmp_path):
        path = ods_with_sheet(tmp_path / "wide.ods", self.WIDE_XML)
        r = ODSReader(path)
        sheet = r.sheet("Sheet1")
        assert (sheet["A1"].value, sheet["B1"].value, sheet["C51"].comment.text) == ("a", 1, "far right")
        out = tmp_path / "out.ods"
        r.save(out)
        widths = {
            sum(_repeat(c, "table:number-columns-repeated") for c in row.find_all(TAG_CELL))
            for row in saved_table(out).find_all("table:table-row")
        }
        assert widths == {1002}


# ---------------------------------------------------------------------------
# What load() leaves out of the grid (issue #6): only the padding after the
# data, never empty rows or columns between it - and what it leaves out
# stays in the file, where writing past the data must not land beyond it.
# ---------------------------------------------------------------------------


def test_empty_columns_between_data_stay_in_the_grid(tmp_path):
    # regression: a run of more than 10 of them was left out, moving Z1 to B1
    path = ods_with_sheet(tmp_path / "gap.ods", _GAP_COLUMNS_XML)
    s = ODSReader(path).sheet("Sheet1")
    assert s.size == (1, 26)
    assert s["B1"].value is None
    assert s["Z1"].value == "z"


def test_empty_rows_between_data_stay_in_the_grid(tmp_path):
    # regression: an element of more than 1,000 empty rows was left out
    # wherever it stood, moving A2000 up to A2
    xml = (
        "<table:table-column/>"
        + table_row(text_cell("a"))
        + table_row(empty_cells(), repeat=1998)
        + table_row(text_cell("far below"))
    )
    s = ODSReader(ods_with_sheet(tmp_path / "gap.ods", xml)).sheet("Sheet1")
    assert s.size == (2000, 1)
    assert s["A2"].value is None
    assert s["A2000"].value == "far below"


@pytest.mark.parametrize(
    ("after_filler", "size"),
    [
        # LibreOffice writes the sheet's last row apart, after the filler: the
        # empty row above the filler stays in the grid, as it always has
        (1, (2, 1)),
        # rows after the filler lie a million rows down: out of the grid
        (133, (2, 1)),
        (0, (1, 1)),  # no row after the filler: the empty last row goes
    ],
)
def test_the_rows_around_the_filler_of_a_full_height_sheet(tmp_path, after_filler, size):
    xml = (
        "<table:table-column/>"
        + table_row(text_cell("a"))
        + table_row(empty_cells())
        + table_row(empty_cells(), repeat=1_048_570)
    )
    if after_filler > 1:
        xml += table_row(empty_cells(), repeat=after_filler - 1)
    if after_filler:
        xml += table_row(empty_cells())
    path = ods_with_sheet(tmp_path / "padded.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    assert s.size == size
    s[size[0], 0].value = "next"  # the first row past the grid
    r.save()
    reread = ODSReader(path).sheet("Sheet1")
    assert cells_with_content(reread) == {"A1": "a", f"A{size[0] + 1}": "next"}


def test_empty_rows_padding_a_sheet_to_its_full_height_are_still_left_out(tmp_path):
    xml = "<table:table-column/>" + table_row(text_cell("a")) + table_row(empty_cells(), repeat=1_048_575)
    path = ods_with_sheet(tmp_path / "padded.ods", xml)
    r = ODSReader(path)
    assert r.sheet("Sheet1").size == (1, 1)
    r.sheet("Sheet1")["A2"].value = "b"
    r.save()
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == {"A1": "a", "A2": "b"}


def test_a_plain_unformatted_filler_is_still_left_out(tmp_path):
    # the padding itself says nothing about its cells, and still goes: it is
    # what keeps the grid from materialising 16,384 columns per row
    xml = '<table:table-column table:number-columns-repeated="3"/>' + table_row(
        text_cell("a"), text_cell("b"), text_cell("c"), empty_cells(16381)
    )
    path = ods_with_sheet(tmp_path / "plain.ods", xml)
    table = ODSReader(path)
    assert table.sheet("Sheet1").size == (1, 3)
    table.save()
    row = saved_table(path).find("table:table-row")
    assert sum(_repeat(c, "table:number-columns-repeated") for c in row.find_all(TAG_CELL)) == 3


def test_a_note_below_the_data_is_in_the_grid(tmp_path):
    # regression: its row was left out of the grid, and the note unreadable
    xml = _data_then_row_5(note_cell("My note"), empty_cells(2))
    s = ODSReader(ods_with_sheet(tmp_path / "note.ods", xml)).sheet("Sheet1")
    assert s.size == (5, 3)
    assert s["A5"].comment.text == "My note"
    assert s["A5"].is_empty  # a note is not a value


@pytest.mark.parametrize("gap", [24, 2000])
def test_a_note_right_of_the_data_is_in_the_grid(tmp_path, gap):
    # regression: more than 10 empty columns before it left it out of the
    # grid, more than 1,000 out of the file
    xml = (
        f'<table:table-column table:number-columns-repeated="{gap + 2}"/>'
        + table_row(text_cell("a"), empty_cells(gap), note_cell("far right"))
        + table_row(text_cell("b"), empty_cells(gap + 1))
    )
    path = ods_with_sheet(tmp_path / "note.ods", xml)
    r = ODSReader(path)
    assert r.sheet("Sheet1").size == (2, gap + 2)
    assert [len(row) for row in r.sheet("Sheet1").rows] == [gap + 2, gap + 2]
    assert r.sheet("Sheet1")[0, gap + 1].comment.text == "far right"
    r.save()
    assert addresses_holding(saved_table(path), _is_note) == [string_address(0, gap + 1)]


def test_a_note_far_below_the_data_is_in_the_grid(tmp_path):
    xml = (
        "<table:table-column/>"
        + table_row(text_cell("a"))
        + table_row(empty_cells(), repeat=1498)
        + table_row(note_cell("far below"))
    )
    s = ODSReader(ods_with_sheet(tmp_path / "note.ods", xml)).sheet("Sheet1")
    assert s.size == (1500, 1)
    assert s["A1500"].comment.text == "far below"


# ---------------------------------------------------------------------------
# Dates, date-times and durations (issue #4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value_type", "raw", "text", "expected"),
    [
        (
            "date",
            "2023-11-30T13:00:00",
            "30/11/2023 13:00",
            dt.datetime(2023, 11, 30, 13),
        ),
        (
            "date",
            "2023-11-30T13:00:00.12346",
            "30/11/2023 13:00",
            dt.datetime(2023, 11, 30, 13, 0, 0, 123_460),
        ),
        ("time", "PT128H45M00S", "128:45:00", dt.timedelta(hours=128, minutes=45)),
        ("time", "PT12H30M15.5S", "12:30:15", dt.time(12, 30, 15, 500_000)),
        ("time", "-PT01H30M00S", "-01:30:00", dt.timedelta(hours=-1, minutes=-30)),
        # a date-time shown in a time-only format: LibreOffice saves it as
        # the duration since 1899
        ("time", "PT1086253H00M00S", "13:00:00", dt.timedelta(hours=1_086_253)),
        ("time", "PT09H30M00S", "09:30:00", dt.time(9, 30)),
    ],
)
def test_a_sheet_holding_a_date_time_or_a_long_duration_loads(tmp_path, value_type, raw, text, expected):
    # regression (issue #4): one such cell made loading the whole sheet raise
    # ValueError, so not even A1 could be read
    path = _saved_with_raw_values(tmp_path, [(value_type, raw, text)])
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a value that reads is no reason to warn
        s = ODSReader(path).sheet("Sheet1")
    assert s["A1"].value == "label"
    assert s["A2"].value == expected
    assert type(s["A2"].value) is type(expected)
    assert s["A2"].text == text


def test_an_unreadable_value_reads_as_its_text_with_one_warning_for_the_sheet(tmp_path):
    path = _saved_with_raw_values(
        tmp_path,
        [
            ("date", "30/11/2023", "30/11/2023"),
            # a year has no fixed length: LibreOffice refuses it too
            ("time", "P1Y", "1 year"),
            ("float", "n/a", "n/a"),
            ("time", "PT128H45M00S", "128:45:00"),
            ("duration", "PT1H", "1:00"),  # no such value type in ODF
        ],
    )
    with pytest.warns(UserWarning, match="not readable as the declared type") as caught:
        s = ODSReader(path).sheet("Sheet1")
    (message,) = [str(w.message) for w in caught if "not readable" in str(w.message)]
    assert "sheet 'Sheet1'" in message
    assert "A2 (date value '30/11/2023'), A3 (time value 'P1Y'), A4 (float value 'n/a') and 1 more" in message
    assert s["A1"].value == "label"
    assert [s[row, 0].value for row in range(1, 6)] == [
        "30/11/2023",
        "1 year",
        "n/a",
        dt.timedelta(hours=128, minutes=45),
        "1:00",
    ]
    # the value as written, and its declared type, are still there
    assert s["A2"].raw_value == "30/11/2023"
    assert s["A2"].format == "date"
