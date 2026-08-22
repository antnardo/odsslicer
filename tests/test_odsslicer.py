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
from odsslicer.classes import ArrayValues, Cell, Sheet


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


def test_number_inference_bails_out_safely_on_a_style_it_cannot_reproduce(writable_reader):
    # if the self-consistency check fails the inferred pattern is discarded, so a
    # cell whose template text doesn't match our simple prefix/number/suffix model
    # falls back to the plain str() conversion rather than producing garbage.
    s = writable_reader.sheet("Sheet1")
    cell = s["A6"]
    cell.cell.find("text:p").string = "deux cents"  # not a model our regex can parse
    cell.__init__(cell.cell, row=cell.row, col=cell.col, sheet=cell.sheet)  # refresh the cache
    cell.value = 0.75
    assert cell.text == "0.75"


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
