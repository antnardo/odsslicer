"""Style views read the document, never a copy of it: two views of one style
agree after either writes (audit before 1.0, part B1). The rest of the style
tests still live in test_odsslicer.py."""

import pytest

from odsslicer import Border, CellStyle, ColumnStyle, NumberFormat, ODSReader, RowStyle, TableStyle


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
    percent = NumberFormat.create(sheet.reader, "percentage", 1)
    a = sheet["A1"].style
    sheet["A1"].style.number_format = percent
    assert a.number_format.name == percent.name
    sheet["A1"].style.number_format = None
    assert a.number_format is None


def test_a_view_resolves_a_conditional_format_against_the_cells_value_now(sheet):
    positive = NumberFormat.create(sheet.reader, "number", 2)
    negative = NumberFormat.create(sheet.reader, "number", 2, font_color="#FF0000")
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
