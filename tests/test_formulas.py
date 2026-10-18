"""Formulas: the ordinary syntax, templates, the friendly reading, fills."""

import pytest

from conftest import first
from odsslicer import Cell, ODSReader


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
# Writing: formulas templated per cell ({r}/{c}) through Cell.formula
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
# Writing: a formula read in ordinary syntax (Cell.formula_friendly)
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


def test_formula_friendly_translates_a_real_complex_formula():
    # a real formula from bareme/examples/root_init/DS1/Notes.ods (MPX4!I3):
    # of:=IF(OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1])=0;"";
    #        OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1]))
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<table:table-cell table:formula='of:=IF(OFFSET([$Notes.$C$3];[.I$1];"
        '[.$A3]+[.$A$1])=0;"";OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1]))\'>'
        "<text:p/></table:table-cell></root>"
    )
    tag = first(xml, "table:table-cell")
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
# Writing: a formula filled across cells (Cell.fill_formula)
# ---------------------------------------------------------------------------


def test_fill_formula_down_a_column_shifts_relative_rows(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["B2"].formula = "$A1+1"
    s["B2"].fill_formula("B3:B10")
    for row in range(1, 10):  # 0-indexed rows 1..9 == B2..B10
        assert s[row, 1].formula_friendly == f"=$A{row}+1"


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
            assert s[row, col].formula_friendly == "=$A$1*10"


def test_fill_formula_accepts_a_single_cell_address(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A1+1"
    s["C1"].fill_formula("C1")  # no-op shift, single cell (not a range)
    assert s["C1"].formula_friendly == "=$A1+1"


def test_fill_formula_out_of_range_raises(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["B5"].formula = "$A1"
    with pytest.raises(ValueError):
        s["B5"].fill_formula(s[3, 1])  # one row up would need row 0 -> invalid


def test_fill_formula_without_a_formula_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A1"].fill_formula("A2")


def test_filled_formulas_read_back_after_a_save(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["B2"].formula = "$A1+1"
    s["B2"].fill_formula("B3:B5")

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    for row, ref_row in zip(range(1, 4), (1, 2, 3), strict=True):
        assert reread[row, 1].formula_friendly == f"=$A{ref_row}+1"


def test_a_friendly_formula_takes_an_absolute_sheet_and_an_accented_name():
    # regression: left untranslated, "of:=$Data.A5*2", which no application reads
    s = ODSReader.new().sheet("Sheet1")
    s["A1"].formula = "$Data.A5*2+Élèves.B3+SUM($Data.A1:A5)"
    assert s["A1"].formula == "of:=[$Data.A5]*2+[Élèves.B3]+SUM([$Data.A1:.A5])"
    assert s["A1"].formula_friendly == "=$Data.A5*2+Élèves.B3+SUM($Data.A1:A5)"
