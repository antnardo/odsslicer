"""Cell: reading a value, its text and formula, and writing them in place."""

import datetime as dt
import math

import pytest

from conftest import (
    CELL_ELEMENTS,
    ENCODED_WHITESPACE,
    _fragment,
    encoded_whitespace_ods,
    every,
    first,
    number_cell,
    ods_with_sheet,
    saved_table,
    table_row,
    text_cell,
)
from odsslicer import Cell, ODSReader, xmltree
from odsslicer.xmltree import qn


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
# Writing: Cell.value = ... and ODSReader.save()
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
    tags = every(xml, "table:table-cell")
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
    tag = first(xml, "table:table-cell")
    cell = Cell(tag)
    assert cell.text == ""
    assert str(cell) == ""


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
    tag = first(xml, "table:table-cell")
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
    tag = first(xml, "table:table-cell")
    assert Cell(tag).text == "valeur"


def test_writing_a_multi_line_value_writes_one_paragraph_per_line(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "ligne 1\nligne 2\nligne 3"
    assert s["A1"].text == "ligne 1\nligne 2\nligne 3"
    # ODF has no newline *inside* a paragraph (it is plain whitespace there),
    # so each line must be its own <text:p>, the way applications write it
    assert [xmltree.text(p) for p in xmltree.children(s["A1"].cell, "text:p")] == [
        "ligne 1",
        "ligne 2",
        "ligne 3",
    ]
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    assert ODSReader(out).sheet("Sheet1")["A1"].value == "ligne 1\nligne 2\nligne 3"


class TestStyleName:
    def test_a_cell_with_its_own_style_gives_its_name(self, tmp_path):
        xml = table_row(
            '<table:table-cell table:style-name="ce9" office:value-type="float" office:value="1"/>'
        )
        sheet = ODSReader(ods_with_sheet(tmp_path / "styled.ods", xml)).sheet("Sheet1")
        assert sheet["A1"].style_name == sheet["A1"].cell.get(qn("table:style-name")) == "ce9"

    def test_a_cell_without_one_gives_none(self, tmp_path):
        sheet = ODSReader(ods_with_sheet(tmp_path / "plain.ods", table_row(number_cell(1)))).sheet("Sheet1")
        assert sheet["A1"].style_name is None

    def test_a_formatted_cell_gives_the_style_it_was_forked(self, writable_reader):
        cell = writable_reader.sheet("Sheet1")["A1"]
        cell.style.bold = True
        assert cell.style_name == cell.cell.get(qn("table:style-name"))
        assert cell.style_name is not None


@pytest.mark.parametrize("xml", CELL_ELEMENTS)
def test_cell_text_is_its_paragraphs_joined_whatever_else_it_holds(xml):
    (element,) = _fragment(xml)
    paragraphs = xmltree.children(element, "text:p")
    expected = "\n".join(xmltree.paragraph_text(p) for p in paragraphs) if paragraphs else None
    assert Cell(element).text == expected


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
    assert len(xmltree.children(s["A1"].cell, "text:p")) == len(second.split("\n"))


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
    tag = first(xml, "table:table-cell")
    cell = Cell(tag)
    assert cell.text == "1er / 20"
    assert cell.value == "1er / 20"


def test_a_duration_written_into_a_time_of_day_cell_reads_as_a_time_of_day(writable_reader):
    # .value is at once what reading the cell back gives: see odsslicer/datetimes.py
    s = writable_reader.sheet("Sheet1")
    s["A9"].value = dt.timedelta(hours=9, minutes=30)  # A9: HH:MM:SS, kept
    assert s["A9"].raw_value == "PT09H30M00S"
    assert s["A9"].value == dt.time(9, 30)


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


def test_a_time_repeated_over_columns_reads_as_each_column_says(tmp_path):
    # one element over two columns, the second formatted [HH]:MM as a whole,
    # in a row repeated twice: `load` copies a repeated cell's first reading
    # (`Cell._at`), save a time value's
    repeated = _time_cell().replace(
        "<table:table-cell", '<table:table-cell table:number-columns-repeated="2"', 1
    )
    xml = (
        '<table:table-column/><table:table-column table:default-cell-style-name="elapsed"/>'
        + table_row(repeated, repeat=2)
        + table_row(text_cell("end"))
    )
    s = ODSReader(ods_with_sheet(tmp_path / "times.ods", xml, _ELAPSED_STYLES)).sheet("Sheet1")
    duration, clock = dt.timedelta(hours=7, minutes=30), dt.time(7, 30)
    assert [[cell.value for cell in row] for row in s.rows[:2]] == [[clock, duration], [clock, duration]]


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
    assert qn("table:formula") not in s["A2"].cell.attrib


def test_write_empty_formula_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A2"].formula = ""


def test_save_round_trip_after_writing_a_formula(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "=[.A2]+[.A3]"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["C1"].formula == "of:=[.A2]+[.A3]"
    assert reread["C1"].is_formula
    assert xmltree.attributes(reread["C1"].cell) == {"table:formula": "of:=[.A2]+[.A3]"}


# ---------------------------------------------------------------------------
# The Cell.style setter: a style copied from one cell to another
# ---------------------------------------------------------------------------


def test_assigning_another_cells_style_points_at_the_same_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["B1"].style = s["A1"].style
    assert s["B1"].style_name == s["A1"].style_name
    assert s["B1"].style.bold is True


def test_assigning_a_cell_directly_also_works(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.italic = True
    s["B1"].style = s["A1"]
    assert s["B1"].style.italic is True


def test_assigning_a_bare_style_name_also_works(writable_reader):
    s = writable_reader.sheet("Sheet1")
    name = s["A7"].style_name  # ce2, has a real named style
    s["B1"].style = name
    assert s["B1"].style_name == name


def test_assigning_none_clears_the_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["A1"].style = None
    assert s["A1"].style_name is None


def test_assigning_an_invalid_style_value_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].style = 42


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


def test_removing_a_link_on_part_of_the_text_keeps_the_text_around_it(tmp_path):
    # bs4's unwrap() kept the text after the link; lxml has no unwrap, and
    # the obvious stand-in drops that text (see tests/test_xmltree.py)
    path = ods_with_sheet(
        tmp_path / "linked.ods",
        table_row(
            '<table:table-cell office:value-type="string"><text:p>Voir '
            '<text:a xlink:href="https://example.com">ici</text:a> pour le détail</text:p></table:table-cell>'
        ),
    )
    cell = ODSReader(path).sheet("Sheet1")["A1"]
    assert cell.hyperlink == "https://example.com"
    cell.hyperlink = None
    assert cell.hyperlink is None
    assert xmltree.text(first(cell.cell, "text:p")) == "Voir ici pour le détail"


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
