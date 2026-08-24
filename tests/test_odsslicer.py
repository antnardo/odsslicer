# -*- coding: utf-8 -*-
"""
Suite de tests pytest pour le module `odsslicer`.

Basée sur les scripts manuels historiques (tests.py, tests2.py, example.py),
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

import pytest

from odsslicer import ODSReader
from odsslicer.classes import ArrayValues, Border, Cell, CellStyle, NumberFormat, Sheet


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


@pytest.mark.parametrize("col", list(range(0, 60)) + [100, 300, 701, 702, 703, 728, 729, 1000])
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
    with pytest.raises(IndexError):
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

    attrs = {}

    def __getitem__(self, key):
        return {"table:name": "Fake", "table:style-name": "st"}[key]


def test_ragged_rows_trigger_warning(monkeypatch, capsys):
    # regression: rows_len was a `map` object consumed twice (once by max(),
    # once by the warning check), so the warning never actually fired.
    monkeypatch.setattr(Sheet, "load", lambda self, table_bs: [[1, 2, 3], [1, 2]])
    sheet = Sheet(_FakeTag())
    assert sheet.size == (2, 3)
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


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
        '<text:p>VRAI</text:p></table:table-cell>'
        '<table:table-cell office:value-type="boolean" office:boolean-value="false">'
        '<text:p>FAUX</text:p></table:table-cell>'
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
        '<table:table-column/><table:table-row><table:table-cell/></table:table-row>'
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


def test_write_merge_master_cell_reveals_covered_siblings(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    assert s.size == (9, 4)
    s["A1"].value = "nouveau maitre"
    assert s["A1"].value == "nouveau maitre"
    assert s["A1"].attrs.get("table:number-columns-spanned") is None
    # ODF stores each covered cell's original value under it; unmerging reveals it
    assert s["B1"].value == 2.0
    assert s["C1"].value == 3.0
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
        r, "date", components=[("year", "long"), ("text", "-"), ("month", "long"), ("text", "-"), ("day", "long")]
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
    fmt = NumberFormat.create(r, "date", components=[("day-of-week", "long"), ("text", " "), ("day", "long")])
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.date(2026, 3, 5)
    assert s["A1"].text == "2026-03-05"  # isoformat() fallback, not a partial/garbled render


# ---------------------------------------------------------------------------
# Écriture : nouvelles feuilles (ODSReader.add_sheet)
# ---------------------------------------------------------------------------

def test_add_sheet_creates_an_empty_sheet(writable_reader):
    before = list(writable_reader.sheets_names)
    s = writable_reader.add_sheet("NewSheet")
    assert writable_reader.sheets_names == before + ["NewSheet"]
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
        '<table:table-cell table:formula=\'of:=IF(OFFSET([$Notes.$C$3];[.I$1];'
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
    for row, ref_row in zip(range(1, 4), (1, 2, 3)):
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
        ("day", "long"), ("text", "/"), ("month", "long"), ("text", "/"), ("year", "short"),
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
    assert s.size[0] >= 11 and s.size[1] >= 27
    assert s["Z10"].merge_range == "Z10:AA11"


def test_merge_single_cell_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s.merge("A1")


def test_merge_already_merged_cell_raises(writable_reader):
    s = writable_reader.sheet("SheetFusion")
    with pytest.raises(ValueError):
        s.merge("A1:B2")  # A1 is already the master of A1:C1


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
    from odsslicer.classes import RowStyle, ColumnStyle, TableStyle

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
    # the start (exactly at the deleted row) is left as-is - best effort,
    # no #REF!-style error value - the end (past it) shifts up
    assert s["A4"].formula_friendly == "=SUM(A2:A2)"


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


def test_delete_sheet(writable_reader):
    r = writable_reader
    r.add_sheet("Extra")
    r.delete_sheet("Extra")
    assert "Extra" not in r.sheets_names
    with pytest.raises(IndexError):
        r.sheet("Extra")


def test_delete_unknown_sheet_raises(writable_reader):
    with pytest.raises(IndexError):
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
    with pytest.raises(IndexError):
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
    with pytest.raises(IndexError):
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
