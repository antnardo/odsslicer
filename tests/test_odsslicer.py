"""
Suite de tests pytest pour le module `odsslicer`.

Basée à l'origine sur les scripts manuels historiques du module interne,
étendue avec des cas de régression pour les bugs corrigés :
- Sheet.string_address sur les colonnes multi-lettres (>= Z)
- Sheet.get_col sur les colonnes hors bornes
- l'avertissement de lignes de longueur différente (générateur épuisé)
- empty_row / empty_col avec l'argument `slice`
- ODSReader.sheets qui doit renvoyer une liste réutilisable

Et une section dédiée à l'écriture (Cell.value = ... / ODSReader.save()).
"""

import datetime as dt
import math
import warnings
import zipfile
from decimal import Decimal
from fractions import Fraction
from typing import ClassVar

import numpy as np
import pytest
from bs4 import Tag

from conftest import (
    ENCODED_WHITESPACE,
    FIXTURES_DIR,
    PRINT_TITLE_WIDTHS_XML,
    PRINT_TITLE_XML,
    addresses_holding,
    cells_with_content,
    chart_frame,
    chart_ranges,
    document_in,
    empty_cells,
    encoded_whitespace_ods,
    formula_cell,
    note_cell,
    number_cell,
    ods_with_sheet,
    saved_table,
    shape_cell,
    table_row,
    text_cell,
    with_chart,
)
from odsslicer import ODSReader
from odsslicer.classes import ArrayValues, Border, Cell, CellStyle, NumberFormat, Sheet
from odsslicer.constants import TAG_CELL
from odsslicer.sheet import _repeat

# ---------------------------------------------------------------------------
# Sheet.address : conversion "A1" / "A1:B3" / "A:B" / "1:2" -> index/slice
# ---------------------------------------------------------------------------


def test_address_simple_cell():
    assert Sheet.address("A1") == (0, 0)
    assert Sheet.address("Z2") == (1, 25)
    assert Sheet.address("AA1") == (0, 26)


def test_address_row_only():
    assert Sheet.address("1") == 0
    assert Sheet.address("3") == 2


def test_address_col_only():
    row, col = Sheet.address("A", n_rows=5)
    assert col == 0
    assert (row.start, row.stop, row.step) == (None, 5, None)


def test_address_row_range():
    row, col = Sheet.address("A1:A10")
    assert (row.start, row.stop, row.step) == (0, 10, None)
    assert col == 0


def test_address_col_range():
    row, col = Sheet.address("A1:C1")
    assert row == 0
    assert (col.start, col.stop, col.step) == (0, 3, None)


def test_address_box_range():
    row, col = Sheet.address("B2:C5")
    assert (row.start, row.stop, row.step) == (1, 5, None)
    assert (col.start, col.stop, col.step) == (1, 3, None)


def test_address_single_cell_range_collapses():
    row, col = Sheet.address("A1:A1")
    assert row == 0 and col == 0


def test_address_rows_range_only():
    assert Sheet.address("1:2") == slice(0, 2)


@pytest.mark.parametrize("bad", ["1A", "A1=", "A:2", "2:A", "B:A"])
def test_address_invalid_raises(bad):
    with pytest.raises(ValueError):
        Sheet.address(bad)


# ---------------------------------------------------------------------------
# Sheet.string_address / string_to_col : conversion index <-> lettres
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "col, expected",
    [
        (0, "A"),
        (1, "B"),
        (25, "Z"),
        (26, "AA"),
        (27, "AB"),  # regression: used to give "BB"
        (51, "AZ"),  # regression: used to give "ZZ"
        (52, "BA"),  # regression: used to give "AA"
        (53, "BB"),
        (701, "ZZ"),
        (702, "AAA"),
    ],
)
def test_string_address_columns(col, expected):
    assert Sheet.string_address(0, col) == f"{expected}1"


def test_string_address_row_is_one_indexed():
    assert Sheet.string_address(0, 0) == "A1"
    assert Sheet.string_address(9, 0) == "A10"


@pytest.mark.parametrize("col", [*range(60), 100, 300, 701, 702, 703, 728, 729, 1000])
def test_string_address_round_trips_through_string_to_col(col):
    letters = Sheet.string_address(0, col)[:-1]
    assert Sheet.string_to_col(letters) == col


def test_string_to_col_matches_docstring_examples():
    assert Sheet.string_to_col("A") == 0
    assert Sheet.string_to_col("Z") == 25
    assert Sheet.string_to_col("AA") == 26
    assert Sheet.string_to_col("AZ") == 51
    assert Sheet.string_to_col("BA") == 52


# ---------------------------------------------------------------------------
# ODSReader / Sheet : accès aux cellules sur TEST.ods, feuille Sheet1
# ---------------------------------------------------------------------------


def test_reader_lists_sheet_names(reader):
    assert set(reader.sheets_names) >= {
        "Sheet1",
        "Sheet2Repeat",
        "SheetEmpty",
        "SheetFusion",
    }


def test_sheets_property_returns_a_reusable_list(reader):
    # regression: used to be a one-shot generator without len()/reuse support
    sheets = reader.sheets
    assert isinstance(sheets, list)
    assert len(sheets) == len(reader.sheets_names)
    assert len(reader.sheets) == len(sheets)  # can be consumed more than once


def test_sheet_unknown_name_raises(reader):
    with pytest.raises(KeyError):
        reader.sheet("DoesNotExist")


def test_sheet_is_cached_between_calls(reader):
    assert reader.sheet("Sheet1") is reader.sheet("Sheet1")


def test_indexing_equivalences(sheet1):
    assert sheet1["A1"] == sheet1[0, 0]
    assert sheet1["1"] == sheet1[0]
    assert sheet1["A"] == sheet1[:, 0]
    assert sheet1["A1:B3"] == sheet1[0:3, 0:2]


def test_empty_row_slice_is_empty(sheet1):
    assert len(sheet1[1:1]) == 0


def test_cell_values_and_formats(sheet1):
    assert sheet1["A1"].value == "texte simple"
    assert sheet1["B1"].value == "seconde colonne"
    assert sheet1["A2"].value == 3.4
    assert sheet1["A3"].value == 3
    assert sheet1["A4"].value == "3/2"
    assert sheet1["A5"].value == 6.4 and sheet1["A5"].is_formula
    assert sheet1["A6"].value == 2
    assert sheet1["A7"].value == 2
    assert sheet1["A8"].value == dt.date(2021, 2, 28)
    assert sheet1["A9"].value == dt.time(15, 0, 0)


def test_cell_out_of_range_is_empty_not_an_error(sheet1):
    c = sheet1["ZZZ100000"]
    assert c.value is None
    assert c.is_empty is True


def test_cell_repr_and_str(sheet1):
    cell = sheet1["A1"]
    assert repr(cell).startswith("Cell(")
    assert str(cell) == "texte simple"


def test_cell_dunder_numeric_methods(sheet1):
    cell = sheet1["A2"]  # value == 3.4
    assert int(cell) == 3
    assert float(cell) == 3.4
    assert round(cell, 1) == 3.4
    assert abs(cell) == 3.4
    assert -cell == -3.4
    assert +cell == 3.4
    assert math.trunc(cell) == 3
    assert math.ceil(cell) == 4
    assert math.floor(cell) == 3  # regression: __floot__ typo used to be dead code


def test_cell_comparisons_compare_values(sheet1):
    # A2 == 3.4, A3 == 3.0 (comparisons only make sense between defined values)
    assert sheet1["A3"] < sheet1["A2"]
    assert sheet1["A2"] > sheet1["A3"]
    assert sheet1["A2"] <= sheet1["A2"]
    assert sheet1["A2"] >= sheet1["A2"]
    assert sheet1["A1"] == "texte simple"


def test_cell_address_matches_position(sheet1):
    assert sheet1["A1"].address == "A1"
    assert sheet1["B2"].address == "B2"


# ---------------------------------------------------------------------------
# ArrayValues : dimensions, to_list / to_numpy / to_vector, égalité
# ---------------------------------------------------------------------------


def test_array_values_dimension_and_size(sheet1):
    row = sheet1["A1:B1"]
    assert row.dimension == 1
    assert row.size == (2,)

    box = sheet1["A1:B3"]
    assert box.dimension == 2
    assert box.size == (3, 2)


def test_array_values_to_list(sheet1):
    assert sheet1["A1:B1"].to_list() == ["texte simple", "seconde colonne"]


def test_array_values_to_numpy(sheet_repeat):
    arr = sheet_repeat["A1:D2"].to_numpy()
    assert arr.tolist() == [[1, 1, 1, 1], [1, 1, 1, 1]]


def test_array_values_to_vector(sheet1):
    column = sheet1[0:2, 0]  # (2 x 1) shape
    vector = column.to_vector()
    assert vector.to_list() == ["texte simple", 3.4]


def test_array_values_equality_compares_values_not_identity(sheet1):
    assert sheet1["A1:B1"] == sheet1[0, 0:2]


# ---------------------------------------------------------------------------
# Lignes/colonnes répétées (compression ODS "number-rows/columns-repeated")
# ---------------------------------------------------------------------------


def test_repeated_rows_and_cols_shape(sheet_repeat):
    assert sheet_repeat.to_numpy().shape == sheet_repeat.size == (9, 6)


def test_repeated_rows_and_cols_values(sheet_repeat):
    assert sheet_repeat["A1:D2"].to_list() == [[1, 1, 1, 1], [1, 1, 1, 1]]
    assert sheet_repeat["F9"].value == 5
    assert sheet_repeat["F8"].value is None


def test_get_col_out_of_bounds_returns_empty_not_indexerror(sheet_repeat):
    # regression: get_col used to compare against n_rows instead of n_cols,
    # raising IndexError whenever n_rows > n_cols for an out-of-range column.
    col = sheet_repeat.get_col(sheet_repeat.n_cols + 3)
    assert len(col) == sheet_repeat.n_rows
    assert all(cell[0].value is None for cell in col)


# ---------------------------------------------------------------------------
# Feuille vide : toutes les cellules doivent renvoyer None avec la bonne forme
# ---------------------------------------------------------------------------


def test_empty_sheet_shapes(sheet_empty):
    assert sheet_empty["A1"].value is None
    assert sheet_empty["ZZ1"].value is None
    assert sheet_empty["ZZZ2222222"].value is None
    assert sheet_empty["B1"].value is None
    assert sheet_empty["A2"].value is None


def test_empty_sheet_ranges_have_correct_shape(sheet_empty):
    assert sheet_empty["A1:C1"].to_list() == [None] * 3
    assert sheet_empty["A1:C2"].to_list() == [[None] * 3, [None] * 3]
    assert sheet_empty[0:10:2, 0].to_list() == [[None] for _ in range(5)]
    assert sheet_empty[0:10:2, :2].to_list() == [[None, None] for _ in range(5)]
    assert sheet_empty[0, 0:5:2].to_list() == [None] * 3


# ---------------------------------------------------------------------------
# Cellules fusionnées / masquées (SheetFusion)
# ---------------------------------------------------------------------------


def test_merged_and_hidden_cells(sheet_fusion):
    assert sheet_fusion.size == (9, 4)
    assert sheet_fusion["A4"].value == 5  # hidden in cols
    assert sheet_fusion["B4"].value == 7  # not hidden
    assert sheet_fusion["C1"].value == 3  # hidden in rows
    assert sheet_fusion["C9"].value == 1  # hidden and repeated


# ---------------------------------------------------------------------------
# empty_row / empty_col : cas générique et cas avec un `slice` explicite
# ---------------------------------------------------------------------------


def test_empty_row_default(sheet1):
    row = sheet1.empty_row(0)
    assert len(row) == sheet1.n_cols
    assert all(cell.is_empty for cell in row)


def test_empty_col_default(sheet1):
    col = sheet1.empty_col(0)
    assert len(col) == sheet1.n_rows
    assert all(cell[0].is_empty for cell in col)


def test_empty_row_with_slice(sheet1):
    # regression: the slice branch used to recompute a *count* and pass it
    # to range() as a *stop* index, silently dropping one element.
    row = sheet1.empty_row(0, start=2, slice=slice(2, 10))
    assert len(row) == 8


def test_empty_col_with_slice(sheet1):
    col = sheet1.empty_col(0, start=2, slice=slice(2, 10))
    assert len(col) == 8


# ---------------------------------------------------------------------------
# Avertissement "lignes de longueurs différentes" (Sheet.__init__)
# ---------------------------------------------------------------------------


class _FakeTag(dict):
    """Minimal stand-in for the BeautifulSoup tag Sheet.__init__ expects."""

    attrs: ClassVar[dict] = {}

    def __getitem__(self, key):
        return {"table:name": "Fake", "table:style-name": "st"}[key]


def test_ragged_rows_trigger_warning(monkeypatch, caplog):
    # regression: rows_len was a `map` object consumed twice (once by max(),
    # once by the warning check), so the warning never actually fired.
    # (The message goes through the "odsslicer" logger at WARNING level.)
    import logging

    monkeypatch.setattr(Sheet, "load", lambda self, table_bs: [[1, 2, 3], [1, 2]])
    with caplog.at_level(logging.WARNING, logger="odsslicer"):
        sheet = Sheet(_FakeTag())
    assert sheet.size == (2, 3)
    assert any("same length" in r.message for r in caplog.records)


def test_uniform_rows_do_not_trigger_warning(monkeypatch, capsys):
    monkeypatch.setattr(Sheet, "load", lambda self, table_bs: [[1, 2], [3, 4]])
    sheet = Sheet(_FakeTag())
    assert sheet.size == (2, 2)
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


# ---------------------------------------------------------------------------
# Écriture : Cell.value = ... et ODSReader.save()
# ---------------------------------------------------------------------------


def test_write_string_float_date_time(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "nouvelle valeur"
    s["A3"].value = 42.5
    s["A8"].value = dt.date(2030, 1, 15)
    s["A9"].value = dt.time(8, 30, 0)
    assert s["A1"].value == "nouvelle valeur" and s["A1"].format == "string"
    assert s["A3"].value == 42.5 and s["A3"].format == "float"
    assert s["A8"].value == dt.date(2030, 1, 15) and s["A8"].format == "date"
    assert s["A9"].value == dt.time(8, 30, 0) and s["A9"].format == "time"


def test_write_into_previously_empty_cell(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["B2"].value is None and s["B2"].is_empty
    s["B2"].value = "nouvelle cellule"
    assert s["B2"].value == "nouvelle cellule"
    assert not s["B2"].is_empty


def test_write_clears_formula(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A5"].is_formula
    s["A5"].value = 7.0
    assert s["A5"].value == 7.0
    assert not s["A5"].is_formula
    assert s["A5"].formula is None


def test_write_preserves_percentage_and_currency_format(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A6"].format == "percentage"
    s["A6"].value = 0.5
    assert s["A6"].value == 0.5 and s["A6"].format == "percentage"

    assert s["A7"].format == "currency"
    s["A7"].value = 3.0
    assert s["A7"].value == 3.0 and s["A7"].format == "currency"


def test_write_none_clears_cell(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A4"].value = None
    assert s["A4"].value is None
    assert s["A4"].is_empty
    assert s["A4"].format is None


@pytest.mark.parametrize("flag", [True, False])
def test_write_and_read_back_boolean(writable_reader, tmp_path, flag):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = flag
    assert s["A1"].value is flag
    assert s["A1"].format == "boolean"

    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet1")
    assert reread["A1"].value is flag
    assert reread["A1"].format == "boolean"


def test_reading_a_boolean_cell_does_not_use_office_value():
    # regression: FORMATS["boolean"] used to be plain `bool`, and the reader looked
    # at `office:value` instead of the ODF-mandated `office:boolean-value` attribute
    # -> a real boolean cell always read back as False (or crashed on bool(None)).
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<table:table-cell office:value-type="boolean" office:boolean-value="true">'
        "<text:p>VRAI</text:p></table:table-cell>"
        '<table:table-cell office:value-type="boolean" office:boolean-value="false">'
        "<text:p>FAUX</text:p></table:table-cell>"
        "</root>"
    )
    from bs4 import BeautifulSoup

    tags = BeautifulSoup(xml, "xml").find_all("table:table-cell")
    true_cell, false_cell = Cell(tags[0]), Cell(tags[1])
    assert true_cell.value is True
    assert false_cell.value is False


def test_empty_text_p_reads_as_empty_string_not_the_word_none():
    # regression: bs4's `text:p.string` is None whenever text:p isn't exactly one
    # plain text node - including a genuinely empty <text:p/> (e.g. a formula
    # whose cached result is ""). The old code did `str(p.string)`, which turned
    # that None into the literal 4-character string "None".
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<table:table-cell><text:p/></table:table-cell>"
        "</root>"
    )
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(xml, "xml").find("table:table-cell")
    cell = Cell(tag)
    assert cell.text == ""
    assert str(cell) == ""


def test_reading_a_whole_empty_sheet_gives_an_empty_selection(reader):
    # regression: an empty list was taken for a single cell (dimension 0), so
    # reading every cell of a sheet with no rows - what a plain scan over a
    # workbook does - crashed with AttributeError on a real file
    s = reader.sheet("SheetEmpty")
    assert s.size == (0, 0)
    for selection in (s[:, :], s[:], s[0:0, 0:0]):
        assert selection.dimension == 1
        assert selection.size == (0,)
        assert selection.to_list() == []
        assert selection.to_numpy().shape == (0,)


def test_writing_to_an_empty_selection_does_nothing(writable_reader):
    writable_reader.sheet("SheetEmpty")[:, :].value = 1  # no cell to write to


def test_a_row_of_an_empty_sheet_keeps_its_two_dimensions(reader):
    # [[]] is a one-row selection of zero cells, not a zero-row one

    selection = ArrayValues([[]])
    assert selection.dimension == 2
    assert selection.size == (1, 0)
    assert selection.to_list() == [[]]


def test_multi_paragraph_cell_reads_every_line():
    # regression: a cell holding several lines (Ctrl+Enter in a spreadsheet) is
    # one <text:p> per line in ODF - the reader only looked at the first one,
    # silently dropping the rest of a cell LibreOffice shows in full
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
        '<table:table-cell office:value-type="string">'
        "<text:p>ligne 1</text:p><text:p>ligne 2</text:p><text:p>ligne 3</text:p>"
        "</table:table-cell></root>"
    )
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(xml, "xml").find("table:table-cell")
    cell = Cell(tag)
    assert cell.text == "ligne 1\nligne 2\nligne 3"
    assert cell.value == "ligne 1\nligne 2\nligne 3"


def test_a_comments_paragraphs_are_not_read_as_the_cells_text():
    # the cell's own paragraphs are its direct children only: an annotation
    # carries its own text:p, which must stay out of cell.text
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
        '<table:table-cell office:value-type="string">'
        "<office:annotation><text:p>note 1</text:p><text:p>note 2</text:p></office:annotation>"
        "<text:p>valeur</text:p>"
        "</table:table-cell></root>"
    )
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(xml, "xml").find("table:table-cell")
    assert Cell(tag).text == "valeur"


def test_writing_a_multi_line_value_writes_one_paragraph_per_line(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "ligne 1\nligne 2\nligne 3"
    assert s["A1"].text == "ligne 1\nligne 2\nligne 3"
    # ODF has no newline *inside* a paragraph (it is plain whitespace there),
    # so each line must be its own <text:p>, the way applications write it
    assert [p.get_text() for p in s["A1"].cell.find_all("text:p", recursive=False)] == [
        "ligne 1",
        "ligne 2",
        "ligne 3",
    ]
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    assert ODSReader(out).sheet("Sheet1")["A1"].value == "ligne 1\nligne 2\nligne 3"


class TestEncodedWhitespace:
    """Spaces, tabs and line breaks ODF writes as elements (issue #27)."""

    @pytest.mark.parametrize(("row", "shown"), list(enumerate(shown for _, shown in ENCODED_WHITESPACE)))
    def test_reading_gives_the_text_libreoffice_shows(self, tmp_path, row, shown):
        sheet = ODSReader(encoded_whitespace_ods(tmp_path / "ws.ods")).sheet("Sheet1")
        assert sheet[row, 0].text == shown
        assert sheet[row, 0].value == shown

    @pytest.mark.parametrize(
        ("value", "paragraphs"),
        [
            ("a   b", ['a <text:s text:c="2"/>b']),
            ("  lead", ['<text:s text:c="2"/>lead']),
            ("trail  ", ["trail <text:s/>"]),
            (" ", ["<text:s/>"]),
            ("a\tb", ["a<text:tab/>b"]),
            ("a b", ["a b"]),
            ("x  \n  y", ["x <text:s/>", '<text:s text:c="2"/>y']),
        ],
    )
    def test_writing_encodes_runs_the_way_libreoffice_does(self, tmp_path, value, paragraphs):
        r = ODSReader.new()
        r.sheet("Sheet1")["A1"].value = value
        out = tmp_path / "out.ods"
        r.save(out)
        cell = saved_table(out).find("table:table-cell")
        assert [p.decode_contents() for p in cell.find_all("text:p")] == paragraphs
        assert ODSReader(out).sheet("Sheet1")["A1"].value == value

    def test_rewriting_a_cell_drops_its_previous_encoded_spaces(self, tmp_path):
        r = ODSReader(encoded_whitespace_ods(tmp_path / "ws.ods"))
        r.sheet("Sheet1")["A1"].value = "plain"
        out = tmp_path / "out.ods"
        r.save(out)
        cell = saved_table(out).find("table:table-cell")
        assert cell.find("text:s") is None
        assert ODSReader(out).sheet("Sheet1")["A1"].value == "plain"

    def test_a_hyperlink_keeps_the_encoded_spaces_it_wraps(self, tmp_path):
        r = ODSReader(encoded_whitespace_ods(tmp_path / "ws.ods"))
        r.sheet("Sheet1")["A1"].hyperlink = "https://example.org"
        out = tmp_path / "out.ods"
        r.save(out)
        reread = ODSReader(out).sheet("Sheet1")["A1"]
        assert (reread.hyperlink, reread.value) == ("https://example.org", "a   b")

    def test_a_comment_reads_and_writes_its_encoded_spaces(self, tmp_path):
        r = ODSReader.new()
        r.sheet("Sheet1")["A1"].comment = "two  spaces"
        out = tmp_path / "out.ods"
        r.save(out)
        assert saved_table(out).find("office:annotation").find("text:s") is not None
        assert ODSReader(out).sheet("Sheet1")["A1"].comment.text == "two  spaces"


@pytest.mark.parametrize(
    ("first", "second"),
    [("a\nb\nc", "x"), ("x", "a\nb\nc"), ("a\nb", "c\nd"), ("a\nb", "")],
)
def test_rewriting_a_cell_leaves_no_stale_paragraph(writable_reader, first, second):
    # going from more lines to fewer must drop the extra paragraphs, not keep
    # them hanging around below the new value
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = first
    s["A1"].value = second
    assert s["A1"].value == second
    assert len(s["A1"].cell.find_all("text:p", recursive=False)) == len(second.split("\n"))


def test_rich_text_with_a_span_reads_correctly_instead_of_none():
    # regression: same root cause as above, but for real (non-empty) text split
    # across several children - e.g. "1er / 20" stored as "1" + <text:span>er</text:span>
    # + " / 20", as found in a real spreadsheet (superscript "er" after a number).
    # `text:p.string` is None here too (more than one child), so the old code
    # also turned perfectly good text into the literal string "None".
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
        '<table:table-cell office:value-type="string">'
        '<text:p>1<text:span text:style-name="T1">er</text:span> / 20</text:p>'
        "</table:table-cell></root>"
    )
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(xml, "xml").find("table:table-cell")
    cell = Cell(tag)
    assert cell.text == "1er / 20"
    assert cell.value == "1er / 20"


def test_write_unsupported_type_raises_typeerror(writable_reader):
    class Foo:
        pass

    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].value = Foo()


# ---------------------------------------------------------------------------
# Values from numpy - and so pandas - or a database (issue #11): written as
# the value they stand for, as Python types; NaN is a missing value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "written", "read_back"),
    [
        pytest.param(np.int64(1250), 1250, 1250.0, id="int64"),
        pytest.param(np.uint8(7), 7, 7.0, id="uint8"),
        pytest.param(np.bool_(True), True, True, id="bool_"),
        pytest.param(np.float64(1.5), 1.5, 1.5, id="float64"),
        pytest.param(np.float32(0.5), 0.5, 0.5, id="float32"),
        pytest.param(np.longdouble(2.5), 2.5, 2.5, id="longdouble"),
        pytest.param(np.str_("x"), "x", "x", id="str_"),
        pytest.param(np.array(3), 3, 3.0, id="0-d array"),
        pytest.param(
            np.datetime64("2026-09-20"), dt.date(2026, 9, 20), dt.date(2026, 9, 20), id="datetime64[D]"
        ),
        pytest.param(
            np.datetime64("2026-09-20T10:11:12.123456789"),
            dt.datetime(2026, 9, 20, 10, 11, 12, 123456),
            dt.datetime(2026, 9, 20, 10, 11, 12, 123456),
            id="datetime64[ns]",
        ),
        # a duration, in the format of a duration (issue #20)
        pytest.param(
            np.timedelta64(90, "m"),
            dt.timedelta(minutes=90),
            dt.timedelta(minutes=90),
            id="timedelta64",
        ),
        pytest.param(
            np.timedelta64(30, "h"),
            dt.timedelta(hours=30),
            dt.timedelta(hours=30),
            id="timedelta64 past a day",
        ),
        pytest.param(Decimal("2310.50"), 2310.5, 2310.5, id="Decimal"),
        pytest.param(Fraction(1, 4), 0.25, 0.25, id="Fraction"),
    ],
)
def test_a_value_from_numpy_or_a_database_is_written_as_what_it_stands_for(
    tmp_path, value, written, read_back
):
    # regression: TypeError, but for float64 and str_, which stayed numpy's
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = value
    assert s["A1"].value == written
    assert type(s["A1"].value) is type(written)
    r.save(tmp_path / "out.ods")
    back = ODSReader(tmp_path / "out.ods").sheet("Sheet1")["A1"].value
    assert back == read_back
    assert type(back) is type(read_back)


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(np.float64("nan"), id="float64 nan"),
        pytest.param(Decimal("NaN"), id="Decimal NaN"),
        pytest.param(np.datetime64("NaT", "D"), id="datetime64 NaT"),
        pytest.param(np.timedelta64("NaT", "s"), id="timedelta64 NaT"),
    ],
)
def test_a_missing_value_leaves_the_cell_empty(writable_reader, missing):
    # regression: NaN was written as office:value="nan", which LibreOffice
    # reads as 0 - and NaT raised TypeError
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = missing
    assert s["A1"].value is None
    assert s["A1"].is_empty


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("inf"), id="inf"),
        pytest.param(-np.inf, id="-inf"),
        pytest.param(Decimal("Infinity"), id="Decimal Infinity"),
        pytest.param(np.datetime64("20000-01-01"), id="datetime64 past year 9999"),
    ],
)
def test_a_value_no_cell_can_hold_raises_valueerror_and_writes_nothing(writable_reader, value):
    s = writable_reader.sheet("Sheet1")
    before, size = s["A1"].value, s.size
    with pytest.raises(ValueError):
        s["A1"].value = value
    with pytest.raises(ValueError):
        s[size[0] + 5, 0].value = value  # past the grid, which must not grow
    assert s["A1"].value == before
    assert s.size == size


def test_a_numpy_array_or_scalar_fills_a_range(tmp_path):
    # regression: TypeError, 'numpy.int64' object is not iterable for the
    # scalar, and for each of the array's integers
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1:B2"].value = np.array([[1250, 38], [1320, 41]])
    s["C1:D2"].value = np.int64(0)
    s["E1:E2"].value = np.array(5)  # an array of no dimension, a scalar
    r.save(tmp_path / "out.ods")
    back = ODSReader(tmp_path / "out.ods").sheet("Sheet1")
    assert back["A1:E2"].to_list() == [
        [1250.0, 38.0, 0.0, 0.0, 5.0],
        [1320.0, 41.0, 0.0, 0.0, 5.0],
    ]


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
    s.get_row(50)  # read-only access must not trigger growth
    assert s.size == (9, 26)
    s["A100"].value = "far down"
    assert s.size == (100, 26)
    assert s["Z1"].value == "far right"
    assert s["A100"].value == "far down"
    assert s["A1"].value == "texte simple"


def test_write_growth_coexists_with_repeated_and_merged_cells(writable_reader):
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


def test_growing_the_only_sheet_of_a_document_with_nothing_to_copy_from():
    # regression: growing a fully empty sheet that is the *only* sheet in the
    # whole document (nothing elsewhere to fall back on as a namespace
    # template) used to fail two different ways:
    # 1. Sheet.grow_to discarded the sheet's own lone "phantom" blank row
    #    *before* using it as a row/cell template, leaving nothing to copy.
    # 2. Writing a string value needs a text:p template, and a sheet this
    #    minimal (a single bare <table:table-cell/>, no text:p anywhere at
    #    all) has none anywhere in the document either.
    # Both now fall back to building a correctly-namespaced tag from scratch
    # (_new_qualified_tag) instead of raising NotImplementedError.
    from bs4 import BeautifulSoup

    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<table:table table:name="Sheet1">'
        "<table:table-column/><table:table-row><table:table-cell/></table:table-row>"
        "</table:table></root>"
    )
    table = BeautifulSoup(xml, "xml").find("table:table")
    sheet = Sheet(table)
    assert sheet.size == (0, 0)

    sheet["A1"].value = "hello"
    sheet["C3"].value = 42
    assert sheet.size == (3, 3)
    assert sheet["A1"].value == "hello"
    assert sheet["C3"].value == 42


def test_save_round_trip_after_growth(writable_reader, tmp_path):
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


def test_write_column_repeated_cell_splits_the_block(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    assert s.size == (9, 6)
    s["B1"].value = 999
    assert s["B1"].value == 999
    # siblings that shared the same compressed XML element keep their original value
    assert s["A1"].value == 1.0
    assert s["C1"].value == 1.0
    assert s["D1"].value == 1.0
    # a different row using its own, separate col-repeat block is unaffected
    assert s["A2"].value == 1.0 and s["B2"].value == 1.0
    assert s.size == (9, 6)


def test_write_row_and_column_repeated_cell_splits_both_layers(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["C5"].value = 42
    assert s["C5"].value == 42
    # rest of row 5 preserved
    for addr in ("A5", "B5", "D5", "E5", "F5"):
        assert s[addr].value is None
    # other rows that were part of the same repeated-row block are untouched
    for addr in ("A3", "B3", "A4", "B4", "A6", "A7", "A8"):
        assert s[addr].value is None
    assert s.size == (9, 6)


def test_write_already_individual_cell_is_a_no_op_split(writable_reader):
    # F9 has no repeat/merge attributes at all: baseline sanity check
    s = writable_reader.sheet("Sheet2Repeat")
    assert s["F9"].value == 5.0
    s["F9"].value = 6.0
    assert s["F9"].value == 6.0


def test_write_sequential_cells_in_the_same_former_repeat_block(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["A1"].value = "a"
    s["C1"].value = "c"
    assert s["A1"].value == "a" and s["C1"].value == "c"
    assert s["B1"].value == 1.0 and s["D1"].value == 1.0

    s["C3"].value = "x"
    s["C6"].value = "y"
    assert s["C3"].value == "x" and s["C6"].value == "y"
    assert s["A3"].value is None and s["A6"].value is None
    assert s.size == (9, 6)


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
    assert s["A1"].attrs.get("table:number-columns-spanned") is None
    assert s["B1"].value == 2.0  # other covered sibling reveals its residual value
    assert s.size == (9, 4)


def test_write_vertical_merge_covered_cell(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s["A5"].value = "bas de fusion verticale"
    assert s["A5"].value == "bas de fusion verticale"
    assert s["A3"].value == "hidden as col, 5, 6"
    assert s["A3"].attrs.get("table:number-rows-spanned") is None
    assert s["A4"].value == 5.0


def test_write_rectangular_merge_covered_cell(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s["C7"].value = "coin de fusion rectangulaire"
    assert s["C7"].value == "coin de fusion rectangulaire"
    assert s["A6"].value == "Hidden empty"
    assert s["A6"].attrs.get("table:number-rows-spanned") is None
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
    assert s["A8"].attrs.get("table:number-rows-spanned") is None
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
# Runs of repeated rows (issue #5) - every row of a run points at the same
# `<table:table-row>` element, so reshaping one row has to reach them all.
# The damage only shows once saved: read back from the file.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "values", "written"),
    [
        # past the run's two columns: every row of the run got D = 1, F = 2
        ("D2:D4", [[1], [2], [3]], {"D2": 1.0, "D3": 2.0, "D4": 3.0}),
        ("C6", 4, {"C6": 4.0}),
        (
            "B3:E4",
            [[1, 2, 3, 4], [5, 6, 7, 8]],
            {"B3": 1, "C3": 2, "D3": 3, "E3": 4, "B4": 5, "C4": 6, "D4": 7, "E4": 8},
        ),
        # inside them, which already worked
        ("B2:B4", [[1], [2], [3]], {"B2": 1.0, "B3": 2.0, "B4": 3.0}),
    ],
)
def test_writing_into_a_run_of_repeated_rows_changes_only_the_written_cells(
    repeated_run_ods, address, values, written
):
    r = ODSReader(repeated_run_ods)
    r.sheet("Sheet1")[address].value = values
    r.save()
    s = ODSReader(repeated_run_ods).sheet("Sheet1")
    assert cells_with_content(s) == {"A1": "a", "B1": "b", "A7": "end", **written}


def test_writing_past_the_width_of_a_libreoffice_run_of_repeated_rows(writable_reader, tmp_path):
    # Sheet2Repeat, saved by LibreOffice: rows 3 to 8 are one element whose
    # cells are one repeated element too, and F9 comes after them
    s = writable_reader.sheet("Sheet2Repeat")
    before = cells_with_content(s)
    s["G3:G5"].value = [[1], [2], [3]]
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet2Repeat")
    assert reread.size == (9, 7)
    assert cells_with_content(reread) == {**before, "G3": 1.0, "G4": 2.0, "G5": 3.0}


def test_growing_a_run_of_repeated_rows_widens_its_element_once(repeated_run_ods):
    # the shared element used to get the new cells once per row of the run
    s = ODSReader(repeated_run_ods).sheet("Sheet1")
    s.grow_to(0, 4)
    run = s.rows[1][0].cell.parent
    assert run.attrs["table:number-rows-repeated"] == "5"
    cells = run.find_all(TAG_CELL)
    assert sum(_repeat(c, "table:number-columns-repeated") for c in cells) == 5
    # one new cell for the whole run, as load() would have it
    assert all(s.rows[r][4].cell is s.rows[1][4].cell for r in range(2, 6))


@pytest.mark.parametrize(
    ("col", "expected"),
    [(0, {"A1": "b"}), (1, {"A1": "a", "A7": "end"})],
)
def test_deleting_a_column_through_a_run_of_repeated_cells(repeated_run_ods, col, expected):
    # regression: ValueError, the run's repeated cell being split once per
    # row of the run - the second time once it had left the tree
    r = ODSReader(repeated_run_ods)
    r.sheet("Sheet1").delete_column(col)
    r.save()
    assert cells_with_content(ODSReader(repeated_run_ods).sheet("Sheet1")) == expected


def test_deleting_a_column_of_a_libreoffice_sheet_with_repeated_rows(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet2Repeat")
    s.delete_column(2)
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet2Repeat")
    assert reread.size == (9, 5)
    ones = {f"{col}{r}": 1.0 for col in "ABC" for r in (1, 2)}
    assert cells_with_content(reread) == {**ones, "E9": 5.0}


@pytest.mark.parametrize("address", ["A3", "B5", "A6", "A7", "C9"])
def test_writing_into_or_below_a_trailing_run_of_empty_rows(tmp_path, address):
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


# ---------------------------------------------------------------------------
# What load() leaves out of the grid (issue #6): only the padding after the
# data, never empty rows or columns between it - and what it leaves out
# stays in the file, where writing past the data must not land beyond it.
# ---------------------------------------------------------------------------

_GAP_COLUMNS_XML = '<table:table-column table:number-columns-repeated="26"/>' + table_row(
    text_cell("a"), empty_cells(24), text_cell("z")
)


def test_empty_columns_between_data_stay_in_the_grid(tmp_path):
    # regression: a run of more than 10 of them was left out, moving Z1 to B1
    path = ods_with_sheet(tmp_path / "gap.ods", _GAP_COLUMNS_XML)
    s = ODSReader(path).sheet("Sheet1")
    assert s.size == (1, 26)
    assert s["B1"].value is None
    assert s["Z1"].value == "z"


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


# 15 empty columns formatted past the data, as LibreOffice writes them: left
# out of the grid, but not out of the file - D1 used to land in S1
_FORMATTED_PAST_DATA_XML = '<table:table-column table:number-columns-repeated="18"/>'
_FORMATTED_PAST_DATA_XML += "".join(
    table_row(text_cell(text), text_cell(text), text_cell(text), empty_cells(15, "yellow"))
    for text in ("x", "y")
)
_YELLOW_XML = (
    '<style:style style:name="yellow" style:family="table-cell">'
    '<style:table-cell-properties fo:background-color="#fff2cc"/></style:style>'
)


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
        for r in table.find_all("table:table-row")
        for cell in r.find_all(TAG_CELL, recursive=False)
        if cell.get("table:style-name") == style_name
    )


# ---------------------------------------------------------------------------
# What the file holds below the grid (issue #10): a shape or a chart anchored
# to a cell, a formatted cell, in rows the grid leaves out since no cell of
# theirs holds content. It stays in the file whatever the edit, where
# LibreOffice would have it. A note is content: the grid reaches it.
# ---------------------------------------------------------------------------


def _data_then_row_5(*cells):
    """1, 2, 3 in A1:A3, row 4 empty and `cells` in row 5, as LibreOffice
    writes a sheet whose last row holds no content: no filler after it."""
    return (
        '<table:table-column table:number-columns-repeated="3"/>'
        + "".join(table_row(text_cell(str(v)), empty_cells(2)) for v in (1, 2, 3))
        + table_row(empty_cells(3))
        + table_row(*cells)
    )


def _is_shape(cell):
    return cell.find("draw:rect", recursive=False) is not None


def _is_yellow(cell):
    return cell.get("table:style-name") == "yellow"


def _is_note(cell):
    return cell.find("office:annotation", recursive=False) is not None


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


# edit -> where a shape anchored in B2, inside the data, ends up. LibreOffice
# keeps it at the same address when the row or the column it is anchored in
# goes, rather than deleting it with them (issue #24).
_IN_GRID_EDITS = {
    "delete_rows([1]) (its own row)": (lambda s: s.delete_rows([1]), "B2"),
    "delete_rows([0, 1]) (its row among others)": (lambda s: s.delete_rows([0, 1]), "B1"),
    "delete_column(1) (its own column)": (lambda s: s.delete_column(1), "B2"),
    "delete_rows([2]) (another row)": (lambda s: s.delete_rows([2]), "B2"),
    "delete_column(0) (another column)": (lambda s: s.delete_column(0), "A2"),
}


@pytest.mark.parametrize("edit", list(_IN_GRID_EDITS))
def test_a_shape_inside_the_data_survives_the_deletion_of_its_row_or_column(tmp_path, edit):
    # regression (issue #24): a shape, a chart or an image is a child of the
    # cell it is anchored to, so deleting that cell's row or column deleted it
    # with them - a report template lost its chart on a plain delete_rows
    change, shape_at = _IN_GRID_EDITS[edit]
    xml = '<table:table-column table:number-columns-repeated="3"/>' + "".join(
        table_row(text_cell(str(v)), shape_cell("Box") if v == 2 else empty_cells(), empty_cells())
        for v in (1, 2, 3)
    )
    path = ods_with_sheet(tmp_path / "shape-in-grid.ods", xml)
    r = ODSReader(path)
    sheet = r.sheet("Sheet1")
    assert addresses_holding(saved_table(path), _is_shape) == ["B2"]
    change(sheet)
    r.save()
    assert addresses_holding(saved_table(path), _is_shape) == [shape_at]


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


def test_inserted_columns_take_the_width_of_the_column_they_push_right(writable_reader):
    # issue #25: they took the default width, where LibreOffice repeats the
    # definition of the column they push right - its width, its visibility,
    # the default cell style of a column formatted as a whole
    s = writable_reader.sheet("Sheet1")
    s.column_style(1).width = "5cm"
    s.insert_columns(1, 2)
    assert [s.column_style(c).width for c in (1, 2, 3)] == ["5cm", "5cm", "5cm"]
    assert s.column_style(0).width != "5cm"


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
    assert addresses_holding(saved_table(path), _is_note) == [Sheet.string_address(0, gap + 1)]


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


def _count_index_lookups(monkeypatch, name):
    """The elements named `name` looked up with bs4's `Tag.index`, a scan
    from the parent's first child, from now on."""
    scanned = []
    index = Tag.index

    def counting_index(self, element):
        if getattr(element, "name", None) == name:
            scanned.append(element)
        return index(self, element)

    monkeypatch.setattr(Tag, "index", counting_index)
    return scanned


def test_writing_a_column_past_the_data_never_scans_the_table_for_a_row(monkeypatch):
    # regression (0.14.0 to 0.14.3): each new row went in with bs4's
    # insert_after, whose parent.index() scans the table from its first row,
    # and writing a column past the data grows the sheet one row per cell -
    # quadratic: generate+save of benchmarks/bench.py took 246 s at 100,000 rows, not 32 s
    scanned = _count_index_lookups(monkeypatch, "table-row")
    sheet = ODSReader.new().sheet("Sheet1")
    sheet[0:200, 0].value = [[float(i)] for i in range(200)]
    assert sheet.size == (200, 1)
    assert scanned == []


@pytest.mark.parametrize("row", [0, 100, 200])
def test_inserting_rows_scans_the_table_for_a_row_at_most_once(monkeypatch, row):
    # regression: each new row went in with bs4's insert_before or
    # insert_after, whose parent.index() scans the table from its first row -
    # insert_rows(n, n) on n rows took 8.2 s at 20,000 rows, not 0.25 s
    sheet = ODSReader.new().sheet("Sheet1")
    sheet[0:200, 0].value = [[float(i)] for i in range(200)]
    scanned = _count_index_lookups(monkeypatch, "table-row")
    sheet.insert_rows(row, 200)
    assert len(scanned) <= 1
    values = [float(i) for i in range(200)]
    assert sheet.to_list() == [[v] for v in values[:row] + [None] * 200 + values[row:]]


@pytest.mark.parametrize("col", [0, 50, 100])
def test_inserting_columns_scans_each_row_for_a_cell_at_most_once(monkeypatch, col):
    # same within each row, one scan per new cell: insert_columns(1000, 1000)
    # on 200 rows of 1,000 columns took 6.7 s, not 2.9 s
    sheet = ODSReader.new().sheet("Sheet1")
    sheet[0:20, 0:100].value = [[float(c) for c in range(100)] for _ in range(20)]
    scanned = _count_index_lookups(monkeypatch, "table-cell")
    sheet.insert_columns(col, 100)
    assert len(scanned) <= 20
    values = [float(c) for c in range(100)]
    assert sheet.to_list() == [values[:col] + [None] * 100 + values[col:]] * 20


# Splitting a repeated element looks it up once, wherever it lies and however
# many times it repeats. Regression: each copy went in with insert_after, a
# scan from the first child up to the copy before.


def test_writing_into_repeated_rows_scans_the_table_for_a_row_at_most_once(tmp_path, monkeypatch):
    # writing one cell in a run of 20,000 repeated rows below 50,000 took 23 s, not 0.36 s
    xml = "".join(table_row(number_cell(i)) for i in range(100)) + table_row(text_cell("x"), repeat=200)
    sheet = ODSReader(ods_with_sheet(tmp_path / "rows.ods", xml)).sheet("Sheet1")
    scanned = _count_index_lookups(monkeypatch, "table-row")
    sheet[200, 0].value = "y"
    assert len(scanned) <= 1
    expected = [[float(i)] for i in range(100)] + [["x"]] * 200
    expected[200] = ["y"]
    assert sheet.to_list() == expected


def test_writing_into_repeated_cells_scans_the_row_for_a_cell_at_most_once(tmp_path, monkeypatch):
    # in a cell repeated 16,384 times: 1.7 s, not 0.14 s
    repeated = (
        '<table:table-cell table:number-columns-repeated="200" office:value-type="string">'
        "<text:p>x</text:p></table:table-cell>"
    )
    xml = table_row(*(number_cell(c) for c in range(100)), repeated)
    sheet = ODSReader(ods_with_sheet(tmp_path / "cells.ods", xml)).sheet("Sheet1")
    scanned = _count_index_lookups(monkeypatch, "table-cell")
    sheet[0, 200].value = "y"
    assert len(scanned) <= 1
    expected = [float(c) for c in range(100)] + ["x"] * 200
    expected[200] = "y"
    assert sheet.to_list() == [expected]


def test_styling_a_repeated_column_scans_the_definitions_at_most_once(tmp_path, monkeypatch):
    # in a definition repeated 16,384 times, as LibreOffice pads a sheet: 1.6 s, not 0.04 s
    xml = '<table:table-column table:number-columns-repeated="200"/>' + table_row(text_cell("a"))
    sheet = ODSReader(ods_with_sheet(tmp_path / "columns.ods", xml)).sheet("Sheet1")
    scanned = _count_index_lookups(monkeypatch, "table-column")
    sheet.column_style(100).width = "3cm"
    assert len(scanned) <= 1
    assert len(sheet.table.find_all("table:table-column")) == 200
    assert [sheet.column_style(c).width for c in (99, 100, 101)] == [None, "3cm", None]


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
def test_a_shape_below_the_data_in_a_group_of_rows(tmp_path, grouped, edit, shape_at, content):
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


def test_insertions_keep_a_full_size_sheet_full_size(tmp_path):
    # rows and columns padded to 1,048,576 x 16,384 as LibreOffice and Excel
    # write them: the rows past the grid used to be skipped by column
    # insertions, and deleted by row insertions
    xml = (
        '<table:table-column table:number-columns-repeated="16384"/>'
        + table_row(text_cell("a"), empty_cells(16383))
        + table_row(empty_cells(16384), repeat=1_048_574)
        + table_row(empty_cells(16384))
    )
    path = ods_with_sheet(tmp_path / "full.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    s.insert_rows(0, 2)
    s["B10"].value = "b"  # rows 4 to 10 taken back from the filler, full width
    s.insert_columns(0, 3)
    r.save()
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == {"D3": "a", "E10": "b"}
    rows = saved_table(path).find_all("table:table-row")
    assert sum(_repeat(row, "table:number-rows-repeated") for row in rows) == 1_048_576
    widths = [
        sum(_repeat(c, "table:number-columns-repeated") for c in row.find_all(TAG_CELL, recursive=False))
        for row in rows
    ]
    # the rows taken back into the grid, from the filler, are full width too
    assert widths[3:] == [16_384] * (len(rows) - 3)


def test_inserting_columns_keeps_the_formatted_cells_past_the_data(tmp_path):
    # far from the sheet's maximum width, the rows give nothing back
    path = ods_with_sheet(tmp_path / "formatted.ods", _FORMATTED_PAST_DATA_XML, _YELLOW_XML)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_columns(0, 2)
    r.save()
    yellow = addresses_holding(saved_table(path), _is_yellow)
    # D:R moved to F:T, on both rows
    assert yellow == [Sheet.string_address(r, c) for r in (0, 1) for c in range(5, 20)]


def test_inserting_a_column_into_a_row_as_wide_as_the_sheet_keeps_the_grid(tmp_path):
    # a note in XFD, the last column: nothing lies past the grid to give
    # back, and the grid's own cells must keep their elements
    xml = '<table:table-column table:number-columns-repeated="16384"/>' + table_row(
        text_cell("a"), empty_cells(16382), note_cell("last column")
    )
    path = ods_with_sheet(tmp_path / "wide.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    assert s.size == (1, 16384)
    s.insert_columns(0)
    r.save()
    reread = ODSReader(path).sheet("Sheet1")
    assert reread.size == s.size == (1, 16385)
    assert reread[0, 1].value == "a"
    assert reread[0, 16384].comment.text == "last column"


def test_inserting_columns_keeps_the_definitions_past_the_data(tmp_path):
    # regression: the last definition gave the inserted columns back though
    # the sheet was far from its maximum width, and the columns it defines
    # lost their width
    xml = (
        '<table:table-column table:number-columns-repeated="3"/>'
        '<table:table-column table:style-name="wide" table:number-columns-repeated="15"/>'
        + table_row(text_cell("a"), text_cell("b"), text_cell("c"), empty_cells(15))
    )
    wide = (
        '<style:style style:name="wide" style:family="table-column">'
        '<style:table-column-properties style:column-width="5cm"/></style:style>'
    )
    path = ods_with_sheet(tmp_path / "wide.ods", xml, wide)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_columns(0, 2)
    r.save()
    s = ODSReader(path).sheet("Sheet1")
    styles = [s.column_style(col) for col in (4, 5, 19, 20)]
    assert [style.width if style else None for style in styles] == [None, "5cm", "5cm", None]


# ---------------------------------------------------------------------------
# Charts follow structural edits (issue #15): their ranges, in their own part
# of the package and listed on their object, and the cell their frame ends in
# ---------------------------------------------------------------------------

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


def test_save_round_trip(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "modifié"
    s["A3"].value = 42.5

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    reread_sheet = reread.sheet("Sheet1")
    assert reread_sheet["A1"].value == "modifié"
    assert reread_sheet["A3"].value == 42.5
    # untouched cells on the same sheet survive the round trip
    assert reread_sheet["A2"].value == 3.4
    assert reread_sheet["A5"].value == 6.4 and reread_sheet["A5"].is_formula


def test_save_leaves_other_sheets_untouched(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "modifié"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    fusion = reread.sheet("SheetFusion")
    assert fusion.size == (9, 4)
    assert fusion["A4"].value == 5
    assert fusion["B4"].value == 7
    assert fusion["C1"].value == 3
    assert fusion["C9"].value == 1


def test_save_keeps_odf_mimetype_convention(writable_reader, tmp_path):
    import zipfile

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        info = z.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"


def test_save_defaults_to_overwriting_source_file(writable_reader, tmp_path):
    import shutil

    copy_path = tmp_path / "inplace.ods"
    shutil.copy(writable_reader.file, copy_path)
    r = ODSReader(copy_path)
    r.sheet("Sheet1")["A1"].value = "in place"
    r.save()  # no path given -> overwrite r.file (== copy_path)

    reread = ODSReader(copy_path)
    assert reread.sheet("Sheet1")["A1"].value == "in place"


# ---------------------------------------------------------------------------
# Écriture : formatage du texte affiché appris d'un exemple existant
# ---------------------------------------------------------------------------


def test_percentage_display_text_learned_from_own_prior_state(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A6"].text == "200,00 %"
    s["A6"].value = 0.5
    assert s["A6"].value == 0.5
    assert s["A6"].text == "50,00 %"


def test_currency_display_text_learned_from_own_prior_state(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A7"].text == "2,00 €"
    s["A7"].value = 12.5
    assert s["A7"].text == "12,50 €"


def test_date_display_text_learned_from_own_prior_state(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A8"].text == "28/02/21"
    s["A8"].value = dt.date(2030, 1, 5)
    assert s["A8"].text == "05/01/30"


def test_plain_float_borrows_only_the_decimal_separator_not_the_decimal_count(writable_reader):
    # regression: naively reusing the template's decimal COUNT (as is correct for
    # percentage/currency) would round 7.25 down to "7,2" and lose precision for
    # a plain "General"-format float cell, which shows as many digits as needed.
    s = writable_reader.sheet("Sheet1")
    assert s["A2"].text == "3,4"
    s["A2"].value = 7.25
    assert s["A2"].text == "7,25"
    s["A2"].value = 7
    assert s["A2"].text == "7"


def test_date_display_text_learned_from_another_cell_in_the_sheet(writable_reader):
    # C5 has no prior date of its own: the pattern must come from A8 instead
    s = writable_reader.sheet("Sheet1")
    s["C5"].value = dt.date(2030, 1, 5)
    assert s["C5"].text == "05/01/30"


def test_boolean_display_text_falls_back_without_a_template(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = True
    assert s["C1"].text == "true"


def test_boolean_display_text_learned_from_another_cell_of_the_same_polarity(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = True
    s["C1"].cell.find("text:p").string = "VRAI"  # simulate a French-locale boolean cell
    s["C2"].value = True
    assert s["C2"].text == "VRAI"
    s["C3"].value = False  # opposite polarity has no template: falls back to default
    assert s["C3"].text == "false"


def test_number_inference_falls_through_to_the_real_format_when_it_cannot_reproduce_an_example(
    writable_reader,
):
    # if the self-consistency check fails, the inferred (learn-by-example) pattern
    # is discarded - but A6 still has a real, resolvable percentage NumberFormat of
    # its own (ce1 -> N11, 2 decimal places), so the *real format* fallback (see
    # below) renders it correctly instead of giving up to a bare str() conversion
    s = writable_reader.sheet("Sheet1")
    cell = s["A6"]
    cell.cell.find("text:p").string = "deux cents"  # not a model our regex can parse
    cell.__init__(cell.cell, row=cell.row, col=cell.col, sheet=cell.sheet)  # refresh the cache
    cell.value = 0.75
    assert cell.text == "75.00 %"


# ---------------------------------------------------------------------------
# Écriture : texte affiché - repli sur une vraie lecture du format ODF
# (plutôt qu'une heuristique par apprentissage) quand aucun exemple n'existe
# ---------------------------------------------------------------------------


def _blank_document():
    # a document freshly created with ODSReader.new() has exactly one
    # cell, blank - genuinely nothing anywhere for _infer_*_display to
    # learn from, unlike any sheet within TEST.ods (find_previous/
    # find_next search the *whole* document, so even an unrelated
    # sheet's plain numbers can accidentally supply a matching template)
    return ODSReader.new()


def test_number_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="$", grouping=True)
    s["A1"].style.number_format = fmt  # no prior .value write - a truly virgin cell
    s["A1"].value = 1234.5
    assert s["A1"].text == "1,234.50 $"


def test_percentage_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "percentage", decimal_places=1)
    s["A1"].style.number_format = fmt
    s["A1"].value = 0.256
    assert s["A1"].text == "25.6 %"


def test_date_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(
        r,
        "date",
        components=[("year", "long"), ("text", "-"), ("month", "long"), ("text", "-"), ("day", "long")],
    )
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.date(2026, 3, 5)
    assert s["A1"].text == "2026-03-05"


def test_time_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "time", components=[("hours", "long"), ("text", "h"), ("minutes", "long")])
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.time(9, 5)
    assert s["A1"].text == "09h05"


def test_display_falls_back_to_plain_conversion_with_no_example_and_no_style():
    # the ultimate fallback still applies when there's truly nothing to go on
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].value = 1234.5
    assert s["A1"].text == "1234.5"


def test_number_format_with_an_unsupported_date_component_falls_back_safely():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "date", components=[("quarter", "long"), ("text", " "), ("day", "long")])
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.date(2026, 3, 5)
    assert s["A1"].text == "2026-03-05"  # isoformat() fallback, not a partial/garbled render


# ---------------------------------------------------------------------------
# Dates, date-times and durations (issue #4)
# ---------------------------------------------------------------------------

_DMY = [
    ("day", "long"),
    ("text", "/"),
    ("month", "long"),
    ("text", "/"),
    ("year", "long"),
]
_HMS = [
    ("hours", "long"),
    ("text", ":"),
    ("minutes", "long"),
    ("text", ":"),
    ("seconds", "long"),
]
_RAW_VALUE_ATTRS = {"date": "office:date-value", "time": "office:time-value"}


def _saved_with_raw_values(tmp_path, cells):
    """Save a document with a plain label in A1, then from A2 down one cell
    per `(value type, raw value, displayed text)`, written as another
    application would have - odsslicer's own writer produces few of these."""
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = "label"
    for row, (value_type, raw, text) in enumerate(cells, start=1):
        cell = s[row, 0]
        cell.value = text
        cell.attrs["office:value-type"] = value_type
        cell.attrs[_RAW_VALUE_ATTRS.get(value_type, "office:value")] = raw
    path = tmp_path / "raw.ods"
    r.save(path)
    return path


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


@pytest.mark.parametrize(
    ("value", "raw"),
    [
        (dt.datetime(2023, 11, 30, 13), "2023-11-30T13:00:00"),
        # a date-time even at midnight
        (dt.datetime(2023, 11, 30), "2023-11-30T00:00:00"),
        # the microseconds used to be dropped
        (dt.time(12, 30, 15, 500_000), "PT12H30M15.5S"),
        (dt.timedelta(hours=128, minutes=45), "PT128H45M00S"),
        (dt.timedelta(hours=-1, minutes=-30), "-PT01H30M00S"),
    ],
)
def test_a_written_date_time_or_duration_round_trips(tmp_path, value, raw):
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = value
    assert s["A1"].raw_value == raw
    assert s["A1"].value == value
    out = tmp_path / "out.ods"
    r.save(out)
    reread = ODSReader(out).sheet("Sheet1")
    assert reread["A1"].value == value
    assert type(reread["A1"].value) is type(value)
    assert reread["A1"].format == s["A1"].format


def test_a_duration_written_into_a_time_of_day_cell_reads_as_a_time_of_day(writable_reader):
    # .value is at once what reading the cell back gives: see odsslicer/datetimes.py
    s = writable_reader.sheet("Sheet1")
    s["A9"].value = dt.timedelta(hours=9, minutes=30)  # A9: HH:MM:SS, kept
    assert s["A9"].raw_value == "PT09H30M00S"
    assert s["A9"].value == dt.time(9, 30)


@pytest.mark.parametrize(
    ("value", "read"),
    [
        (dt.timedelta(hours=7, minutes=30), dt.timedelta(hours=7, minutes=30)),
        (dt.time(7, 30), dt.time(7, 30)),
    ],
)
def test_a_value_under_a_day_reads_back_as_the_kind_written(tmp_path, value, read):
    # regression: a duration under a day read back as a time of day - the
    # format it gets now, [HH]:MM:SS, tells it apart (issue #20)
    r = ODSReader.new()
    r.sheet("Sheet1")["A1"].value = value
    assert r.sheet("Sheet1")["A1"].value == read
    r.save(tmp_path / "out.ods")
    back = ODSReader(tmp_path / "out.ods").sheet("Sheet1")["A1"].value
    assert back == read
    assert type(back) is type(read)


_ELAPSED_STYLES = (
    '<number:time-style style:name="Nelapsed" number:truncate-on-overflow="false">'
    '<number:hours number:style="long"/><number:text>:</number:text>'
    '<number:minutes number:style="long"/></number:time-style>'
    '<number:time-style style:name="Nclock"><number:hours number:style="long"/>'
    '<number:text>:</number:text><number:minutes number:style="long"/></number:time-style>'
    '<style:style style:name="elapsed" style:family="table-cell" style:data-style-name="Nelapsed"/>'
    '<style:style style:name="clock" style:family="table-cell" style:data-style-name="Nclock"/>'
    '<style:style style:name="child" style:family="table-cell" style:parent-style-name="elapsed"/>'
)


def _time_cell(style=None):
    style_attr = f' table:style-name="{style}"' if style else ""
    return (
        f'<table:table-cell{style_attr} office:value-type="time" office:time-value="PT07H30M00S">'
        "<text:p>07:30</text:p></table:table-cell>"
    )


def test_copying_a_time_of_day_onto_a_duration_keeps_it_a_time_of_day():
    # the copy takes the source's format before its value, which reads back
    # through it: the target's [HH]:MM:SS made 09:00 a duration
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = dt.time(9)
    s["B1"].value = dt.timedelta(hours=26)
    s.copy("A1", "B1")
    assert s["B1"].value == dt.time(9)


def test_a_time_value_reads_as_its_format_says(tmp_path):
    # PT07H30M: a duration in a format counting time in full, [HH]:MM, a
    # time of day otherwise - as LibreOffice saves both
    xml = (
        '<table:table-column table:number-columns-repeated="4"/>'
        '<table:table-column table:default-cell-style-name="elapsed"/>'
        + table_row(
            _time_cell("elapsed"), _time_cell("clock"), _time_cell(), _time_cell("child"), _time_cell()
        )
    )
    s = ODSReader(ods_with_sheet(tmp_path / "times.ods", xml, _ELAPSED_STYLES)).sheet("Sheet1")
    duration, clock = dt.timedelta(hours=7, minutes=30), dt.time(7, 30)
    # its own format, a time of day's, none, its parent's, its column's
    assert [s[0, col].value for col in range(5)] == [duration, clock, clock, duration, duration]


@pytest.mark.parametrize(
    ("change", "address", "expected"),
    [
        pytest.param(
            lambda s: setattr(s["B1"].style, "number_format", "Nelapsed"),
            "B1",
            dt.timedelta(hours=7, minutes=30),
            id="number_format made [HH]:MM",
        ),
        pytest.param(
            lambda s: setattr(s["A1"].style, "number_format", "Nclock"),
            "A1",
            dt.time(7, 30),
            id="number_format made HH:MM",
        ),
        pytest.param(
            lambda s: setattr(s["A1"].style, "number_format", None),
            "A1",
            dt.time(7, 30),
            id="number_format cleared",
        ),
        pytest.param(
            lambda s: setattr(s["B1"], "style", s["A1"]), "B1", dt.timedelta(hours=7, minutes=30), id="style"
        ),
        pytest.param(lambda s: setattr(s["A1"], "style", None), "A1", dt.time(7, 30), id="style cleared"),
    ],
)
def test_a_time_value_reads_as_its_new_format_says(tmp_path, change, address, expected):
    # regression: the value kept the type its first format gave it until the
    # file was saved and read again
    xml = table_row(_time_cell("elapsed"), _time_cell("clock"))
    s = ODSReader(ods_with_sheet(tmp_path / "times.ods", xml, _ELAPSED_STYLES)).sheet("Sheet1")
    change(s)
    assert s[address].value == expected
    assert type(s[address].value) is type(expected)


def test_an_aware_datetime_is_written_in_utc(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A8"].value = dt.datetime(2023, 11, 30, 13, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    assert s["A8"].raw_value == "2023-11-30T08:00:00"
    assert s["A8"].value == dt.datetime(2023, 11, 30, 8)


def test_date_time_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(r, "date", components=[*_DMY, ("text", " "), *_HMS])
    s["A1"].value = dt.datetime(2023, 11, 30, 13, 5, 9)
    assert s["A1"].text == "30/11/2023 13:05:09"


def test_a_date_in_a_date_time_format_displays_as_its_midnight():
    # used to raise AttributeError: a date has no .hour to render
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(r, "date", components=[*_DMY, ("text", " "), *_HMS])
    s["A1"].value = dt.date(2023, 11, 30)
    assert s["A1"].text == "30/11/2023 00:00:00"


@pytest.mark.parametrize(
    ("elapsed", "value", "text"),
    [
        # a clock format wraps around the day
        (False, dt.timedelta(hours=128, minutes=45), "08:45:00"),
        (False, dt.timedelta(hours=-1, minutes=-30), "22:30:00"),
        # [HH]:MM:SS counts every hour
        (True, dt.timedelta(hours=128, minutes=45), "128:45:00"),
        (True, dt.timedelta(hours=-1, minutes=-30), "-01:30:00"),
    ],
)
def test_duration_display_follows_the_real_format_like_libreoffice(elapsed, value, text):
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "time", components=_HMS)
    if elapsed:  # NumberFormat.create has no switch for it
        fmt._tag.attrs["number:truncate-on-overflow"] = "false"
    s["A1"].style.number_format = fmt
    s["A1"].value = value
    assert s["A1"].text == text


def test_an_elapsed_minutes_format_counts_the_hours_in_the_minutes():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "time", components=[("minutes", "long"), ("text", ":"), ("seconds", "long")])
    fmt._tag.attrs["number:truncate-on-overflow"] = "false"
    s["A1"].style.number_format = fmt
    # within a day, so it reads back as a time of day
    s["A1"].value = dt.timedelta(hours=2, minutes=5, seconds=3)
    assert s["A1"].text == "125:03"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.datetime(2023, 11, 30, 13), "2023-11-30 13:00:00"),
        (dt.timedelta(hours=128, minutes=45), "128:45:00"),
        (dt.timedelta(hours=-1, minutes=-30), "-01:30:00"),
    ],
)
def test_date_time_and_duration_display_fall_back_to_a_plain_rendering(value, text):
    # a cell formatted for numbers keeps its format, as it would in
    # LibreOffice, and no date or time layout applies to it
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(r, "number", decimal_places=2)
    s["A1"].value = value
    assert s["A1"].text == text


def _date_formatted(path, *addresses):
    """The first sheet of `path`, A2 and `addresses` given a date format
    whose components nothing renders - a quarter - so that the text of
    `addresses` has to be learnt from another cell of that format, A2's."""
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "date", components=[("quarter", "long")])
    for address in ("A2", *addresses):
        s[address].style.number_format = fmt
    return s


def test_date_time_display_is_learnt_from_another_date_time_cell(tmp_path):
    path = _saved_with_raw_values(tmp_path, [("date", "2023-11-30T13:00:00", "30/11/2023 13:00")])
    s = _date_formatted(path, "A3", "A4")
    s["A3"].value = dt.datetime(2024, 1, 5, 8, 30)
    s["A4"].value = dt.date(2024, 1, 6)  # a bare date, as LibreOffice saves a midnight
    assert s["A3"].text == "05/01/2024 08:30"
    assert s["A4"].text == "06/01/2024 00:00"


def test_a_date_time_displays_as_a_date_next_to_dates(tmp_path):
    # as a date-only format shows it: the example is taken to share the cell's format
    path = _saved_with_raw_values(tmp_path, [("date", "2023-11-30", "30/11/2023")])
    s = _date_formatted(path, "A3")
    s["A3"].value = dt.datetime(2024, 1, 5, 8, 30)
    assert s["A3"].text == "05/01/2024"


@pytest.mark.parametrize(
    "values",
    [
        # LibreOffice saves a date-time falling on midnight as a bare date
        [
            dt.datetime(2023, 11, 29, 13),
            dt.date(2023, 11, 30),
            dt.datetime(2023, 11, 30, 8),
        ],
        [dt.timedelta(hours=-1), dt.time(9), dt.timedelta(hours=26)],
    ],
)
def test_sort_orders_the_types_a_date_or_time_column_mixes(values):
    # Python refuses to order a date and a date-time, or a time and a timedelta
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1:A3"].value = [[values[2]], [values[0]], [values[1]]]
    s.sort("A1:A3", by=0)
    assert [s[row, 0].value for row in range(3)] == values


def test_copy_carries_date_times_and_durations(writable_reader):
    # writing either type raised TypeError, so Sheet.copy could not carry them
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = dt.datetime(2023, 11, 30, 13)
    s["A2"].value = dt.timedelta(hours=128, minutes=45)
    s.copy("A1:A2", "C1")
    assert s["C1"].value == dt.datetime(2023, 11, 30, 13)
    assert s["C2"].value == dt.timedelta(hours=128, minutes=45)


def test_a_duration_is_written_to_every_cell_of_a_range():
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1:A3"].value = dt.timedelta(hours=30)
    assert s["A1:A3"].to_list() == [[dt.timedelta(hours=30)]] * 3


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.time(13, 45), "01:45 PM"),  # used to show 13:45 PM
        (dt.time(12, 5), "12:05 PM"),
        (dt.time(0, 30), "12:30 AM"),
        (dt.time(9, 5), "09:05 AM"),
    ],
)
def test_an_am_pm_format_counts_the_hours_on_a_12_hour_clock(value, text):
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(
        r, "time", components=[*_HMS[:3], ("text", " "), ("am-pm", "")]
    )
    s["A1"].value = value
    assert s["A1"].text == text


# ---------------------------------------------------------------------------
# A date or time written into a cell with no number format gets the one
# LibreOffice would give it if typed there (issue #7)
# ---------------------------------------------------------------------------

_DATE_AND_TIMES = [
    dt.date(2022, 3, 7),
    dt.datetime(2022, 3, 7, 13, 45, 30),
    dt.time(9, 30),
    dt.timedelta(hours=128, minutes=45),
    dt.timedelta(hours=-1, minutes=-30),
]


@pytest.mark.parametrize(
    ("language", "country", "texts"),
    [
        ("fr", "FR", ["07/03/22", "07/03/22 13:45", "09:30:00"]),
        ("en", "US", ["03/07/22", "03/07/22 01:45 PM", "09:30:00 AM"]),
        ("de", None, ["07.03.22", "07.03.22 13:45", "09:30:00"]),
        (None, None, ["2022-03-07", "2022-03-07 13:45:30", "09:30:00"]),
    ],
)
def test_a_date_or_time_written_into_an_unformatted_cell_gets_the_locale_format(
    tmp_path, language, country, texts
):
    # regression: no format at all - LibreOffice showed 44627 for the date
    # past the declared columns, and 08:45:00 for 128:45 within them
    texts = [*texts, "128:45:00", "-01:30:00"]  # durations: the same everywhere
    r = document_in(language, country)
    s = r.sheet("Sheet1")
    for row, value in enumerate(_DATE_AND_TIMES):
        s[row, 0].value = value  # in the one declared column
        s[row, 2].value = value  # past it
    assert [s[row, 0].text for row in range(5)] == texts
    assert [s[row, 2].text for row in range(5)] == texts
    out = tmp_path / "out.ods"
    r.save(out)
    reread = ODSReader(out).sheet("Sheet1")
    assert [reread[row, 2].value for row in range(5)] == _DATE_AND_TIMES
    formats = [reread[row, 2].style.number_format for row in range(5)]
    assert [f.family for f in formats] == ["date", "date", "time", "time", "time"]
    # [HH]:MM:SS counts the hours in full
    assert formats[3]._tag.get("number:truncate-on-overflow") == "false"


def test_one_format_and_one_style_serve_every_unformatted_cell(tmp_path):
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1:A3"].value = [[dt.date(2022, 3, day)] for day in (7, 8, 9)]
    assert len({s[row, 0].attrs["table:style-name"] for row in range(3)}) == 1
    out = tmp_path / "out.ods"
    r.save(out)
    # nor does the next session add a second format
    r2 = ODSReader(out)
    formats = len(r2.data.find_all("number:date-style"))
    s2 = r2.sheet("Sheet1")
    s2["A4"].value = dt.date(2022, 3, 10)
    assert len(r2.data.find_all("number:date-style")) == formats
    assert s2["A4"].style.number_format.name == s2["A1"].style.number_format.name


def test_a_cell_formatted_for_dates_keeps_its_format(writable_reader):
    s = writable_reader.sheet("Sheet1")
    before = s["A8"].style.number_format.name  # DD/MM/YY
    s["A8"].value = dt.datetime(2030, 1, 5, 8, 30)
    assert s["A8"].style.number_format.name == before
    assert s["A8"].text == "05/01/30"


def test_a_cell_formatted_for_numbers_keeps_its_format_as_in_libreoffice():
    r = _blank_document()
    s = r.sheet("Sheet1")
    eur = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€")
    s["A1"].style.number_format = eur
    s["A1"].value = dt.date(2022, 3, 7)
    assert s["A1"].style.number_format.name == eur.name


# Columns formatted as a whole, as LibreOffice writes them: the format is the
# column's default cell style, and its cells have no style of their own
_COLUMN_STYLES_XML = (
    '<number:date-style style:name="Ndmy">'
    '<number:day number:style="long"/><number:text>/</number:text>'
    '<number:month number:style="long"/><number:text>/</number:text>'
    '<number:year number:style="long"/></number:date-style>'
    '<style:style style:name="money" style:family="table-cell">'
    '<style:table-cell-properties fo:background-color="#fff2cc"/></style:style>'
    '<style:style style:name="day" style:family="table-cell"'
    ' style:data-style-name="Ndmy">'
    '<style:table-cell-properties fo:background-color="#e2efda"/></style:style>'
)
_FORMATTED_COLUMNS_XML = (
    '<table:table-column table:default-cell-style-name="money"/>'
    '<table:table-column table:default-cell-style-name="day"/>'
    + table_row(text_cell("Rate"), text_cell("Date"))
    + table_row(empty_cells(2), repeat=3)
    + table_row(text_cell("end"), empty_cells())
)


@pytest.fixture()
def formatted_columns(tmp_path):
    path = ods_with_sheet(tmp_path / "columns.ods", _FORMATTED_COLUMNS_XML, _COLUMN_STYLES_XML)
    return ODSReader(path)


def test_a_cell_reads_its_columns_default_cell_style(formatted_columns):
    s = formatted_columns.sheet("Sheet1")
    assert s["A2"].attrs.get("table:style-name") is None
    assert s["A2"].style.background_color == "#fff2cc"
    assert s["B2"].style.number_format.name == "Ndmy"


def test_a_date_written_into_a_column_formatted_for_dates_keeps_its_format(
    formatted_columns,
):
    s = formatted_columns.sheet("Sheet1")
    s["B2"].value = dt.date(2022, 3, 7)
    assert s["B2"].attrs.get("table:style-name") is None  # still the column's
    assert s["B2"].text == "07/03/2022"
    formatted_columns.save()
    reread = ODSReader(formatted_columns.file).sheet("Sheet1")
    assert reread["B2"].style.number_format.name == "Ndmy"


def test_a_date_written_into_a_formatted_column_keeps_the_columns_look(
    formatted_columns,
):
    # the column has a background but no number format: the cell gets a
    # style of its own, which has to carry the column's background
    s = formatted_columns.sheet("Sheet1")
    s["A2"].value = dt.date(2022, 3, 7)
    assert s["A2"].style.background_color == "#fff2cc"
    assert s["A2"].style.number_format.family == "date"
    assert s["A2"].text == "07/03/22"


def test_a_copy_carries_the_format_a_cell_takes_from_its_column(formatted_columns):
    # the copy used to take the source's own style only - none - which then
    # wiped the date format the target had just been given
    s = formatted_columns.sheet("Sheet1")
    s["B2"].value = dt.date(2022, 3, 7)
    s.copy("B2", "D2")
    assert s["D2"].style.number_format.name == "Ndmy"
    assert s["D2"].style.background_color == "#e2efda"


def test_sorting_a_formatted_column_gives_its_cells_no_style_of_their_own(
    formatted_columns,
):
    s = formatted_columns.sheet("Sheet1")
    s["B2:B4"].value = [[dt.date(2022, 3, day)] for day in (9, 7, 8)]
    s.sort("B2:B4", by=1)
    assert s["B2:B4"].to_list() == [[dt.date(2022, 3, day)] for day in (7, 8, 9)]
    assert all(s[r, 1].attrs.get("table:style-name") is None for r in (1, 2, 3))


def test_setting_a_style_property_keeps_the_columns_formatting(formatted_columns):
    # the fork used to start from nothing, dropping the column's background
    s = formatted_columns.sheet("Sheet1")
    s["B3"].style.bold = True
    assert s["B3"].style.bold
    assert s["B3"].style.background_color == "#e2efda"
    assert s["B3"].style.number_format.name == "Ndmy"


def test_a_date_in_a_system_format_takes_its_text_from_a_cell_libreoffice_saved(
    tmp_path,
):
    # number:format-source="language": LibreOffice shows the system's own
    # short date - 07/03/2022 on a French macOS - whatever the elements say
    styles = (
        '<number:date-style style:name="Nsystem" number:automatic-order="true"'
        ' number:format-source="language"><number:day/><number:text>/</number:text>'
        "<number:month/><number:text>/</number:text><number:year/></number:date-style>"
        '<style:style style:name="day" style:family="table-cell"'
        ' style:data-style-name="Nsystem"/>'
    )
    saved = (
        '<table:table-cell office:value-type="date" office:date-value="2022-03-07">'
        "<text:p>07/03/2022</text:p></table:table-cell>"
    )
    xml = (
        '<table:table-column table:default-cell-style-name="day"/>'
        + table_row(saved)
        + table_row(text_cell("end"))
    )
    r = ODSReader(ods_with_sheet(tmp_path / "system.ods", xml, styles))
    s = r.sheet("Sheet1")
    s["A2"].value = dt.date(2022, 3, 8)
    assert s["A2"].style.number_format.name == "Nsystem"  # kept
    assert s["A2"].text == "08/03/2022"  # not 8/3/22, as the elements read


_SYSTEM_STYLES = (
    '<number:date-style style:name="Nsystem" number:automatic-order="true"'
    ' number:format-source="language"><number:day/><number:text>/</number:text>'
    "<number:month/><number:text>/</number:text><number:year/></number:date-style>"
    '<style:style style:name="system" style:family="table-cell" style:data-style-name="Nsystem"/>'
    '<style:style style:name="child" style:family="table-cell" style:parent-style-name="system"/>'
)


def _saved_date(text, style=None):
    style_attr = f' table:style-name="{style}"' if style else ""
    return (
        f'<table:table-cell{style_attr} office:value-type="date" office:date-value="2022-03-07">'
        f"<text:p>{text}</text:p></table:table-cell>"
    )


@pytest.mark.parametrize(
    "layout",
    [
        # the formats given by the columns' default cell styles
        (
            '<table:table-column table:default-cell-style-name="system"/>'
            '<table:table-column table:default-cell-style-name="short"/>'
            + table_row(_saved_date("07/03/2022"), _saved_date("03/07/22"))
            + table_row("<table:table-cell/>", "<table:table-cell/>")
        ),
        # by the cells' own styles, one of them through its parent
        (
            '<table:table-column table:number-columns-repeated="2"/>'
            + table_row(_saved_date("07/03/2022", "child"), _saved_date("03/07/22", "short"))
            + table_row('<table:table-cell table:style-name="child"/>', "<table:table-cell/>")
        ),
    ],
    ids=["column defaults", "parent style"],
)
def test_a_date_learns_its_text_only_from_a_cell_of_its_own_format(tmp_path, layout):
    # B1, nearer to A2 than A1, shows another format: it lent its layout
    styles = _SYSTEM_STYLES + _SHORT_DATE_STYLE
    r = ODSReader(ods_with_sheet(tmp_path / "formats.ods", layout, styles))
    s = r.sheet("Sheet1")
    s["A2"].value = dt.date(2022, 3, 8)
    assert s["A2"].text == "08/03/2022"


# ---------------------------------------------------------------------------
# The text of a date or a time follows the cell's own format (issue #16): the
# names of days and months in its language, fractions of a second, a
# duration under a day
# ---------------------------------------------------------------------------

_SHORT_DATE_STYLE = (
    '<number:date-style style:name="Nshort"><number:month number:style="long"/>'
    '<number:text>/</number:text><number:day number:style="long"/>'
    "<number:text>/</number:text><number:year/></number:date-style>"
    '<style:style style:name="short" style:family="table-cell" style:data-style-name="Nshort"/>'
)


def _written_in(tmp_path, number_style, value):
    """The text odsslicer writes for `value` into A2, a cell shown with
    `number_style` - the elements of a `<number:*-style>` named N1 - in a
    French document where A1 shows a date in a short format, an example no
    text of A2's may be learnt from."""
    family = "time" if isinstance(value, (dt.time, dt.timedelta)) else "date"
    styles = (
        _SHORT_DATE_STYLE
        + number_style.replace("<style", f'<number:{family}-style style:name="N1"', 1).replace(
            "</style>", f"</number:{family}-style>"
        )
        + '<style:style style:name="own" style:family="table-cell" style:data-style-name="N1"/>'
    )
    example = (
        '<table:table-cell table:style-name="short" office:value-type="date"'
        ' office:date-value="2026-09-13"><text:p>09/13/26</text:p></table:table-cell>'
    )
    xml = (
        "<table:table-column/>" + table_row(example) + table_row('<table:table-cell table:style-name="own"/>')
    )
    r = ODSReader(ods_with_sheet(tmp_path / "formats.ods", xml, styles))
    r.sheet("Sheet1")["A2"].value = value
    return r.sheet("Sheet1")["A2"].text


_DAY_LONG = '<number:day-of-week number:style="long"/>'
_MONTH_NAME = '<number:month number:style="long" number:textual="true"/>'
_YEAR = '<number:year number:style="long"/>'


@pytest.mark.parametrize(
    ("number_style", "expected"),
    [
        # the report's, as LibreOffice shows it: NNNN, MMMM D, YYYY
        pytest.param(
            f'<style number:language="en" number:country="US">{_DAY_LONG}'
            f"<number:text>, </number:text>{_MONTH_NAME}<number:text> </number:text>"
            f"<number:day/><number:text>, </number:text>{_YEAR}</style>",
            "Sunday, September 27, 2026",
            id="en-US",
        ),
        # no language: the document's, French
        pytest.param(
            f"<style>{_DAY_LONG}<number:text> </number:text><number:day/>"
            f"<number:text> </number:text>{_MONTH_NAME}<number:text> </number:text>{_YEAR}</style>",
            "dimanche 27 septembre 2026",
            id="fr-FR",
        ),
        pytest.param(
            '<style number:language="en" number:country="US"><number:day-of-week/>'
            '<number:text>, </number:text><number:month number:textual="true"/>'
            "<number:text> </number:text><number:day/></style>",
            "Sun, Sep 27",
            id="abbreviated",
        ),
        # a month next to a day is declined in Polish, and alone is not
        pytest.param(
            f'<style number:language="pl" number:country="PL"><number:day/>'
            f"<number:text> </number:text>{_MONTH_NAME}<number:text> </number:text>{_YEAR}</style>",
            "27 września 2026",
            id="pl-PL with a day",
        ),
        pytest.param(
            f'<style number:language="pl" number:country="PL">{_MONTH_NAME}'
            f"<number:text> </number:text>{_YEAR}</style>",
            "wrzesień 2026",
            id="pl-PL alone",
        ),
        # a language without names: ISO 8601, never A1's layout
        pytest.param(
            f'<style number:language="tlh">{_DAY_LONG}<number:text> </number:text><number:day/></style>',
            "2026-09-27",
            id="unknown language",
        ),
    ],
)
def test_a_date_is_written_in_its_own_format_with_its_names(tmp_path, number_style, expected):
    # regression: the names could not be rendered, and A1's 09/13/26 lent
    # its layout: 09/27/26
    assert _written_in(tmp_path, number_style, dt.date(2026, 9, 27)) == expected


_HMS_STYLE = (
    '<number:hours number:style="long"/><number:text>:</number:text>'
    '<number:minutes number:style="long"/><number:text>:</number:text>'
)


@pytest.mark.parametrize(
    ("number_style", "value", "expected"),
    [
        # as LibreOffice shows them, through its number formatter
        pytest.param(
            '<style number:language="en" number:country="US"><number:minutes number:style="long"/>'
            '<number:text>:</number:text><number:seconds number:style="long"'
            ' number:decimal-places="2"/></style>',
            dt.timedelta(minutes=1, seconds=24, microseconds=750000),
            "01:24.75",
            id="MM:SS.00",
        ),
        pytest.param(
            f'<style number:language="en" number:country="US">{_HMS_STYLE}'
            '<number:seconds number:style="long" number:decimal-places="2"/></style>',
            dt.time(8, 15, 42, 250000),
            "08:15:42.25",
            id="HH:MM:SS.00",
        ),
        pytest.param(
            f'<style number:language="en" number:country="US">{_HMS_STYLE}'
            '<number:seconds number:style="long" number:decimal-places="1"/></style>',
            dt.timedelta(seconds=84, microseconds=750000),
            "00:01:24.8",
            id="rounded",
        ),
        # rounded but never carried into the seconds, in a clock format...
        pytest.param(
            '<style number:language="en" number:country="US"><number:minutes number:style="long"/>'
            '<number:text>:</number:text><number:seconds number:style="long"'
            ' number:decimal-places="2"/></style>',
            dt.timedelta(seconds=59, microseconds=996000),
            "00:59.99",
            id="no carry",
        ),
        # ... where an elapsed-time one rounds the whole duration
        pytest.param(
            f'<style number:language="en" number:country="US" number:truncate-on-overflow="false">'
            f'{_HMS_STYLE}<number:seconds number:style="long" number:decimal-places="2"/></style>',
            dt.timedelta(seconds=59, microseconds=996000),
            "00:01:00.00",
            id="elapsed carry",
        ),
        # the document's separator: French
        pytest.param(
            '<style><number:minutes number:style="long"/><number:text>:</number:text>'
            '<number:seconds number:style="long" number:decimal-places="2"/></style>',
            dt.timedelta(minutes=1, seconds=24, microseconds=750000),
            "01:24,75",
            id="fr-FR",
        ),
        # without decimals, seconds are cut
        pytest.param(
            f'<style number:language="en" number:country="US">{_HMS_STYLE}'
            '<number:seconds number:style="long"/></style>',
            dt.time(12, 30, 15, 500000),
            "12:30:15",
            id="cut",
        ),
    ],
)
def test_fractions_of_a_second_show_as_libreoffice_shows_them(tmp_path, number_style, value, expected):
    # regression: the fraction was dropped, 01:24 for 01:24.75
    assert _written_in(tmp_path, number_style, value) == expected


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.timedelta(hours=7, minutes=30), "07:30:00"),
        (dt.timedelta(minutes=1, seconds=23, microseconds=450000), "00:01:23"),
    ],
)
def test_a_duration_under_a_day_shows_as_a_duration(value, text):
    # regression: it was given the format of a time of day, and showed as
    # one in an American document: 07:30:00 AM
    r = document_in("en", "US")
    cell = r.sheet("Sheet1")["A1"]
    cell.value = value
    assert cell.text == text
    assert cell.style.number_format._tag.get("number:truncate-on-overflow") == "false"


# ---------------------------------------------------------------------------
# Écriture : nouvelles feuilles (ODSReader.add_sheet)
# ---------------------------------------------------------------------------


def test_add_sheet_creates_an_empty_sheet(writable_reader):
    before = list(writable_reader.sheets_names)
    s = writable_reader.add_sheet("NewSheet")
    assert writable_reader.sheets_names == [*before, "NewSheet"]
    assert s.size == (0, 0)
    assert writable_reader.sheet("NewSheet") is s  # cached, same object


def test_add_sheet_rejects_empty_name(writable_reader):
    with pytest.raises(ValueError):
        writable_reader.add_sheet("")


def test_add_sheet_rejects_duplicate_name(writable_reader):
    writable_reader.add_sheet("Dup")
    with pytest.raises(ValueError):
        writable_reader.add_sheet("Dup")


def test_add_sheet_is_writable_and_grows(writable_reader):
    s = writable_reader.add_sheet("NewSheet")
    s["A1"].value = "hello"
    s["C3"].value = 42
    assert s.size == (3, 3)
    assert s["A1"].value == "hello"
    assert s["C3"].value == 42
    assert s["B2"].value is None


def test_add_sheet_does_not_affect_existing_sheets(writable_reader):
    writable_reader.add_sheet("NewSheet")
    s1 = writable_reader.sheet("Sheet1")
    assert s1["A1"].value == "texte simple"
    assert s1.size == (9, 2)


def test_save_round_trip_after_add_sheet(writable_reader, tmp_path):
    # regression: a brand new sheet's lone blank row (needed for it to be a
    # structurally valid ODF sheet) was physically left in the XML after
    # load() discards it from the logical view; growing the sheet then
    # appended new rows *after* that still-present phantom row without
    # removing it, so a save/reload round trip surfaced it as an extra,
    # wrongly-shaped row - see also test_save_round_trip_growing_from_empty.
    s = writable_reader.add_sheet("NewSheet")
    s["A1"].value = "hello"
    s["C3"].value = 42

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    assert reread.sheets_names[-1] == "NewSheet"
    s2 = reread.sheet("NewSheet")
    assert s2.size == (3, 3)
    assert s2["A1"].value == "hello"
    assert s2["C3"].value == 42
    assert s2["B2"].value is None
    # existing sheets are untouched, and the new table sits after them in the XML
    assert reread.sheet("Sheet1")["A1"].value == "texte simple"

    import zipfile

    with zipfile.ZipFile(out) as z:
        content = z.read("content.xml").decode("utf-8")
    idx_fusion = content.index('table:name="SheetFusion"')
    idx_new = content.index('table:name="NewSheet"')
    idx_named_expr = content.index("table:named-expressions")
    assert idx_fusion < idx_new < idx_named_expr


def test_save_round_trip_growing_from_empty(writable_reader, tmp_path):
    # same phantom-row regression as above, but on a pre-existing empty sheet
    # rather than one freshly created by add_sheet.
    s = writable_reader.sheet("SheetEmpty")
    assert s.size == (0, 0)
    s["B3"].value = "from scratch"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("SheetEmpty")
    assert reread.size == (3, 2)
    assert reread["B3"].value == "from scratch"
    assert reread["A1"].value is None


# ---------------------------------------------------------------------------
# Écriture : formules (Cell.formula)
# ---------------------------------------------------------------------------


def test_write_formula_normalizes_the_of_prefix(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "=[.A2]+[.A3]"
    assert s["C1"].formula == "of:=[.A2]+[.A3]"
    assert s["C1"].is_formula

    s["C2"].formula = "[.A2]*2"  # no leading '='
    assert s["C2"].formula == "of:=[.A2]*2"

    s["C3"].formula = "of:=[.A2]-1"  # already fully prefixed
    assert s["C3"].formula == "of:=[.A2]-1"


def test_write_formula_translates_friendly_a1_syntax(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "A2+A3"
    assert s["C1"].formula == "of:=[.A2]+[.A3]"

    s["C2"].formula = "=A2+A3"  # leading '=' optional either way
    assert s["C2"].formula == "of:=[.A2]+[.A3]"


def test_write_formula_translates_absolute_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A$2+$A$3"
    assert s["C1"].formula == "of:=[.$A$2]+[.$A$3]"


def test_write_formula_translates_ranges(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "SUM(A1:A3)"
    assert s["C1"].formula == "of:=SUM([.A1:.A3])"


def test_write_formula_translates_comma_separators_to_semicolons(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "SUM(A1,A2,A3)"
    assert s["C1"].formula == "of:=SUM([.A1];[.A2];[.A3])"


def test_write_formula_preserves_commas_inside_string_literals(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = 'IF(A1="x,y",1,2)'
    assert s["C1"].formula == 'of:=IF([.A1]="x,y";1;2)'


def test_write_formula_does_not_mistake_a_function_name_for_a_cell_reference(writable_reader):
    # regression: a naive lookahead-based regex backtracks into a shorter
    # match instead of rejecting the token outright, e.g. turning "LOG10("
    # into "[.LOG1]0(" - LOG10 must be left completely untouched.
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "LOG10(A1)"
    assert s["C1"].formula == "of:=LOG10([.A1])"


def test_write_formula_bracket_syntax_is_an_escape_hatch(writable_reader):
    # a formula that already contains "[" is assumed to be hand-written in
    # ODF's own syntax and is left untouched beyond the language prefix
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "[Sheet2.A1]+1"
    assert s["C1"].formula == "of:=[Sheet2.A1]+1"


def test_write_formula_translates_sheet_qualified_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "Sheet2.A1+1"
    assert s["C1"].formula == "of:=[Sheet2.A1]+1"

    s["C2"].formula = "'My Sheet'.A1+1"
    assert s["C2"].formula == "of:=['My Sheet'.A1]+1"

    s["C3"].formula = "SUM(Sheet2.A1:A3)"
    assert s["C3"].formula == "of:=SUM([Sheet2.A1:.A3])"


# ---------------------------------------------------------------------------
# Écriture : formules paramétrées par cellule ({r}/{c}) via Cell.formula
# ---------------------------------------------------------------------------


def test_formula_template_expands_row_and_column(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "{r}+{c}"  # A1 -> row=1, col=1 (1-indexed)
    assert s["A1"].formula == "of:=1+1"
    s["B2"].formula = "{r}+{c}"  # B2 -> row=2, col=2
    assert s["B2"].formula == "of:=2+2"


def test_formula_template_supports_arithmetic(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A2"].formula = "$A{r-1}+1"  # A2 (row 2) -> references row 1
    assert s["A2"].formula == "of:=[.$A1]+1"


def test_formula_template_with_no_placeholders_is_unchanged(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "SUM(B1:B10)"
    assert s["A1"].formula == "of:=SUM([.B1:.B10])"


def test_formula_template_rejects_unsafe_expressions(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A1"].formula = '{__import__("os")}'


def test_formula_template_rejects_unknown_names(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A1"].formula = "{z+1}"


@pytest.mark.parametrize(
    "formula",
    [
        '"{"&A1&"}"',  # issue #26: a brace meant literally, around a reference
        "A{1.5}",  # a number, but not a row
        "A{r/2}",  # division: a row and a half
        "{True}",
    ],
)
def test_formula_template_refuses_what_is_not_a_row_or_column(writable_reader, formula):
    # regression (issue #26): `{"&A1&"}` parses as a Python string, so it was
    # substituted for its content - `'"{"&A1&"}"'` was silently stored as
    # `of:="&A1&"`, a different formula, where an unknown name already raised.
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError, match=r"double the braces|unsupported expression"):
        s["A1"].formula = formula


def test_formula_template_says_how_to_write_a_literal_brace(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError, match=r"double the braces"):
        s["A1"].formula = '"{"&A1&"}"'
    s["A1"].formula = '"{{"&A1&"}}"'  # the way the message points to
    assert s["A1"].formula == 'of:="{"&A1&"}"'  # escaped content is passed through as written'


def test_formula_template_double_braces_escape_a_literal_array_constant(writable_reader):
    # {{...}} (as in str.format) is the escape hatch for a literal {...} -
    # e.g. an ODF/Excel array-constant like {1,2,3}, which is not a {r}/{c}
    # placeholder. The escaped content is passed through completely
    # untouched, including its own commas (not turned into ";").
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "SUM({{1,2,3}})"
    assert s["A1"].formula == "of:=SUM({1,2,3})"


def test_formula_template_escape_content_keeps_its_own_separators(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "IF({{1;2;3}},A1,A2)"
    # the escaped array literal is untouched; the *outer* comma (a real
    # argument separator) and the A1/A2 references are still translated
    assert s["A1"].formula == "of:=IF({1;2;3};[.A1];[.A2])"


def test_formula_template_escape_combines_with_rc_placeholders(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A2"].formula = "$A{r-1}+{{1,2}}"
    assert s["A2"].formula == "of:=[.$A1]+{1,2}"


def test_formula_template_doubled_braces_around_plain_text_stay_literal(writable_reader):
    # mirrors str.format(): "{{r}}" is a literal "{r}", not the evaluated r
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "{{r}}"
    assert s["A1"].formula == "of:={r}"


# ---------------------------------------------------------------------------
# Écriture sur des sélections multi-cellules (ArrayValues.value / .formula)
# ---------------------------------------------------------------------------


def test_slice_value_broadcasts_a_scalar(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].value = 5
    assert s["A30:C30"].to_list() == [5, 5, 5]


def test_slice_value_broadcasts_a_string_without_splitting_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].value = "hi"
    assert s["A30:C30"].to_list() == ["hi", "hi", "hi"]


def test_slice_value_assigns_element_wise_for_a_1d_row(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].value = [7, 8, 9]
    assert s["A30:C30"].to_list() == [7, 8, 9]


def test_slice_value_assigns_element_wise_for_a_2d_block(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A40:B41"].value = [[1, 2], [3, 4]]
    assert s["A40:B41"].to_list() == [[1, 2], [3, 4]]


def test_slice_value_element_wise_shape_mismatch_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A30:C30"].value = [1, 2]


def test_slice_formula_broadcasts_the_same_pattern(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].formula = "SUM(B1:B10)"
    for addr in ("A30", "B30", "C30"):
        assert s[addr].formula == "of:=SUM([.B1:.B10])"


def test_slice_formula_expands_placeholders_per_cell(writable_reader):
    # the user's own motivating example: A2 references A1, A3 references A2, etc.
    s = writable_reader.sheet("Sheet1")
    s["A2:A6"].formula = "$A{r-1}+1"
    for row in range(1, 6):  # 0-indexed rows 1..5 == A2..A6
        assert s.get_cell(row, 0).formula == f"of:=[.$A{row}]+1"


def test_save_round_trip_after_slice_writes(writable_reader, tmp_path):
    # regression: a formula-only cell (no cached value/text) was wrongly
    # treated as "empty" by Cell.is_empty, so if it ended up as the sheet's
    # last row, load()'s "trim a trailing empty row" cleanup silently
    # dropped it on the next save/reload.
    s = writable_reader.sheet("Sheet1")
    s["A20:A25"].formula = "$A{r-1}+1"
    s["C1:C3"].value = [[1], [2], [3]]

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    for row in range(19, 25):
        assert reread.get_cell(row, 0).formula == f"of:=[.$A{row}]+1"
    assert reread["C1:C3"].to_list() == [[1], [2], [3]]


def test_write_formula_clears_stale_value_and_text(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A2"].value == 3.4
    s["A2"].formula = "=[.A3]*2"
    assert s["A2"].value is None
    assert s["A2"].text is None
    assert s["A2"].format is None
    assert s["A2"].is_formula


def test_write_value_clears_an_existing_formula(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A2"].formula = "=[.A3]*2"
    assert s["A2"].is_formula
    s["A2"].value = 99
    assert s["A2"].value == 99
    assert s["A2"].formula is None
    assert not s["A2"].is_formula


def test_clearing_a_formula_with_none(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A2"].formula = "=[.A3]*2"
    s["A2"].formula = None
    assert s["A2"].formula is None
    assert not s["A2"].is_formula
    assert "table:formula" not in s["A2"].attrs


def test_write_empty_formula_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A2"].formula = ""


def test_write_formula_on_a_repeated_cell_materializes_it(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["B1"].formula = "=[.A1]+1"
    assert s["B1"].is_formula
    assert s["A1"].value == 1.0  # sibling in the former repeat block untouched


def test_write_formula_beyond_the_current_extent_grows_the_sheet(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s.size == (9, 2)
    s["E5"].formula = "=[.A1]"
    assert s.size == (9, 5)
    assert s["E5"].is_formula


def test_save_round_trip_after_writing_a_formula(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "=[.A2]+[.A3]"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["C1"].formula == "of:=[.A2]+[.A3]"
    assert reread["C1"].is_formula
    assert reread["C1"].attrs == {"table:formula": "of:=[.A2]+[.A3]"}


# ---------------------------------------------------------------------------
# Écriture : lecture amicale d'une formule (Cell.formula_friendly)
# ---------------------------------------------------------------------------


def test_formula_friendly_translates_odf_syntax_back_to_a1(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "A2+A3"
    assert s["C1"].formula == "of:=[.A2]+[.A3]"
    assert s["C1"].formula_friendly == "=A2+A3"


def test_formula_friendly_preserves_absolute_markers_and_ranges(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A$2+$A$3"
    assert s["C1"].formula_friendly == "=$A$2+$A$3"

    s["C2"].formula = "SUM(A1:A3)"
    assert s["C2"].formula_friendly == "=SUM(A1:A3)"


def test_formula_friendly_translates_semicolons_back_to_commas(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "IF(A1>0,1,-1)"
    assert s["C1"].formula_friendly == "=IF(A1>0,1,-1)"


def test_formula_friendly_preserves_commas_inside_string_literals(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = 'IF(A1="x,y",1,2)'
    assert s["C1"].formula_friendly == '=IF(A1="x,y",1,2)'


def test_formula_friendly_translates_cross_sheet_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "Sheet2.A1+1"
    assert s["C1"].formula_friendly == "=Sheet2.A1+1"


def test_formula_friendly_is_none_without_a_formula(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A1"].formula is None
    assert s["A1"].formula_friendly is None


def test_formula_friendly_on_a_real_complex_formula():
    # a real formula from bareme/examples/root_init/DS1/Notes.ods (MPX4!I3):
    # of:=IF(OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1])=0;"";
    #        OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1]))
    from bs4 import BeautifulSoup

    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<table:table-cell table:formula='of:=IF(OFFSET([$Notes.$C$3];[.I$1];"
        '[.$A3]+[.$A$1])=0;"";OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1]))\'>'
        "<text:p/></table:table-cell></root>"
    )
    tag = BeautifulSoup(xml, "xml").find("table:table-cell")
    cell = Cell(tag)
    assert cell.formula_friendly == (
        '=IF(OFFSET($Notes.$C$3,I$1,$A3+$A$1)=0,"",OFFSET($Notes.$C$3,I$1,$A3+$A$1))'
    )


def test_formula_friendly_round_trips_through_a_write(writable_reader):
    # writing back what .formula_friendly reports should reproduce the same
    # ODF formula (minus the leading "=", which .formula = ... also accepts)
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "SUM(A1:A3)+$B$1"
    friendly = s["C1"].formula_friendly
    s["C2"].formula = friendly
    assert s["C2"].formula == s["C1"].formula


# ---------------------------------------------------------------------------
# Écriture : recopie d'une formule (Cell.fill_formula)
# ---------------------------------------------------------------------------


def test_fill_formula_down_a_column_shifts_relative_rows(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["B2"].formula = "$A1+1"
    s["B2"].fill_formula("B3:B10")
    for row in range(1, 10):  # 0-indexed rows 1..9 == B2..B10
        assert s.get_cell(row, 1).formula_friendly == f"=$A{row}+1"


def test_fill_formula_right_shifts_relative_columns(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].formula = "B{r}*2"  # A1 (row=1) -> "=B1*2"
    assert s["A1"].formula_friendly == "=B1*2"
    s["A1"].fill_formula("B1:D1")
    assert s["B1"].formula_friendly == "=C1*2"
    assert s["C1"].formula_friendly == "=D1*2"
    assert s["D1"].formula_friendly == "=E1*2"


def test_fill_formula_keeps_absolute_references_fixed(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A2"].formula = "$A$1*10"
    s["A2"].fill_formula(s["B2:D3"])  # 2D block target, not just a string
    for row in (1, 2):
        for col in range(1, 4):
            assert s.get_cell(row, col).formula_friendly == "=$A$1*10"


def test_fill_formula_accepts_a_single_cell_address(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A1+1"
    s["C1"].fill_formula("C1")  # no-op shift, single cell (not a range)
    assert s["C1"].formula_friendly == "=$A1+1"


def test_fill_formula_out_of_range_raises(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["B5"].formula = "$A1"
    with pytest.raises(ValueError):
        s["B5"].fill_formula(s.get_cell(3, 1))  # one row up would need row 0 -> invalid


def test_fill_formula_without_a_formula_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A1"].fill_formula("A2")


def test_save_round_trip_after_fill_formula(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["B2"].formula = "$A1+1"
    s["B2"].fill_formula("B3:B5")

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    for row, ref_row in zip(range(1, 4), (1, 2, 3), strict=True):
        assert reread.get_cell(row, 1).formula_friendly == f"=$A{ref_row}+1"


# ---------------------------------------------------------------------------
# Nouveaux fichiers (ODSReader.new())
# ---------------------------------------------------------------------------


def test_new_creates_a_single_empty_sheet():
    doc = ODSReader.new()
    assert doc.sheets_names == ["Sheet1"]
    assert doc.sheet("Sheet1").size == (0, 0)


def test_new_accepts_a_custom_initial_sheet_name():
    doc = ODSReader.new(sheet_name="Budget")
    assert doc.sheets_names == ["Budget"]
    assert doc.sheet("Budget").size == (0, 0)


def test_new_documents_are_independent_of_each_other():
    doc1 = ODSReader.new()
    doc2 = ODSReader.new()
    doc1.sheet("Sheet1")["A1"].value = "from doc1"
    assert doc2.sheet("Sheet1")["A1"].value is None


def test_new_document_supports_writing_growing_formulas_and_add_sheet():
    doc = ODSReader.new()
    s = doc.sheet("Sheet1")
    s["A1"].value = "Total"
    s["B1"].formula = "SUM(A2:A10)"
    assert s.size == (1, 2)
    assert s["B1"].formula_friendly == "=SUM(A2:A10)"

    s2 = doc.add_sheet("Data")
    s2["A1:A3"].value = [10, 20, 30]
    assert s2["A1:A3"].to_list() == [[10], [20], [30]]


def test_new_document_save_without_a_path_raises(tmp_path):
    doc = ODSReader.new()
    doc.sheet("Sheet1")["A1"].value = "x"
    with pytest.raises(ValueError):
        doc.save()


def test_new_document_save_round_trip(tmp_path):
    doc = ODSReader.new()
    s = doc.sheet("Sheet1")
    s["A1"].value = "Total"
    s["B1"].formula = "SUM(A2:A10)"
    doc.add_sheet("Data")["A1:A3"].value = [10, 20, 30]

    out = tmp_path / "brand_new.ods"
    doc.save(out)

    reread = ODSReader(out)
    assert reread.sheets_names == ["Sheet1", "Data"]
    assert reread.sheet("Sheet1")["A1"].value == "Total"
    assert reread.sheet("Sheet1")["B1"].formula_friendly == "=SUM(A2:A10)"
    assert reread.sheet("Data")["A1:A3"].to_list() == [[10], [20], [30]]


def test_new_document_regular_reader_still_defaults_save_to_its_own_file(writable_reader, tmp_path):
    # regression guard: the save()-without-path guard must only trigger for
    # documents created via ODSReader.new(), not regular file-backed ones
    import shutil

    copy_path = tmp_path / "inplace.ods"
    shutil.copy(writable_reader.file, copy_path)
    r = ODSReader(copy_path)
    r.sheet("Sheet1")["A1"].value = "still works"
    r.save()  # no path given -> overwrites r.file (== copy_path), must NOT raise
    assert ODSReader(copy_path).sheet("Sheet1")["A1"].value == "still works"


# ---------------------------------------------------------------------------
# Styles (lecture) : Cell.style, CellStyle, NumberFormat
# ---------------------------------------------------------------------------


def test_cell_with_no_style_name_has_a_blank_writable_style(reader):
    # no `table:style-name` at all doesn't mean `.style` is None anymore -
    # writing to it (see the styles-write tests below) needs a real object
    # to fork a style onto, so every property just resolves to None/False
    s = reader.sheet("Sheet1")
    assert s["A1"].attrs.get("table:style-name") is None
    style = s["A1"].style
    assert style is not None
    assert style.bold is False
    assert style.font_color is None
    assert style.number_format is None


def test_style_resolves_percentage_number_format(reader):
    s = reader.sheet("Sheet1")
    style = s["A6"].style
    assert style is not None
    nf = style.number_format
    assert nf.family == "percentage"
    assert nf.decimal_places == 2


def test_style_resolves_currency_number_format_defined_in_styles_xml(reader):
    # regression-shaped: N108 (the currency format for A7) is defined in
    # styles.xml even though the cell style (ce2) that references it lives
    # in content.xml's automatic-styles - resolution must check both files.
    s = reader.sheet("Sheet1")
    nf = s["A7"].style.number_format
    assert nf.family == "currency"
    assert nf.decimal_places == 2
    assert nf.grouping is True
    assert nf.currency_symbol == "€"


def test_style_resolves_date_format_components(reader):
    s = reader.sheet("Sheet1")
    nf = s["A8"].style.number_format
    assert nf.family == "date"
    assert nf.components == [
        ("day", "long"),
        ("text", "/"),
        ("month", "long"),
        ("text", "/"),
        ("year", "short"),
    ]


def test_style_resolves_alignment_and_raw_properties(reader):
    s = reader.sheet("SheetFusion")
    style = s["A1"].style
    assert style.vertical_align == "middle"
    assert style.horizontal_align == "center"
    assert style.cell_properties["style:vertical-align"] == "middle"
    assert style.text_properties == {}


def test_cell_style_walks_parent_inheritance_chain():
    from bs4 import BeautifulSoup

    from odsslicer.classes import CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="Default" style:family="table-cell">'
        '<style:text-properties fo:font-size="10pt"/>'
        "</style:style>"
        '<style:style style:name="Status" style:family="table-cell" '
        'style:parent-style-name="Default">'
        '<style:table-cell-properties fo:background-color="#cccccc"/>'
        "</style:style>"
        '<style:style style:name="Error" style:family="table-cell" '
        'style:parent-style-name="Status">'
        '<style:text-properties fo:font-weight="bold" fo:color="#ffffff"/>'
        "</style:style>"
        "</root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "Error")
    assert style.bold is True  # set directly on Error
    assert style.font_color == "#ffffff"  # set directly on Error
    assert style.background_color == "#cccccc"  # inherited from Status (1 hop)
    assert style.font_size == "10pt"  # inherited from Default (2 hops)


def test_cell_style_nearest_ancestor_wins_on_conflicting_property():
    from bs4 import BeautifulSoup

    from odsslicer.classes import CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="Base" style:family="table-cell">'
        '<style:table-cell-properties fo:background-color="#111111"/>'
        "</style:style>"
        '<style:style style:name="Child" style:family="table-cell" '
        'style:parent-style-name="Base">'
        '<style:table-cell-properties fo:background-color="#222222"/>'
        "</style:style>"
        "</root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "Child")
    assert style.background_color == "#222222"


def test_cell_style_font_underline_strikethrough_and_rotation():
    # real attribute names/values taken from tests/TEST.ods's styles.xml
    # (style:font-name="Liberation Sans") and the ODF spec for the rest,
    # since none of our fixture files happen to apply these to a real cell.
    from bs4 import BeautifulSoup

    from odsslicer.classes import CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties style:rotation-angle="90"/>'
        '<style:text-properties style:font-name="Liberation Sans" '
        'style:text-underline-style="solid" style:text-line-through-style="solid"/>'
        "</style:style></root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "ce1")
    assert style.font_family == "Liberation Sans"
    assert style.underline is True
    assert style.strikethrough is True
    assert style.rotation == 90


def test_cell_style_border_shorthand_applies_to_every_side():
    # real value taken from tests/TEST.ods's styles.xml (the unused "Note"
    # built-in style: fo:border="0.74pt solid #808080")
    from bs4 import BeautifulSoup

    from odsslicer.classes import CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties fo:border="0.74pt solid #808080"/>'
        "</style:style></root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "ce1")
    for border in (style.border_top, style.border_bottom, style.border_left, style.border_right):
        assert border.width == "0.74pt"
        assert border.style == "solid"
        assert border.color == "#808080"


def test_cell_style_border_specific_side_overrides_shorthand():
    from bs4 import BeautifulSoup

    from odsslicer.classes import Border, CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties fo:border="0.74pt solid #808080" '
        'fo:border-top="2.49pt solid #000000"/>'
        "</style:style></root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "ce1")
    assert style.border_top == Border("2.49pt solid #000000")
    assert style.border_bottom == Border("0.74pt solid #808080")


def test_cell_style_no_border_at_all_is_none(reader):
    s = reader.sheet("Sheet1")
    style = s["A7"].style  # ce2: has a data-style but no border/font properties
    assert style.border_top is None
    assert style.font_family is None


def test_cell_style_diagonal_none_is_not_a_border(reader):
    # regression: the literal string "none" (ODF's way of explicitly
    # cancelling a border/diagonal) must resolve to None, not Border("none")
    # - real value taken from tests/TEST.ods's unused "Note" built-in style
    style = CellStyle(reader, "Note")
    assert style.diagonal_bl_tr is None
    assert style.diagonal_tl_br is None
    assert style.background_color == "#ffffcc"


def test_cell_style_wrap_shrink_protection_and_text_position():
    from bs4 import BeautifulSoup

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties fo:wrap-option="wrap" style:shrink-to-fit="true" '
        'style:cell-protect="protected" style:writing-mode="rl-tb"/>'
        '<style:text-properties style:text-position="super 58%"/>'
        "</style:style></root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "ce1")
    assert style.wrap_text is True
    assert style.shrink_to_fit is True
    assert style.protection == "protected"
    assert style.writing_mode == "rl-tb"
    assert style.text_position == "super 58%"
    assert style.superscript is True
    assert style.subscript is False


def test_cell_style_diagonal_border_parses_like_a_regular_border():
    from bs4 import BeautifulSoup

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties style:diagonal-tl-br="1pt solid #ff0000"/>'
        "</style:style></root>"
    )
    soup = BeautifulSoup(xml, "xml")

    class StubReader:
        def _find_style(self, name, family=None):
            return soup.find("style:style", attrs={"style:name": name}) if name else None

        def _find_number_style(self, name):
            return None

    style = CellStyle(StubReader(), "ce1")
    assert style.diagonal_tl_br.color == "#ff0000"
    assert style.diagonal_bl_tr is None


# ---------------------------------------------------------------------------
# Styles (lecture) : formats de nombre conditionnels (NumberFormat.resolve)
# ---------------------------------------------------------------------------


def test_number_format_resolves_conditional_currency_by_value(reader):
    # regression-shaped, real data: N108 (A7's currency format) is negative-
    # only (red text) with a style:map switching to N108P0 (no color) for
    # value()>=0 - A7's own value is 2.0 (positive).
    s = reader.sheet("Sheet1")
    assert s["A7"].value == 2.0
    resolved = s["A7"].style.number_format
    assert resolved.name == "N108P0"
    assert resolved.font_color is None


def test_number_format_condition_and_manual_resolve(reader):
    number_tag = reader._find_number_style("N108")
    from odsslicer.classes import NumberFormat

    base = NumberFormat(number_tag, reader=reader)
    assert base.font_color == "#ff0000"
    assert [c for c, _ in base.conditions] == ["value()>=0"]

    assert base.resolve(2.0).name == "N108P0"
    assert base.resolve(2.0).font_color is None
    assert base.resolve(-5.0).name == "N108"
    assert base.resolve(-5.0).font_color == "#ff0000"
    # an unresolvable/no-condition-matching case falls back to self
    assert base.resolve("not a number").name == "N108"


# ---------------------------------------------------------------------------
# Styles (lecture) : ligne/colonne/feuille (RowStyle, ColumnStyle, TableStyle)
# ---------------------------------------------------------------------------


def test_row_style_resolves_height(reader):
    s = reader.sheet("Sheet1")
    style = s.row_style(0)
    assert style.name == "ro1"
    assert style.height == "0.452cm"
    assert style.visible is True


def test_column_style_resolves_width(reader):
    s = reader.sheet("Sheet1")
    assert s.column_style(0).width == "2.258cm"
    assert s.column_style(1).width == "4.251cm"


def test_column_style_handles_a_repeated_column_definition(reader):
    # Sheet2Repeat's single co1 column tag covers columns 0-5 via
    # table:number-columns-repeated="6"
    s = reader.sheet("Sheet2Repeat")
    assert s.column_style(3).name == "co1"
    assert s.column_style(5).name == "co1"


def test_sheet_style_resolves_table_properties(reader):
    s = reader.sheet("Sheet1")
    style = s.style
    assert style.name == "ta1"
    assert style.visible is True  # no table:tab-color set on this fixture, but resolves cleanly


def test_row_column_sheet_style_out_of_range_is_none(reader):
    s = reader.sheet("Sheet1")
    assert s.row_style(999) is None
    assert s.column_style(999) is None


def test_row_style_no_reader_is_none():
    # a Sheet built without an owning ODSReader (e.g. constructed directly
    # in a test, as elsewhere in this suite) can't resolve styles at all
    from bs4 import BeautifulSoup

    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        '<table:table table:name="T">'
        '<table:table-column/><table:table-row table:style-name="ro1">'
        "<table:table-cell/></table:table-row></table:table></root>"
    )
    table = BeautifulSoup(xml, "xml").find("table:table")
    sheet = Sheet(table)
    assert sheet.row_style(0) is None
    assert sheet.column_style(0) is None
    assert sheet.style is None


# ---------------------------------------------------------------------------
# Cellules fusionnées (lecture) : is_merged / is_merge_master / is_covered /
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
# Cellules fusionnées (écriture) : Sheet.merge / Sheet.unmerge
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


# ---------------------------------------------------------------------------
# Styles (écriture) : CellStyle
# ---------------------------------------------------------------------------


def test_setting_bold_forks_a_private_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    assert c.attrs.get("table:style-name") is None
    c.style.bold = True
    forked_name = c.attrs.get("table:style-name")
    assert forked_name is not None
    assert c.style.bold is True

    # a second property set on the same cell reuses the same forked style
    c.style.italic = True
    assert c.attrs.get("table:style-name") == forked_name
    assert c.style.bold is True and c.style.italic is True


def test_setting_a_style_property_does_not_affect_other_cells(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    # A1/C1/D1 originally share one compressed, repeated cell element
    s["A1"].style.bold = True
    assert s["A1"].style.bold is True
    assert s["C1"].style.bold is False
    assert s["D1"].style.bold is False


def test_setting_bold_forks_off_an_existing_named_style_as_parent(writable_reader):
    # ce9 (assigned in the fixture) sets vertical/horizontal alignment;
    # forking for .bold must keep that inherited via style:parent-style-name
    s = writable_reader.sheet("Sheet1")
    c = s["A7"]  # ce2, data-style N108 (currency) - has a parent chain to Default
    before_align = c.style.horizontal_align
    c.style.bold = True
    assert c.style.bold is True
    assert c.style.horizontal_align == before_align


def test_underline_and_strikethrough(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.underline = True
    assert c.style.underline is True
    c.style.strikethrough = True
    assert c.style.strikethrough is True
    c.style.underline = False
    assert c.style.underline is False
    assert c.style.strikethrough is True  # unrelated property untouched


def test_colors_font_and_alignment(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.font_color = "#FF0000"
    c.style.background_color = "#00FF00"
    c.style.font_family = "Liberation Sans"
    c.style.font_size = "14pt"
    c.style.vertical_align = "middle"
    c.style.horizontal_align = "center"
    assert c.style.font_color == "#FF0000"
    assert c.style.background_color == "#00FF00"
    assert c.style.font_family == "Liberation Sans"
    assert c.style.font_size == "14pt"
    assert c.style.vertical_align == "middle"
    assert c.style.horizontal_align == "center"


def test_rotation_writing_mode_wrap_shrink_protection(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.rotation = 90
    c.style.writing_mode = "tb-rl"
    c.style.wrap_text = True
    c.style.shrink_to_fit = True
    c.style.protection = "protected"
    assert c.style.rotation == 90
    assert c.style.writing_mode == "tb-rl"
    assert c.style.wrap_text is True
    assert c.style.shrink_to_fit is True
    assert c.style.protection == "protected"

    c.style.rotation = None
    assert c.style.rotation is None


def test_text_position_and_super_subscript(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.superscript = True
    assert c.style.superscript is True
    assert c.style.subscript is False
    c.style.subscript = True
    assert c.style.subscript is True
    assert c.style.superscript is False
    c.style.text_position = None
    assert c.style.superscript is False and c.style.subscript is False


def test_diagonals_none_reverts_to_inherited_while_literal_none_string_cancels(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.diagonal_bl_tr = "0.5pt solid #808080"
    assert c.style.diagonal_bl_tr == Border("0.5pt solid #808080")
    c.style.diagonal_bl_tr = None  # removes the override entirely
    assert c.style.diagonal_bl_tr is None
    c.style.diagonal_bl_tr = "none"  # explicit cancel (still None on read)
    assert c.style.diagonal_bl_tr is None


def test_setting_one_border_side_preserves_the_other_three(writable_reader):
    # ce9 is assigned to some Sheet1 cells with no border info at all here,
    # so start from a cell with a pre-existing 4-side border to prove the
    # "carry the other 3 sides over" behaviour actually matters
    s = writable_reader.sheet("Sheet1")
    c = s["A2"]
    c.style.border_top = "0.5pt solid #000000"
    c.style.border_left = "1pt solid #111111"
    c.style.border_bottom = "1.5pt solid #222222"
    c.style.border_right = "2pt solid #333333"

    # now change only the top side - the other 3 must survive untouched
    c.style.border_top = "3pt solid #FF0000"
    assert c.style.border_top == Border("3pt solid #FF0000")
    assert c.style.border_left == Border("1pt solid #111111")
    assert c.style.border_bottom == Border("1.5pt solid #222222")
    assert c.style.border_right == Border("2pt solid #333333")


def test_border_side_set_to_none_writes_explicit_none(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.border_top = "0.5pt solid #000000"
    c.style.border_bottom = None
    assert c.style.border_top == Border("0.5pt solid #000000")
    assert c.style.border_bottom is None


def test_assign_existing_number_format(writable_reader):
    s = writable_reader.sheet("Sheet1")
    percentage_format = s["A6"].style.number_format
    assert percentage_format is not None

    s["A1"].value = 0.5
    s["A1"].style.number_format = percentage_format
    assert s["A1"].style.number_format.name == percentage_format.name

    s["A2"].style.number_format = percentage_format.name  # a bare style name also works
    assert s["A2"].style.number_format.name == percentage_format.name

    s["A2"].style.number_format = None
    assert s["A2"].style.number_format is None


def test_assign_unknown_number_format_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A1"].style.number_format = "NOT_A_REAL_STYLE"


def test_style_write_without_owning_cell_raises(reader):
    style = CellStyle(reader, name=None, cell=None)
    with pytest.raises(RuntimeError):
        style.bold = True


def test_save_round_trip_after_style_writes(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.style.bold = True
    c.style.font_color = "#FF0000"
    c.style.border_top = "0.5pt solid #000000"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    style = reread["A1"].style
    assert style.bold is True
    assert style.font_color == "#FF0000"
    assert style.border_top == Border("0.5pt solid #000000")


# ---------------------------------------------------------------------------
# Styles (écriture) : RowStyle / ColumnStyle / TableStyle
# ---------------------------------------------------------------------------


def test_row_style_write_forks_and_reuses(writable_reader):
    s = writable_reader.sheet("Sheet1")
    row0_before_name = s.row_style(0).name
    s.row_style(0).height = "2cm"
    forked_name = s.row_style(0).name
    assert forked_name != row0_before_name
    assert s.row_style(0).height == "2cm"

    # a second write on the same row reuses the fork instead of forking again
    s.row_style(0).optimal_height = False
    assert s.row_style(0).name == forked_name
    assert s.row_style(0).height == "2cm"  # carried over from the first write


def test_row_style_write_carries_over_existing_properties(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s.row_style(0).optimal_height is True  # ro1's original value
    s.row_style(0).visible = False
    # .height wasn't touched, but must still reflect ro1's original value,
    # not silently reset just because a private style was forked
    assert s.row_style(0).height == "0.452cm"
    assert s.row_style(0).visible is False


def test_row_style_visible_toggle(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.row_style(0).visible = False
    assert s.row_style(0).visible is False
    s.row_style(0).visible = True
    assert s.row_style(0).visible is True


def test_column_style_write_forks_and_reuses(writable_reader):
    s = writable_reader.sheet("Sheet1")
    col0_before_name = s.column_style(0).name
    s.column_style(0).width = "5cm"
    forked_name = s.column_style(0).name
    assert forked_name != col0_before_name
    assert s.column_style(0).width == "5cm"
    assert s.column_style(1).width == "4.251cm"  # unaffected sibling column


def test_column_style_write_on_a_repeated_column_splits_it(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    assert s.column_style(3).name == s.column_style(5).name == "co1"
    s.column_style(3).width = "3cm"
    assert s.column_style(3).width == "3cm"
    # sibling columns that shared the same repeated definition are untouched
    assert s.column_style(5).width == "2.258cm"
    assert s.column_style(4).width == "2.258cm"


def test_table_style_write_forks_and_reuses(writable_reader):
    s = writable_reader.sheet("Sheet1")
    before_name = s.style.name
    s.style.tab_color = "#123456"
    forked_name = s.style.name
    assert forked_name != before_name
    assert s.style.tab_color == "#123456"

    s.style.visible = False
    assert s.style.name == forked_name  # reused, not re-forked
    assert s.style.tab_color == "#123456"  # carried over
    assert s.style.visible is False


def test_style_write_without_owner_raises_for_row_column_table():
    from odsslicer.classes import ColumnStyle, RowStyle, TableStyle

    with pytest.raises(RuntimeError):
        RowStyle(tag=None).height = "1cm"
    with pytest.raises(RuntimeError):
        ColumnStyle(tag=None).width = "1cm"
    with pytest.raises(RuntimeError):
        TableStyle(tag=None).tab_color = "#000000"


def test_save_round_trip_after_row_column_table_style_writes(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s.row_style(0).height = "2cm"
    s.column_style(0).width = "5cm"
    s.style.tab_color = "#123456"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread.row_style(0).height == "2cm"
    assert reread.column_style(0).width == "5cm"
    assert reread.style.tab_color == "#123456"


# ---------------------------------------------------------------------------
# Cell.style setter : copier/dupliquer un style d'une cellule vers une autre
# ---------------------------------------------------------------------------


def test_assigning_another_cells_style_points_at_the_same_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["B1"].style = s["A1"].style
    assert s["B1"].attrs.get("table:style-name") == s["A1"].attrs.get("table:style-name")
    assert s["B1"].style.bold is True


def test_assigning_a_cell_directly_also_works(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.italic = True
    s["B1"].style = s["A1"]
    assert s["B1"].style.italic is True


def test_assigning_a_bare_style_name_also_works(writable_reader):
    s = writable_reader.sheet("Sheet1")
    name = s["A7"].attrs.get("table:style-name")  # ce2, has a real named style
    s["B1"].style = name
    assert s["B1"].attrs.get("table:style-name") == name


def test_assigning_none_clears_the_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["A1"].style = None
    assert s["A1"].attrs.get("table:style-name") is None


def test_forking_after_a_style_copy_does_not_affect_the_source_cell(writable_reader):
    # regression: the "already forked, reuse" cache used to be keyed off
    # whether table:style-name *looked* like a forked name, which broke as
    # soon as two different cells legitimately shared one via this setter
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["B1"].style = s["A1"].style
    assert s["A1"].attrs.get("table:style-name") == s["B1"].attrs.get("table:style-name")

    s["B1"].style.italic = True
    assert s["A1"].style.italic is False
    assert s["B1"].style.italic is True
    assert s["B1"].style.bold is True  # still carried over from the shared parent
    assert s["A1"].attrs.get("table:style-name") != s["B1"].attrs.get("table:style-name")


def _own_style_tag(reader, cell):
    """The cell's own <style:style> element, exactly as save() will
    serialise it."""
    return reader._find_style(cell.attrs.get("table:style-name"), family="table-cell")


def _assert_parent_is_usable(reader, tag):
    """A style:parent-style-name is only honoured when it names a style from
    styles.xml: LibreOffice ignores an automatic one (content.xml) and falls
    back to Default, dropping everything the fork meant to inherit."""
    parent = tag.get("style:parent-style-name")
    if parent is not None:
        assert reader.data.find("style:style", attrs={"style:name": parent}) is None, (
            f"style:parent-style-name={parent!r} points at an automatic style"
        )


def test_forking_off_an_automatic_style_writes_its_properties_into_the_fork(writable_reader):
    # regression (issue #1): the fork used to inherit through
    # style:parent-style-name pointing at the automatic style it forked off,
    # which LibreOffice ignores - bold and border silently vanished on screen
    # while odsslicer still read them back. Assert on the serialised style
    # alone: following the chain (what CellStyle does on read) passed either
    # way, which is exactly what hid the bug.
    s = writable_reader.sheet("Sheet1")
    a, b = s["A1"], s["B1"]
    a.style.bold = True
    a.style.border_left = "2pt solid #000000"
    b.style = a.style  # both cells now share that one automatic style
    b.style.background_color = "#ffff00"

    tag = _own_style_tag(writable_reader, b)
    _assert_parent_is_usable(writable_reader, tag)
    assert tag.find("style:text-properties")["fo:font-weight"] == "bold"
    cell_props = tag.find("style:table-cell-properties")
    assert cell_props["fo:border-left"] == "2pt solid #000000"
    assert cell_props["fo:background-color"] == "#ffff00"
    # and the source cell keeps its own, untouched by the fork
    assert (
        _own_style_tag(writable_reader, a).find("style:table-cell-properties").get("fo:background-color")
        is None
    )


def test_forking_off_a_named_style_keeps_it_as_the_parent(writable_reader):
    # the other half of the rule: a *named* style is a real ancestor, so the
    # fork links to it rather than copying it (later edits to it still apply)
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.attrs["table:style-name"] = "Default"
    c.style.italic = True
    assert _own_style_tag(writable_reader, c)["style:parent-style-name"] == "Default"


def test_forking_carries_the_number_format_over(writable_reader):
    # style:data-style-name used to come along through the parent link too
    s = writable_reader.sheet("Sheet1")
    c = s["A7"]  # currency cell: its automatic style carries a data style
    data_style = _own_style_tag(writable_reader, c)["style:data-style-name"]
    displayed_before = c.text
    c.style.bold = True
    assert _own_style_tag(writable_reader, c)["style:data-style-name"] == data_style
    assert c.text == displayed_before


def test_repeated_forks_keep_accumulating_properties(writable_reader):
    # the reported symptom, in its shortest form: each new fork must still
    # carry everything the previous ones set
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    for setter, value in (("bold", True), ("italic", True), ("background_color", "#ffff00")):
        setattr(c.style, setter, value)
        c._own_style_name = None  # force the next write to fork again
    tag = _own_style_tag(writable_reader, c)
    _assert_parent_is_usable(writable_reader, tag)
    text_props = tag.find("style:text-properties")
    assert text_props["fo:font-weight"] == "bold"
    assert text_props["fo:font-style"] == "italic"
    assert tag.find("style:table-cell-properties")["fo:background-color"] == "#ffff00"


def test_assigning_an_invalid_style_value_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].style = 42


# ---------------------------------------------------------------------------
# NumberFormat.create / .add_condition (écriture)
# ---------------------------------------------------------------------------


def test_create_a_number_format(writable_reader):
    r = writable_reader
    fmt = NumberFormat.create(r, "number", decimal_places=3, grouping=True)
    assert fmt.family == "number"
    assert fmt.decimal_places == 3
    assert fmt.grouping is True

    s = r.sheet("Sheet1")
    s["A1"].value = 1234.5678
    s["A1"].style.number_format = fmt
    assert s["A1"].style.number_format.name == fmt.name


def test_create_a_percentage_format(writable_reader):
    fmt = NumberFormat.create(writable_reader, "percentage", decimal_places=1)
    assert fmt.family == "percentage"
    assert fmt.decimal_places == 1


@pytest.mark.parametrize(
    ("language", "country", "text"),
    [
        # where LibreOffice's standard percentage format puts the sign
        ("en", "US", "50%"),
        ("fr", "FR", "50 %"),
        ("de", "DE", "50\xa0%"),
        ("tr", "TR", "%50"),
        ("de", None, "50\xa0%"),  # a language alone: its first country's
        ("zxx", None, "50%"),  # a locale LibreOffice does not know
        (None, None, "50%"),
    ],
)
def test_a_percentage_format_puts_its_sign_where_the_locale_does(language, country, text):
    # regression: a space before the sign in every document, 50 % in en-US
    r = document_in(language, country)
    fmt = NumberFormat.create(r, "percentage", decimal_places=0)
    cell = r.sheet("Sheet1")["A1"]
    cell.style.number_format = fmt
    cell.value = 0.5
    assert cell.text == text


def test_the_text_of_a_percentage_follows_its_format(tmp_path):
    # regression: always a space before the sign, whatever the format said:
    # 50.00 % in an en-US 0.00% cell
    styles = (
        '<number:percentage-style style:name="Npct"><number:number number:decimal-places="2"'
        ' number:min-decimal-places="2" number:min-integer-digits="1"/><number:text>%</number:text>'
        "</number:percentage-style>"
        '<style:style style:name="pct" style:family="table-cell" style:data-style-name="Npct"/>'
    )
    xml = "<table:table-column/>" + table_row('<table:table-cell table:style-name="pct"/>')
    r = ODSReader(ods_with_sheet(tmp_path / "pct.ods", xml, styles))
    r.sheet("Sheet1")["A1"].value = 0.5
    assert r.sheet("Sheet1")["A1"].text == "50.00%"


def test_create_an_elapsed_time_format():
    # regression: no way to make [HH]:MM - 26 hours showed as 02:00
    r = ODSReader.new()
    fmt = NumberFormat.create(
        r, "time", components=[("hours", "long"), ("text", ":"), ("minutes", "long")], elapsed=True
    )
    assert fmt.elapsed
    cell = r.sheet("Sheet1")["A1"]
    cell.style.number_format = fmt
    cell.value = dt.timedelta(hours=26)
    assert cell.text == "26:00"
    wrapping = NumberFormat.create(
        r, "time", components=[("hours", "long"), ("text", ":"), ("minutes", "long")]
    )
    assert not wrapping.elapsed


def test_only_a_time_format_is_elapsed():
    with pytest.raises(ValueError):
        NumberFormat.create(ODSReader.new(), "date", components=[("day", "long")], elapsed=True)


def test_create_a_currency_format(writable_reader):
    fmt = NumberFormat.create(writable_reader, "currency", decimal_places=2, currency_symbol="$")
    assert fmt.family == "currency"
    assert fmt.currency_symbol == "$"


def test_create_currency_without_symbol_raises(writable_reader):
    with pytest.raises(ValueError):
        NumberFormat.create(writable_reader, "currency")


def test_create_a_date_format_from_components(writable_reader):
    components = [("day", "long"), ("text", "-"), ("month", "long"), ("text", "-"), ("year", "long")]
    fmt = NumberFormat.create(writable_reader, "date", components=components)
    assert fmt.family == "date"
    assert fmt.components == components


def test_create_date_without_components_raises(writable_reader):
    with pytest.raises(ValueError):
        NumberFormat.create(writable_reader, "date")


def test_create_a_boolean_format(writable_reader):
    fmt = NumberFormat.create(writable_reader, "boolean")
    assert fmt.family == "boolean"


def test_create_unknown_family_raises(writable_reader):
    with pytest.raises(ValueError):
        NumberFormat.create(writable_reader, "fraction")


def test_create_with_font_color(writable_reader):
    fmt = NumberFormat.create(writable_reader, "currency", currency_symbol="€", font_color="#FF0000")
    assert fmt.font_color == "#FF0000"


def test_add_condition_wires_up_conditional_resolution(writable_reader):
    r = writable_reader
    negative = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
    base = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€")
    base.add_condition("value()<0", negative)

    assert base.resolve(-5).name == negative.name
    assert base.resolve(-5).font_color == "#FF0000"
    assert base.resolve(5).name == base.name
    assert base.resolve(5).font_color is None


def test_add_condition_with_non_number_format_target_raises(writable_reader):
    fmt = NumberFormat.create(writable_reader, "number")
    with pytest.raises(TypeError):
        fmt.add_condition("value()<0", "not a NumberFormat")


def test_save_round_trip_after_creating_and_assigning_a_number_format(writable_reader, tmp_path):
    r = writable_reader
    negative = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
    base = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€")
    base.add_condition("value()<0", negative)

    s = r.sheet("Sheet1")
    s["A1"].value = -10.0
    s["A1"].style.number_format = base

    out = tmp_path / "out.ods"
    r.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    resolved = reread["A1"].style.number_format
    assert resolved.currency_symbol == "€"
    assert resolved.font_color == "#FF0000"


# ---------------------------------------------------------------------------
# Sheet.delete_row / delete_column / ODSReader.delete_sheet (écriture)
# ---------------------------------------------------------------------------


def test_delete_row_shifts_everything_up(writable_reader):
    s = writable_reader.sheet("Sheet1")
    before = [s[i, 0].value for i in range(s.n_rows)]
    n_rows_before = s.n_rows
    # row 7 (the date) isn't referenced by A5's SUM(A2:A3) formula, so this
    # exercises the plain row-shift in isolation - see the dedicated
    # "adjust formulas on delete" tests below for the formula-adjustment
    # side effect itself
    s.delete_row(7)
    after = [s[i, 0].value for i in range(s.n_rows)]
    assert s.n_rows == n_rows_before - 1
    assert s.size == (n_rows_before - 1, s.n_cols)
    assert after == before[:7] + before[8:]


def test_delete_column_shifts_everything_left(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n_cols_before = s.n_cols
    assert s[0, 1].value == "seconde colonne"
    s.delete_column(0)
    assert s.n_cols == n_cols_before - 1
    assert s[0, 0].value == "seconde colonne"


def test_delete_rows_batch_matches_sequential_deletes(writable_reader, test_ods_path):
    from odsslicer import ODSReader

    s = writable_reader.sheet("Sheet1")
    s["C5"].formula = "A6+A8"
    s.delete_rows([1, 3, 6])

    sequential = ODSReader(test_ods_path)
    s2 = sequential.sheet("Sheet1")
    s2["C5"].formula = "A6+A8"
    for row in [6, 3, 1]:  # same set, one by one, bottom-up
        s2.delete_row(row)

    assert s.size == s2.size
    assert [c.value for c in s[0]] == [c.value for c in s2[0]]
    assert s["C3"].formula_friendly == s2["C3"].formula_friendly == "=A4+A5"


def test_delete_rows_ignores_duplicates(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n = s.n_rows
    s.delete_rows([2, 2, 2])
    assert s.n_rows == n - 1


def test_delete_rows_empty_iterable_is_a_no_op(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n = s.n_rows
    s.delete_rows([])
    assert s.n_rows == n


def test_delete_rows_out_of_range_removes_nothing(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n = s.n_rows
    with pytest.raises(IndexError):
        s.delete_rows([1, 999])
    assert s.n_rows == n  # atomic: the valid index was not removed either


def test_delete_rows_through_merges(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s.delete_rows([0, 3])  # intersects the A1:C1 and A3:A5 merges
    for row in s.rows:
        for cell in row:
            if cell.is_merged:
                assert cell.merge_master is not None  # any surviving merge is coherent


def test_delete_row_out_of_range_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(IndexError):
        s.delete_row(999)


def test_delete_column_out_of_range_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(IndexError):
        s.delete_column(999)


def test_delete_row_through_a_merge_unmerges_only_that_merge(writable_reader):
    # A6:D7 and A8:D9 are two separate rectangular merges in SheetFusion -
    # deleting row 5 ("A6", the first merge's master row) must dissolve
    # only that one, leaving the untouched A8:D9 merge intact (just
    # shifted up by one row, to A7:D8)
    s = writable_reader.sheet("SheetFusion")
    assert s["A6"].merge_span == (2, 4)
    assert s["A8"].merge_span == (2, 4)
    s.delete_row(5)
    assert s["A7"].is_merge_master
    assert s["A7"].merge_span == (2, 4)
    # the two other, untouched merges (A1:C1 and A3:A5, 3 cells each) plus
    # the surviving, shifted A7:D8 (8 cells) - nothing from the dissolved
    # A6:D7 merge left over
    merged_count = sum(cell.is_merged for row in s.rows for cell in row)
    assert merged_count == 3 + 3 + 8


def test_delete_column_through_a_merge_unmerges_first(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    s.delete_column(0)  # column A carries the master of every merge in this fixture
    for row in s.rows:
        for cell in row:
            assert not cell.is_merged


def test_save_round_trip_after_delete_row_and_column(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s.delete_row(1)  # drops the row holding 3.4
    s.delete_column(1)  # drops "seconde colonne" - keeps col 0's real values
    # in every remaining row, so none of them ends up empty and gets
    # trimmed as a trailing blank row on reload (see load()'s cleanup)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread.size == s.size
    assert reread[0, 0].value == "texte simple"
    assert reread[s.n_rows - 1, 0].value == dt.time(15, 0)


# ---------------------------------------------------------------------------
# delete_row/delete_column : ajustement des références de formule
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


# ---------------------------------------------------------------------------
# References to other sheets as LibreOffice writes them (issue #14): an
# absolute sheet, a name starting with an accent, a range's end on its
# start's sheet
# ---------------------------------------------------------------------------

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


def test_a_friendly_formula_takes_an_absolute_sheet_and_an_accented_name():
    # regression: left untranslated, "of:=$Data.A5*2", which no application reads
    s = ODSReader.new().sheet("Sheet1")
    s["A1"].formula = "$Data.A5*2+Élèves.B3+SUM($Data.A1:A5)"
    assert s["A1"].formula == "of:=[$Data.A5]*2+[Élèves.B3]+SUM([$Data.A1:.A5])"
    assert s["A1"].formula_friendly == "=$Data.A5*2+Élèves.B3+SUM($Data.A1:A5)"


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


# ---------------------------------------------------------------------------
# Sheet.insert_rows / insert_columns (écriture)
# ---------------------------------------------------------------------------


def _defined_extent(sheet):
    """(rows, columns) declared by the sheet's XML, repeats included - what
    a spreadsheet application counts against its maximum grid size."""
    rows = sum(int(t.get("table:number-rows-repeated", "1")) for t in sheet.table.find_all("table:table-row"))
    cols = sum(
        int(t.get("table:number-columns-repeated", "1"))
        for t in sheet.table.find_all("table:table-column", recursive=False)
    )
    return rows, cols


def test_insert_rows_shifts_everything_down(writable_reader):
    s = writable_reader.sheet("Sheet1")
    before = [s[i, 0].value for i in range(s.n_rows)]
    n_rows, n_cols = s.size
    # below A5's SUM(A2:A3), so no formula gets rewritten: this exercises the
    # plain shift - see the formula tests below for the rest
    s.insert_rows(5, 3)
    assert s.size == (n_rows + 3, n_cols)
    assert [s[i, 0].value for i in range(s.n_rows)] == before[:5] + [None] * 3 + before[5:]


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


def test_insert_row_inserts_a_single_row(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n_rows = s.n_rows
    s.insert_row(0)
    assert s.n_rows == n_rows + 1
    assert s[0, 0].value is None
    assert s[1, 0].value == "texte simple"


def test_insert_rows_at_the_end_appends(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n_rows = s.n_rows
    s.insert_rows(n_rows, 2)
    assert s.n_rows == n_rows + 2
    s[n_rows + 1, 0].value = "appended"
    assert s[n_rows + 1, 0].value == "appended"


def test_insert_columns_shifts_everything_right(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n_rows, n_cols = s.size
    s.insert_columns(1, 2)
    assert s.size == (n_rows, n_cols + 2)
    assert s[0, :4].to_list() == ["texte simple", None, None, "seconde colonne"]
    assert all(len(row) == n_cols + 2 for row in s.rows)


def test_insert_column_at_the_end_appends(writable_reader):
    s = writable_reader.sheet("Sheet1")
    n_cols = s.n_cols
    s.insert_column(n_cols)
    assert s.n_cols == n_cols + 1
    assert s[0, :3].to_list() == ["texte simple", "seconde colonne", None]


@pytest.mark.parametrize("method", ["insert_rows", "insert_columns"])
@pytest.mark.parametrize("position", [-1, 999])
def test_insert_out_of_range_raises(writable_reader, method, position):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(IndexError):
        getattr(s, method)(position)


@pytest.mark.parametrize("method", ["insert_rows", "insert_columns"])
def test_insert_count_below_one_raises(writable_reader, method):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        getattr(s, method)(0, 0)


def test_insert_rows_between_repeated_rows_keeps_them_independent(writable_reader):
    # rows 0 and 1 share one <table:table-row table:number-rows-repeated="2">
    s = writable_reader.sheet("Sheet2Repeat")
    s.insert_row(1)
    assert s[0, :4].to_list() == [1.0] * 4
    assert s[1, :4].to_list() == [None] * 4
    assert s[2, :4].to_list() == [1.0] * 4
    s[2, 0].value = 9.0
    assert s[0, 0].value == 1.0


def test_insert_columns_through_repeated_rows_and_cells(writable_reader, tmp_path):
    # rows 0-1 are one repeated row of repeated cells: the new cells must go
    # into each logical row exactly once, and stay independent
    s = writable_reader.sheet("Sheet2Repeat")
    s.insert_column(2)
    assert s[0, :5].to_list() == [1.0, 1.0, None, 1.0, 1.0]
    s[0, 2].value = "new"
    assert s[1, 2].value is None
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet2Repeat")
    assert reread[0, :5].to_list() == [1.0, 1.0, "new", 1.0, 1.0]
    assert reread[1, :5].to_list() == [1.0, 1.0, None, 1.0, 1.0]


@pytest.mark.parametrize(
    ("at", "master_row", "span"),
    [
        (3, 2, (4, 1)),  # strictly inside A3:A5 (rows 2-4): the merge grows
        (2, 3, (3, 1)),  # at its first row: the merge moves down whole
        (5, 2, (3, 1)),  # just below its last row: untouched
    ],
)
def test_insert_row_and_a_merge(writable_reader, at, master_row, span):
    s = writable_reader.sheet("SheetFusion")
    assert s["A3"].merge_span == (3, 1)
    s.insert_row(at)
    assert s[master_row, 0].merge_span == span
    assert all(s[r, 0].is_covered for r in range(master_row + 1, master_row + span[0]))
    assert not s[master_row + span[0], 0].is_covered


def test_insert_columns_inside_a_merge_grows_it(writable_reader, tmp_path):
    s = writable_reader.sheet("SheetFusion")
    assert s["A1"].merge_span == (1, 3)
    s.insert_columns(1, 2)
    assert s["A1"].merge_span == (1, 5)
    assert all(s[0, c].is_covered for c in range(1, 5))
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    assert ODSReader(out).sheet("SheetFusion")["A1"].merge_span == (1, 5)


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


def test_insert_columns_keeps_column_widths_with_their_columns(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.column_style(1).width = "5cm"
    s.insert_columns(1, 2)
    assert s.column_style(3).width == "5cm"
    # the new ones repeat the column they push right, as LibreOffice does
    # (issue #25) - see test_inserted_columns_take_the_width_of_the_column...
    assert [s.column_style(c).width for c in (1, 2)] == ["5cm", "5cm"]
    assert _defined_extent(s)[1] == s.n_cols


# ---------------------------------------------------------------------------
# Column definitions (issue #13): a sheet declares only the columns it uses,
# and LibreOffice groups the print titles' definitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("col", "expected"),
    [(1, {"A1": "a", "B1": "c"}), (2, {"A1": "a"})],
)
def test_deleting_a_column_past_the_declared_ones(tmp_path, col, expected):
    # regression: AssertionError, no definition covering the column
    xml = "<table:table-column/>" + table_row(text_cell("a"))  # as LibreOffice writes it
    path = ods_with_sheet(tmp_path / "one.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    s["C1"].value = "c"
    s.delete_column(col)
    r.save()
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == expected


def _widths(sheet):
    styles = [sheet.column_style(col) for col in range(sheet.n_cols)]
    return [style.width if style else None for style in styles]


@pytest.mark.parametrize(
    ("edit", "widths"),
    [
        pytest.param(lambda s: None, ["1cm", "2cm", "3cm"], id="read"),
        pytest.param(lambda s: s.delete_column(1), ["1cm", "3cm"], id="delete_column(1)"),
        pytest.param(lambda s: s.delete_column(0), ["2cm", "3cm"], id="delete_column(0)"),
        pytest.param(lambda s: s.insert_columns(1), ["1cm", "2cm", "2cm", "3cm"], id="insert_columns(1)"),
        pytest.param(lambda s: s.insert_columns(0), ["1cm", "1cm", "2cm", "3cm"], id="insert_columns(0)"),
    ],
)
def test_the_widths_of_print_title_columns_stay_with_their_columns(tmp_path, edit, widths):
    # regression: the grouped definition was skipped, and every column took
    # the width of the one after it
    path = ods_with_sheet(tmp_path / "titles.ods", PRINT_TITLE_XML, PRINT_TITLE_WIDTHS_XML)
    r = ODSReader(path)
    edit(r.sheet("Sheet1"))
    assert _widths(r.sheet("Sheet1")) == widths
    r.save()
    assert _widths(ODSReader(path).sheet("Sheet1")) == widths
    # an emptied group goes: ODF wants one definition in it at least
    for group in saved_table(path).find_all("table:table-header-columns"):
        assert group.find("table:table-column") is not None


@pytest.mark.parametrize(
    ("edit", "widths"),
    [
        pytest.param(lambda s: s.delete_column(1), ["1cm", "3cm"], id="delete_column(1)"),
        pytest.param(lambda s: s.insert_columns(1), ["1cm", "1cm", "1cm", "3cm"], id="insert_columns(1)"),
    ],
)
def test_a_repeated_print_title_definition_is_split_where_it_stands(tmp_path, edit, widths):
    # A and B print titles, one definition repeated twice
    xml = (
        '<table:table-header-columns><table:table-column table:style-name="w1"'
        ' table:number-columns-repeated="2"/></table:table-header-columns>'
        '<table:table-column table:style-name="w3"/>'
        + table_row(text_cell("a"), text_cell("b"), text_cell("c"))
    )
    path = ods_with_sheet(tmp_path / "titles.ods", xml, PRINT_TITLE_WIDTHS_XML)
    r = ODSReader(path)
    edit(r.sheet("Sheet1"))
    r.save()
    assert _widths(ODSReader(path).sheet("Sheet1")) == widths


def test_a_full_width_sheet_with_print_titles_keeps_its_width(tmp_path):
    # the print title's definition counts in the sheet's full width
    xml = (
        '<table:table-header-columns><table:table-column table:style-name="w1"/>'
        "</table:table-header-columns>"
        '<table:table-column table:style-name="w3" table:number-columns-repeated="16383"/>'
        + table_row(text_cell("a"), text_cell("b"))
    )
    path = ods_with_sheet(tmp_path / "titles.ods", xml, PRINT_TITLE_WIDTHS_XML)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_columns(1, 2)
    r.save()
    definitions = saved_table(path).find_all("table:table-column")
    assert sum(_repeat(d, "table:number-columns-repeated") for d in definitions) == 16_384


def test_insertions_stay_within_the_applications_grid(tmp_path):
    # LibreOffice declares its full 16,384 x 1,048,576 grid through trailing
    # filler rows/columns: insertions must not push the document past it
    table = ODSReader(FIXTURES_DIR / "wild" / "libreoffice26_linux_streets.ods")
    s = table.sheet("Feuille1")
    rows_before, cols_before = _defined_extent(s)
    assert (rows_before, cols_before) == (1048576, 16384)
    first_values = [s[i, 0].value for i in range(2)]
    s.insert_rows(1, 5)
    s.insert_columns(1, 2)
    rows_after, cols_after = _defined_extent(s)
    assert rows_after <= rows_before
    assert cols_after == cols_before
    out = tmp_path / "out.ods"
    table.save(out)
    reread = ODSReader(out).sheet("Feuille1")
    assert reread.size == s.size
    assert reread[0, 0].value == first_values[0]
    assert reread[6, 0].value == first_values[1]


def test_save_round_trip_after_insertions(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s.insert_rows(1, 2)
    s.insert_column(0)
    s[1, 0].value = "inserted"
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet1")
    assert reread.size == s.size
    assert reread[0, 1].value == "texte simple"
    assert reread[1, 0].value == "inserted"
    assert reread[3, 1].value == 3.4
    assert reread["B7"].formula_friendly == "=SUM(B4:B5)"


def test_delete_sheet(writable_reader):
    r = writable_reader
    r.add_sheet("Extra")
    r.delete_sheet("Extra")
    assert "Extra" not in r.sheets_names
    with pytest.raises(KeyError):
        r.sheet("Extra")


def test_delete_unknown_sheet_raises(writable_reader):
    with pytest.raises(KeyError):
        writable_reader.delete_sheet("NoSuchSheet")


def test_delete_the_last_remaining_sheet_raises(writable_reader):
    r = writable_reader
    for name in list(r.sheets_names)[:-1]:
        r.delete_sheet(name)
    assert len(r.sheets_names) == 1
    with pytest.raises(ValueError):
        r.delete_sheet(r.sheets_names[0])


def test_save_round_trip_after_delete_sheet(writable_reader, tmp_path):
    r = writable_reader
    r.add_sheet("Extra")
    r.delete_sheet("Extra")
    out = tmp_path / "out.ods"
    r.save(out)
    reread = ODSReader(out)
    assert "Extra" not in reread.sheets_names


# ---------------------------------------------------------------------------
# Sheet.copy (copier-coller de cellules/plages, écriture)
# ---------------------------------------------------------------------------


def test_copy_a_single_cell(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s.copy("A1", "C1")
    assert s["C1"].value == "texte simple"
    assert s["C1"].style.bold is True


def test_copy_a_range_preserves_shape(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.copy("A1:B2", "D5")
    assert s["D5"].value == s["A1"].value
    assert s["E5"].value == s["B1"].value
    assert s["D6"].value == s["A2"].value
    assert s["E6"].value == s["B2"].value


def test_copy_shifts_relative_formula_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "A2+A3"
    s.copy("C1", "E5")
    assert s["E5"].formula_friendly == "=C6+C7"


def test_copy_keeps_absolute_formula_references_in_place(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A$2+A3"
    s.copy("C1", "E5")
    assert s["E5"].formula_friendly == "=$A$2+C7"


def test_copy_grows_the_sheet_if_needed(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.copy("A1:B2", "Z20")
    assert s.n_rows >= 21 and s.n_cols >= 27
    assert s["Z20"].value == s["A1"].value


def test_copy_is_safe_with_overlapping_source_and_dest(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["H1"].value = 1
    s["H2"].value = 2
    s["H3"].value = 3
    s.copy("H1:H3", "H2")
    assert [s[f"H{i}"].value for i in (1, 2, 3, 4)] == [1, 1, 2, 3]


def test_copy_an_empty_cell_clears_the_destination(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "will be cleared"
    s.copy("Z1", "C1")  # Z1 is out of range -> empty
    assert s["C1"].value is None


def test_save_round_trip_after_copy(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["C1"].formula = "A2+A3"
    s.copy("A1:C1", "E5")

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["E5"].value == "texte simple"
    assert reread["E5"].style.bold is True
    assert reread["G5"].formula_friendly == "=E6+E7"


# ---------------------------------------------------------------------------
# ODSReader.properties (DocumentProperties, meta.xml)
# ---------------------------------------------------------------------------


def test_document_properties_reads_existing_metadata(reader):
    p = reader.properties
    assert p.creator == "Antonin Marchand"
    assert p.initial_creator == "Antonin Marchand"
    assert p.generator.startswith("LibreOffice/")


def test_document_properties_unset_fields_are_none_or_empty(reader):
    p = reader.properties
    assert p.title is None
    assert p.subject is None
    assert p.description is None
    assert p.keywords == []
    assert p.custom == {}


def test_document_properties_write_text_fields(writable_reader):
    p = writable_reader.properties
    p.title = "Mon classeur"
    p.subject = "Tests"
    p.description = "Un fichier de test"
    p.creator = "Someone Else"
    assert p.title == "Mon classeur"
    assert p.subject == "Tests"
    assert p.description == "Un fichier de test"
    assert p.creator == "Someone Else"
    # untouched fields still resolve correctly
    assert p.initial_creator == "Antonin Marchand"


def test_document_properties_setting_none_clears_the_field(writable_reader):
    p = writable_reader.properties
    p.title = "Mon classeur"
    p.title = None
    assert p.title is None


def test_document_properties_keywords_read_write(writable_reader):
    p = writable_reader.properties
    p.keywords = ["test", "ods", "python"]
    assert p.keywords == ["test", "ods", "python"]
    p.keywords = ["only-one"]
    assert p.keywords == ["only-one"]  # replaces, doesn't append
    p.keywords = []
    assert p.keywords == []


def test_document_properties_generator_has_no_setter(writable_reader):
    with pytest.raises(AttributeError):
        writable_reader.properties.generator = "odsslicer"


def test_document_properties_custom_dict_access(writable_reader):
    p = writable_reader.properties
    p["Client"] = "Acme Corp"
    assert "Client" in p
    assert p["Client"] == "Acme Corp"
    assert p.custom == {"Client": "Acme Corp"}
    del p["Client"]
    assert "Client" not in p
    with pytest.raises(KeyError):
        p["Client"]
    with pytest.raises(KeyError):
        del p["Client"]


def test_document_properties_custom_typed_values(writable_reader):
    p = writable_reader.properties
    p["as_text"] = "hello"
    p["as_float"] = 42.5
    p["as_bool"] = True
    p["as_date"] = dt.date(2026, 12, 31)
    assert p["as_text"] == "hello" and isinstance(p["as_text"], str)
    assert p["as_float"] == 42.5 and isinstance(p["as_float"], float)
    assert p["as_bool"] is True
    assert p["as_date"] == dt.date(2026, 12, 31)


def test_document_properties_custom_overwrite_changes_type(writable_reader):
    p = writable_reader.properties
    p["Value"] = 1.0
    assert isinstance(p["Value"], float)
    p["Value"] = "now text"
    assert p["Value"] == "now text"


def test_document_properties_custom_invalid_type_raises(writable_reader):
    with pytest.raises(TypeError):
        writable_reader.properties["bad"] = object()


def test_save_round_trip_after_setting_document_properties(writable_reader, tmp_path):
    p = writable_reader.properties
    p.title = "Mon classeur"
    p.keywords = ["a", "b"]
    p["Client"] = "Acme Corp"
    p["Montant"] = 42.5

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).properties
    assert reread.title == "Mon classeur"
    assert reread.keywords == ["a", "b"]
    assert reread.custom == {"Client": "Acme Corp", "Montant": 42.5}
    # cell data/styles from the rest of the document are still intact
    assert ODSReader(out).sheet("Sheet1")["A1"].value == "texte simple"


# ---------------------------------------------------------------------------
# Cell.comment (Comment, office:annotation)
# ---------------------------------------------------------------------------


def test_cell_with_no_comment_is_none(reader):
    s = reader.sheet("Sheet1")
    assert s["A1"].comment is None


def test_setting_a_comment_creates_one(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    assert s["A1"].comment.text == "Une note"


def test_multiline_comment_round_trips(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Ligne 1\nLigne 2\nLigne 3"
    assert s["A1"].comment.text == "Ligne 1\nLigne 2\nLigne 3"


def test_comment_does_not_corrupt_the_cells_own_value(writable_reader):
    # regression: office:annotation nests its own text:p - reading/writing
    # a cell's value must never pick up the comment's paragraph instead
    s = writable_reader.sheet("Sheet1")
    assert s["A1"].value == "texte simple"
    s["A1"].comment = "Une note"
    assert s["A1"].value == "texte simple"
    assert s["A1"].text == "texte simple"


def test_writing_a_value_does_not_remove_an_existing_comment(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    s["A1"].value = "nouvelle valeur"
    assert s["A1"].value == "nouvelle valeur"
    assert s["A1"].comment.text == "Une note"


def test_comment_author_date_and_visible(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    c = s["A1"].comment
    assert c.author is None
    assert c.date is None
    assert c.visible is False

    c.author = "Antonin"
    c.date = dt.datetime(2026, 8, 23, 10, 30)
    c.visible = True
    assert c.author == "Antonin"
    assert c.date == dt.datetime(2026, 8, 23, 10, 30)
    assert c.visible is True


def test_setting_comment_to_none_removes_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    s["A1"].comment = None
    assert s["A1"].comment is None


def test_setting_comment_text_again_replaces_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Premiere note"
    s["A1"].comment = "Deuxieme note"
    assert s["A1"].comment.text == "Deuxieme note"


def test_comment_date_requires_a_datetime(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    with pytest.raises(TypeError):
        s["A1"].comment.date = "2026-08-23"


def test_setting_a_non_string_comment_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].comment = 42


def test_comment_on_a_repeated_cell_only_affects_that_cell(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["A1"].comment = "Just A1"
    assert s["A1"].comment.text == "Just A1"
    assert s["C1"].comment is None  # shared the same compressed element originally


def test_save_round_trip_after_comment(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    c = s["A1"].comment
    c.author = "Antonin"
    c.date = dt.datetime(2026, 8, 23, 10, 30)
    c.visible = True
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["A1"].value == "texte simple"
    comment = reread["A1"].comment
    assert comment.text == "Une note"
    assert comment.author == "Antonin"
    assert comment.date == dt.datetime(2026, 8, 23, 10, 30)
    assert comment.visible is True


# ---------------------------------------------------------------------------
# Sheet.sort
# ---------------------------------------------------------------------------


def _fill_sort_table(s):
    s["A1"].value = "Charlie"
    s["B1"].value = 3.0
    s["C1"].formula = "B1*10"
    s["A2"].value = "Alice"
    s["B2"].value = 1.0
    s["C2"].formula = "B2*10"
    s["A3"].value = "Bob"
    s["B3"].value = None
    s["A4"].value = "Dana"
    s["B4"].value = 2.0
    s["C4"].formula = "B4*10"
    s["A1"].style.bold = True


def test_sort_ascending_by_column(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    assert [s[i, 0].value for i in range(4)] == ["Alice", "Dana", "Charlie", "Bob"]


def test_sort_none_always_sorts_last(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=False)
    # descending would put a "biggest" None first under naive reverse=True -
    # it must still sort last
    assert [s[i, 0].value for i in range(4)] == ["Charlie", "Dana", "Alice", "Bob"]


def test_sort_is_stable(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "first"
    s["B1"].value = 1.0
    s["A2"].value = "second"
    s["B2"].value = 1.0
    s["A3"].value = "third"
    s["B3"].value = 1.0
    s.sort("A1:B3", by=1)
    assert [s[i, 0].value for i in range(3)] == ["first", "second", "third"]


def test_sort_moves_style_with_its_row(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    assert s["A3"].value == "Charlie" and s["A3"].style.bold is True
    assert s["A1"].style.bold is False


def test_sort_shifts_same_row_formula_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    # Alice's row (was row 2, now row 1) keeps a formula referring to its
    # own (now relocated) row
    assert s["A1"].value == "Alice"
    assert s["C1"].formula_friendly == "=B1*10"


def test_sort_column_out_of_range_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    with pytest.raises(ValueError):
        s.sort("A1:C4", by=5)


def test_save_round_trip_after_sort(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert [reread[i, 0].value for i in range(4)] == ["Alice", "Dana", "Charlie", "Bob"]
    assert reread["C1"].formula_friendly == "=B1*10"


# ---------------------------------------------------------------------------
# ODSReader.rename_sheet / .move_sheet
# ---------------------------------------------------------------------------


def test_rename_sheet_updates_names(writable_reader):
    r = writable_reader
    r.rename_sheet("Sheet1", "Renamed")
    assert "Renamed" in r.sheets_names
    assert "Sheet1" not in r.sheets_names
    assert r.sheet("Renamed").name == "Renamed"


def test_rename_sheet_updates_an_already_constructed_sheet_object(writable_reader):
    r = writable_reader
    s = r.sheet("Sheet1")  # force construction before the rename
    r.rename_sheet("Sheet1", "Renamed")
    assert s.name == "Renamed"
    assert r.sheet("Renamed") is s


def test_rename_sheet_updates_cross_sheet_formula_references(writable_reader):
    r = writable_reader
    s2 = r.sheet("Sheet2Repeat")
    s2["A1"].formula = "Sheet1.A2+Sheet1.A3"
    r.rename_sheet("Sheet1", "Renamed")
    assert s2["A1"].formula_friendly == "=Renamed.A2+Renamed.A3"


def test_rename_sheet_quotes_a_name_with_spaces_in_references(writable_reader):
    r = writable_reader
    s2 = r.sheet("Sheet2Repeat")
    s2["A1"].formula = "Sheet1.A2"
    r.rename_sheet("Sheet1", "Mon Bilan")
    assert s2["A1"].formula == "of:=['Mon Bilan'.A2]"


def test_rename_sheet_does_not_touch_an_unqualified_reference_in_its_own_formulas(writable_reader):
    r = writable_reader
    s1 = r.sheet("Sheet1")
    s1["C1"].formula = "A2+A3"
    r.rename_sheet("Sheet1", "Renamed")
    assert s1["C1"].formula_friendly == "=A2+A3"


def test_rename_unknown_sheet_raises(writable_reader):
    with pytest.raises(KeyError):
        writable_reader.rename_sheet("NoSuchSheet", "x")


def test_rename_sheet_to_an_existing_name_raises(writable_reader):
    with pytest.raises(ValueError):
        writable_reader.rename_sheet("Sheet1", "Sheet2Repeat")


def test_rename_sheet_to_empty_name_raises(writable_reader):
    with pytest.raises(ValueError):
        writable_reader.rename_sheet("Sheet1", "")


def test_rename_sheet_to_its_own_name_is_a_no_op(writable_reader):
    r = writable_reader
    r.rename_sheet("Sheet1", "Sheet1")
    assert r.sheets_names.count("Sheet1") == 1


def test_save_round_trip_after_rename_sheet(writable_reader, tmp_path):
    r = writable_reader
    s2 = r.sheet("Sheet2Repeat")
    s2["A1"].formula = "Sheet1.A2"
    r.rename_sheet("Sheet1", "Renamed")
    out = tmp_path / "out.ods"
    r.save(out)

    reread = ODSReader(out)
    assert "Renamed" in reread.sheets_names
    assert reread.sheet("Sheet2Repeat")["A1"].formula_friendly == "=Renamed.A2"
    assert reread.sheet("Renamed")["A1"].value == "texte simple"


def test_move_sheet_reorders(writable_reader):
    r = writable_reader
    r.move_sheet("SheetFusion", 0)
    assert r.sheets_names == ["SheetFusion", "Sheet1", "Sheet2Repeat", "SheetEmpty"]


def test_move_sheet_to_the_end(writable_reader):
    r = writable_reader
    r.move_sheet("Sheet1", 3)
    assert r.sheets_names == ["Sheet2Repeat", "SheetEmpty", "SheetFusion", "Sheet1"]


def test_move_sheet_to_the_middle(writable_reader):
    r = writable_reader
    r.move_sheet("SheetFusion", 1)
    assert r.sheets_names == ["Sheet1", "SheetFusion", "Sheet2Repeat", "SheetEmpty"]


def test_move_sheet_to_its_own_position_is_a_no_op(writable_reader):
    r = writable_reader
    before = list(r.sheets_names)
    r.move_sheet("Sheet2Repeat", 1)
    assert r.sheets_names == before


def test_move_unknown_sheet_raises(writable_reader):
    with pytest.raises(KeyError):
        writable_reader.move_sheet("NoSuchSheet", 0)


def test_move_sheet_out_of_range_raises(writable_reader):
    with pytest.raises(ValueError):
        writable_reader.move_sheet("Sheet1", 99)


def test_save_round_trip_after_move_sheet(writable_reader, tmp_path):
    r = writable_reader
    r.move_sheet("SheetFusion", 0)
    out = tmp_path / "out.ods"
    r.save(out)

    reread = ODSReader(out)
    assert reread.sheets_names == ["SheetFusion", "Sheet1", "Sheet2Repeat", "SheetEmpty"]
    assert reread.sheet("Sheet1")["A1"].value == "texte simple"


# ---------------------------------------------------------------------------
# Cell.hyperlink
# ---------------------------------------------------------------------------


def test_cell_with_no_hyperlink_is_none(reader):
    s = reader.sheet("Sheet1")
    assert s["A1"].hyperlink is None


def test_setting_a_hyperlink_wraps_the_existing_text(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].hyperlink = "https://example.com"
    assert s["A1"].hyperlink == "https://example.com"
    assert s["A1"].value == "texte simple"  # text/value untouched


def test_setting_a_hyperlink_on_an_empty_cell_gives_it_empty_text(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].hyperlink = "https://example.com"
    assert s["C1"].hyperlink == "https://example.com"
    assert s["C1"].text == ""


def test_removing_a_hyperlink_keeps_the_text(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].hyperlink = "https://example.com"
    s["A1"].hyperlink = None
    assert s["A1"].hyperlink is None
    assert s["A1"].value == "texte simple"


def test_overwriting_the_value_clears_the_hyperlink(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].hyperlink = "https://example.com"
    s["A1"].value = "new text"
    assert s["A1"].hyperlink is None
    assert s["A1"].value == "new text"


def test_setting_a_non_string_hyperlink_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].hyperlink = 42


def test_setting_a_hyperlink_twice_replaces_the_url(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].hyperlink = "https://first.example"
    s["A1"].hyperlink = "https://second.example"
    assert s["A1"].hyperlink == "https://second.example"
    assert s["A1"].value == "texte simple"  # text still there, not duplicated


def test_hyperlink_on_a_repeated_cell_only_affects_that_cell(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["A1"].hyperlink = "https://example.com"
    assert s["A1"].hyperlink == "https://example.com"
    assert s["C1"].hyperlink is None  # shared the same compressed element originally


def test_save_round_trip_after_hyperlink(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "Anthropic"
    s["C1"].hyperlink = "https://anthropic.com"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["C1"].value == "Anthropic"
    assert reread["C1"].hyperlink == "https://anthropic.com"


# ---------------------------------------------------------------------------
# Sheet.create_pivot_table (définition ODF uniquement, pas de calcul)
# ---------------------------------------------------------------------------


def _fill_pivot_source(s):
    rows = [
        ("Category", "Region", "Amount"),
        ("A", "North", 10),
        ("B", "South", 20),
        ("A", "South", 5),
        ("B", "North", 7),
    ]
    for i, (cat, reg, amt) in enumerate(rows):
        s[i, 0].value = cat
        s[i, 1].value = reg
        s[i, 2].value = amt


def _find_pivot_table(reader, name):
    tables = reader.data.find("table:data-pilot-tables")
    return tables.find("table:data-pilot-table", attrs={"table:name": name}) if tables is not None else None


def test_create_pivot_table_writes_the_definition(writable_reader):
    r = writable_reader
    s = r.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table(
        "A1:C5", "E1", rows=["Category"], columns=["Region"], values={"Amount": "sum"}, name="MyPivot"
    )

    tag = _find_pivot_table(r, "MyPivot")
    assert tag is not None
    assert tag.get("table:target-range-address") == "SheetEmpty.E1"
    source = tag.find("table:source-cell-range")
    assert source.get("table:cell-range-address") == "SheetEmpty.A1:C5"

    fields = tag.find_all("table:data-pilot-field")
    by_name = {f.get("table:source-field-name"): f for f in fields}
    assert by_name["Category"].get("table:orientation") == "row"
    assert by_name["Region"].get("table:orientation") == "column"
    assert by_name["Amount"].get("table:orientation") == "data"
    assert by_name["Amount"].get("table:function") == "sum"


def test_create_pivot_table_no_computed_result_is_written(writable_reader):
    # the whole point: no calculation engine, same as formulas - only the
    # definition is written, the target cell stays untouched/empty
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table("A1:C5", "E1", rows=["Category"], values={"Amount": "sum"})
    assert s["E1"].value is None


def test_create_pivot_table_default_name(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table("A1:C5", "E1", rows=["Category"], values={"Amount": "sum"})
    s.create_pivot_table("A1:C5", "F1", rows=["Region"], values={"Amount": "sum"})
    assert _find_pivot_table(s.reader, "DataPilotTable1") is not None
    assert _find_pivot_table(s.reader, "DataPilotTable2") is not None


def test_create_pivot_table_duplicate_name_raises(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table("A1:C5", "E1", rows=["Category"], values={"Amount": "sum"}, name="MyPivot")
    with pytest.raises(ValueError):
        s.create_pivot_table("A1:C5", "F1", rows=["Region"], values={"Amount": "sum"}, name="MyPivot")


def test_create_pivot_table_unknown_field_raises(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    with pytest.raises(ValueError):
        s.create_pivot_table("A1:C5", "E1", rows=["NoSuchField"])


def test_create_pivot_table_unknown_function_raises(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    with pytest.raises(ValueError):
        s.create_pivot_table("A1:C5", "E1", values={"Amount": "bogus"})


def test_create_pivot_table_cross_sheet_source(writable_reader):
    r = writable_reader
    source = r.sheet("SheetEmpty")
    _fill_pivot_source(source)
    target = r.sheet("Sheet1")
    target.create_pivot_table(
        "SheetEmpty.A1:C5", "E1", rows=["Category"], values={"Amount": "sum"}, name="CrossSheetPivot"
    )
    tag = _find_pivot_table(r, "CrossSheetPivot")
    assert tag.get("table:target-range-address") == "Sheet1.E1"
    assert tag.find("table:source-cell-range").get("table:cell-range-address") == "SheetEmpty.A1:C5"


def test_save_round_trip_after_create_pivot_table(writable_reader, tmp_path):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table(
        "A1:C5", "E1", rows=["Category"], columns=["Region"], values={"Amount": "sum"}, name="MyPivot"
    )
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    tag = _find_pivot_table(reread, "MyPivot")
    assert tag is not None
    assert tag.get("table:target-range-address") == "SheetEmpty.E1"
    # source data survived untouched
    assert reread.sheet("SheetEmpty")["A2"].value == "A"


# ---------------------------------------------------------------------------
# recalculate(): error paths that don't need LibreOffice installed
# ---------------------------------------------------------------------------


def test_recalculate_missing_file_raises(tmp_path):
    from odsslicer import recalculate

    with pytest.raises(FileNotFoundError):
        recalculate(tmp_path / "does_not_exist.ods")


def test_recalculate_explicit_nonexistent_executable_raises(writable_reader, tmp_path):
    # an explicit absolute path that doesn't exist must error out, not fall
    # back silently to whatever default install happens to be around
    import odsslicer
    from odsslicer import recalculate

    out = tmp_path / "out.ods"
    writable_reader.save(out)
    saved = odsslicer.LIBREOFFICE_COMMAND[0]
    odsslicer.LIBREOFFICE_COMMAND[0] = str(tmp_path / "no" / "such" / "soffice")
    try:
        with pytest.raises(FileNotFoundError):
            recalculate(out)
    finally:
        odsslicer.LIBREOFFICE_COMMAND[0] = saved


def test_recalculate_bare_name_not_found_anywhere_raises(writable_reader, tmp_path, monkeypatch):
    import odsslicer
    from odsslicer import libreoffice, recalculate

    out = tmp_path / "out.ods"
    writable_reader.save(out)
    monkeypatch.setattr(libreoffice, "_LIBREOFFICE_FALLBACKS", [])
    saved = odsslicer.LIBREOFFICE_COMMAND[0]
    odsslicer.LIBREOFFICE_COMMAND[0] = "definitely-not-a-real-binary-name"
    try:
        with pytest.raises(FileNotFoundError):
            recalculate(out)
    finally:
        odsslicer.LIBREOFFICE_COMMAND[0] = saved


def test_save_with_update_links_but_no_recalculate_raises(writable_reader, tmp_path):
    # only the LibreOffice run updates links: asking for it without one must
    # not pass silently, nor write the file first
    out = tmp_path / "out.ods"
    with pytest.raises(ValueError, match="recalculate=True"):
        writable_reader.save(out, update_links=True)
    assert not out.exists()
