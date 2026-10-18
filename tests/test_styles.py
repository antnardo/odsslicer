"""Style views: `CellStyle`, `RowStyle`, `ColumnStyle`, `TableStyle` and
`Border`, read through the inheritance chain and written on the owner's own
style. They read the document, never a copy of it: two views of one style
agree after either writes (audit before 1.0, part B1)."""

import pytest
from lxml import etree

from conftest import FIXTURES_DIR, first, ods_with_prefixes, ods_with_sheet, table_row
from odsslicer import (
    Border,
    CellStyle,
    ColumnStyle,
    NumberFormat,
    NumberFormatSpec,
    ODSReader,
    RowStyle,
    Sheet,
    TableStyle,
)
from odsslicer.xmltree import qn


@pytest.fixture()
def sheet():
    reader = ODSReader.new()
    sheet = reader.sheet("Sheet1")
    sheet["A1"].value = 1
    return sheet


def test_a_view_made_before_another_writes_reads_the_new_value(sheet):
    a = sheet["A1"].style
    b = sheet["A1"].style
    b.bold = True
    assert a.bold is True
    assert a.name == b.name == sheet["A1"].style.name


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("bold", True, True),
        ("italic", True, True),
        ("underline", True, True),
        ("strikethrough", True, True),
        ("font_family", "Liberation Sans", "Liberation Sans"),
        ("font_color", "#ff0000", "#ff0000"),
        ("font_size", "14pt", "14pt"),
        ("text_position", "super 58%", "super 58%"),
        ("superscript", True, True),
        ("subscript", True, True),
        ("background_color", "#00ff00", "#00ff00"),
        ("vertical_align", "middle", "middle"),
        ("horizontal_align", "center", "center"),
        ("rotation", 90, 90),
        ("writing_mode", "tb-rl", "tb-rl"),
        ("wrap_text", True, True),
        ("shrink_to_fit", True, True),
        ("protection", "protected", "protected"),
        ("diagonal_bl_tr", "0.5pt solid #808080", Border("0.5pt solid #808080")),
        ("diagonal_tl_br", Border("1pt solid #000000"), Border("1pt solid #000000")),
        ("border_top", "0.5pt solid #000000", Border("0.5pt solid #000000")),
        ("border_bottom", "0.5pt solid #000000", Border("0.5pt solid #000000")),
        ("border_left", "0.5pt solid #000000", Border("0.5pt solid #000000")),
        ("border_right", "0.5pt solid #000000", Border("0.5pt solid #000000")),
    ],
)
def test_one_view_writes_another_view_of_the_same_style_reads_it(sheet, name, value, expected):
    reader_view = sheet["A1"].style
    before = getattr(reader_view, name)
    setattr(sheet["A1"].style, name, value)
    assert before != expected
    assert getattr(reader_view, name) == expected


def test_a_view_reads_the_number_format_another_view_assigned(sheet):
    percent = NumberFormat.create(sheet.reader, NumberFormatSpec("percentage", 1))
    a = sheet["A1"].style
    sheet["A1"].style.number_format = percent
    assert a.number_format.name == percent.name
    sheet["A1"].style.number_format = None
    assert a.number_format is None


def test_a_view_resolves_a_conditional_format_against_the_cells_value_now(sheet):
    positive = NumberFormat.create(sheet.reader, NumberFormatSpec("number", 2))
    negative = NumberFormat.create(sheet.reader, NumberFormatSpec("number", 2, font_color="#FF0000"))
    positive.add_condition("value()<0", negative)
    sheet["A1"].style.number_format = positive
    view = sheet["A1"].style
    assert view.number_format.name == positive.name
    sheet["A1"].value = -5
    assert view.number_format.name == negative.name


def test_two_views_see_each_others_writes_both_ways(sheet):
    a = sheet["A1"].style
    b = sheet["A1"].style
    b.bold = True
    a.italic = True
    assert (a.bold, a.italic) == (b.bold, b.italic) == (True, True)


def test_a_border_side_written_through_one_view_keeps_the_sides_another_view_wrote(sheet):
    a = sheet["A1"].style
    b = sheet["A1"].style
    a.border_left = "1pt solid #111111"
    b.border_top = "2pt solid #222222"
    assert a.border_left == Border("1pt solid #111111")
    assert a.border_top == Border("2pt solid #222222")


@pytest.mark.parametrize(
    ("name", "value", "flattened", "key"),
    [
        ("background_color", "#00ff00", "cell_properties", "fo:background-color"),
        ("bold", True, "text_properties", "fo:font-weight"),
    ],
)
def test_the_flattened_properties_of_a_view_follow_another_views_write(sheet, name, value, flattened, key):
    view = sheet["A1"].style
    setattr(sheet["A1"].style, name, value)
    assert key in getattr(view, flattened)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [("height", "2cm", "2cm"), ("optimal_height", False, False), ("visible", False, False)],
)
def test_one_row_style_view_writes_another_reads_it(sheet, name, value, expected):
    view = sheet.row_style(0)
    assert getattr(view, name) != expected
    setattr(sheet.row_style(0), name, value)
    assert getattr(view, name) == expected
    assert view.name == sheet.row_style(0).name


@pytest.mark.parametrize(("name", "value", "expected"), [("width", "5cm", "5cm"), ("visible", False, False)])
def test_one_column_style_view_writes_another_reads_it(sheet, name, value, expected):
    view = sheet.column_style(0)
    assert getattr(view, name) != expected
    setattr(sheet.column_style(0), name, value)
    assert getattr(view, name) == expected
    assert view.name == sheet.column_style(0).name


@pytest.mark.parametrize(
    ("name", "value", "expected"), [("tab_color", "#123456", "#123456"), ("visible", False, False)]
)
def test_one_sheet_style_view_writes_another_reads_it(sheet, name, value, expected):
    view = sheet.style
    assert getattr(view, name) != expected
    setattr(sheet.style, name, value)
    assert getattr(view, name) == expected
    assert view.name == sheet.style.name


@pytest.mark.parametrize(
    ("style_class", "names"),
    [
        (
            CellStyle,
            [
                "bold",
                "italic",
                "underline",
                "strikethrough",
                "font_family",
                "font_color",
                "font_size",
                "text_position",
                "superscript",
                "subscript",
                "background_color",
                "vertical_align",
                "horizontal_align",
                "rotation",
                "writing_mode",
                "wrap_text",
                "shrink_to_fit",
                "protection",
                "diagonal_bl_tr",
                "diagonal_tl_br",
                "border_top",
                "border_bottom",
                "border_left",
                "border_right",
                "number_format",
                "cell_properties",
                "text_properties",
                "name",
            ],
        ),
        (RowStyle, ["height", "optimal_height", "visible", "name"]),
        (ColumnStyle, ["width", "visible", "name"]),
        (TableStyle, ["tab_color", "visible", "name"]),
    ],
)
def test_every_style_property_has_a_docstring(style_class, names):
    assert all(getattr(style_class, name).__doc__ for name in names)


# ---------------------------------------------------------------------------
# Styles, read: Cell.style, CellStyle, NumberFormat
# ---------------------------------------------------------------------------


def test_cell_with_no_style_name_has_a_blank_writable_style(reader):
    # no `table:style-name` at all doesn't mean `.style` is None anymore -
    # writing to it (see the styles-write tests below) needs a real object
    # to fork a style onto, so every property just resolves to None/False
    s = reader.sheet("Sheet1")
    assert s["A1"].style_name is None
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


def test_raw_properties_keep_the_specifications_prefixes_whatever_the_files(tmp_path):
    # keyed as the specification writes them (DOCS.md, "What changes in 1.0"),
    # not with the prefix a file binds - which bs4 handed over as written
    style_uri = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
    path = ods_with_prefixes(FIXTURES_DIR / "TEST.ods", tmp_path / "TEST.ods", {style_uri: "st"})
    style = ODSReader(path).sheet("SheetFusion")["A1"].style
    assert style.cell_properties["style:vertical-align"] == "middle"
    assert style.vertical_align == "middle"
    assert (
        style.cell_properties
        == ODSReader(FIXTURES_DIR / "TEST.ods").sheet("SheetFusion")["A1"].style.cell_properties
    )


def test_raw_properties_of_a_namespace_the_specification_does_not_name_keep_the_files_prefix(tmp_path):
    # LibreOffice's own extensions, `officeooo:rsid` say: no prefix of the
    # specification's to give them, so the file's, as bs4 gave it
    xml = table_row('<table:table-cell table:style-name="x" office:value-type="float" office:value="1"/>')
    styles = (
        '<style:style style:name="x" style:family="table-cell">'
        '<style:text-properties xmlns:officeooo="http://openoffice.org/2009/office"'
        ' officeooo:rsid="0012" fo:color="#ff0000"/></style:style>'
    )
    style = ODSReader(ods_with_sheet(tmp_path / "ext.ods", xml, styles)).sheet("Sheet1")["A1"].style
    assert style.text_properties == {"officeooo:rsid": "0012", "fo:color": "#ff0000"}


def test_cell_style_walks_parent_inheritance_chain():
    from odsslicer import CellStyle

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
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

    style = CellStyle(StubReader(), "Error")
    assert style.bold is True  # set directly on Error
    assert style.font_color == "#ffffff"  # set directly on Error
    assert style.background_color == "#cccccc"  # inherited from Status (1 hop)
    assert style.font_size == "10pt"  # inherited from Default (2 hops)


def test_cell_style_nearest_ancestor_wins_on_conflicting_property():
    from odsslicer import CellStyle

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
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

    style = CellStyle(StubReader(), "Child")
    assert style.background_color == "#222222"


def test_cell_style_font_underline_strikethrough_and_rotation():
    # real attribute names/values taken from tests/TEST.ods's styles.xml
    # (style:font-name="Liberation Sans") and the ODF spec for the rest,
    # since none of our fixture files happen to apply these to a real cell.
    from odsslicer import CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties style:rotation-angle="90"/>'
        '<style:text-properties style:font-name="Liberation Sans" '
        'style:text-underline-style="solid" style:text-line-through-style="solid"/>'
        "</style:style></root>"
    )
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

    style = CellStyle(StubReader(), "ce1")
    assert style.font_family == "Liberation Sans"
    assert style.underline is True
    assert style.strikethrough is True
    assert style.rotation == 90


def test_cell_style_border_shorthand_applies_to_every_side():
    # real value taken from tests/TEST.ods's styles.xml (the unused "Note"
    # built-in style: fo:border="0.74pt solid #808080")
    from odsslicer import CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties fo:border="0.74pt solid #808080"/>'
        "</style:style></root>"
    )
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

    style = CellStyle(StubReader(), "ce1")
    for border in (style.border_top, style.border_bottom, style.border_left, style.border_right):
        assert border.width == "0.74pt"
        assert border.style == "solid"
        assert border.color == "#808080"


def test_cell_style_border_specific_side_overrides_shorthand():
    from odsslicer import Border, CellStyle

    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties fo:border="0.74pt solid #808080" '
        'fo:border-top="2.49pt solid #000000"/>'
        "</style:style></root>"
    )
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

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
    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties fo:wrap-option="wrap" style:shrink-to-fit="true" '
        'style:cell-protect="protected" style:writing-mode="rl-tb"/>'
        '<style:text-properties style:text-position="super 58%"/>'
        "</style:style></root>"
    )
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

    style = CellStyle(StubReader(), "ce1")
    assert style.wrap_text is True
    assert style.shrink_to_fit is True
    assert style.protection == "protected"
    assert style.writing_mode == "rl-tb"
    assert style.text_position == "super 58%"
    assert style.superscript is True
    assert style.subscript is False


def test_cell_style_diagonal_border_parses_like_a_regular_border():
    xml = (
        '<root xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">'
        '<style:style style:name="ce1" style:family="table-cell">'
        '<style:table-cell-properties style:diagonal-tl-br="1pt solid #ff0000"/>'
        "</style:style></root>"
    )
    soup = etree.fromstring(xml)

    class StubCatalog:
        def find_style(self, name, family=None):
            return first(soup, "style:style", style_name=name) if name else None

        def find_number_style(self, name):
            return None

    class StubReader:
        _catalog = StubCatalog()

    style = CellStyle(StubReader(), "ce1")
    assert style.diagonal_tl_br.color == "#ff0000"
    assert style.diagonal_bl_tr is None


# ---------------------------------------------------------------------------
# Styles, read: rows, columns, sheets (RowStyle, ColumnStyle, TableStyle)
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
    xml = (
        '<root xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        '<table:table table:name="T">'
        '<table:table-column/><table:table-row table:style-name="ro1">'
        "<table:table-cell/></table:table-row></table:table></root>"
    )
    table = first(xml, "table:table")
    sheet = Sheet(table)
    assert sheet.row_style(0) is None
    assert sheet.column_style(0) is None
    assert sheet.style is None


# ---------------------------------------------------------------------------
# Styles, written: CellStyle
# ---------------------------------------------------------------------------


def test_setting_bold_forks_a_private_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    assert c.style_name is None
    c.style.bold = True
    forked_name = c.style_name
    assert forked_name is not None
    assert c.style.bold is True

    # a second property set on the same cell reuses the same forked style
    c.style.italic = True
    assert c.style_name == forked_name
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
# Styles, written: RowStyle / ColumnStyle / TableStyle
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


def test_stylename_after_a_table_style_write_names_the_fork(writable_reader):
    # regression: a copy taken on loading, which named the style the sheet
    # had before its own was forked
    s = writable_reader.sheet("Sheet1")
    s.style.tab_color = "#123456"
    assert s.stylename == s.style.name == s.table.get(qn("table:style-name"))


def test_style_write_without_owner_raises_for_row_column_table():
    from odsslicer import ColumnStyle, RowStyle, TableStyle

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
