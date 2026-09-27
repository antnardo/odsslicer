# -*- coding: utf-8 -*-
import csv
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Make the `odsslicer` package importable when running `pytest` straight from a
# checkout, without an editable install. This file lives at <repo>/tests/conftest.py
# -> parents[1] is the repo root, and the package lives at <repo>/src/odsslicer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from bs4 import BeautifulSoup, Tag

from odsslicer import ODSReader
from odsslicer.addresses import string_address
from odsslicer.xmlutils import _ODF_NAMESPACES

FIXTURES_DIR = Path(__file__).resolve().parent

# A real, local LibreOffice install used for consistency checks (see
# test_libreoffice_consistency.py) - the strongest available signal that a
# file odsslicer wrote is genuinely valid ODF, not just something our own
# (comparatively lenient) BeautifulSoup-based reader happens to parse back.
# Not installed in CI by default, so tests using it skip automatically.
SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")

requires_soffice = pytest.mark.skipif(
    SOFFICE is None, reason="LibreOffice CLI (soffice/libreoffice) not found on PATH"
)


@pytest.fixture(scope="session")
def test_ods_path():
    return FIXTURES_DIR / "TEST.ods"


@pytest.fixture(scope="session")
def reader(test_ods_path):
    return ODSReader(test_ods_path)


@pytest.fixture(scope="session")
def sheet1(reader):
    return reader.sheet("Sheet1")


@pytest.fixture(scope="session")
def sheet_repeat(reader):
    return reader.sheet("Sheet2Repeat")


@pytest.fixture(scope="session")
def sheet_empty(reader):
    return reader.sheet("SheetEmpty")


@pytest.fixture(scope="session")
def sheet_fusion(reader):
    return reader.sheet("SheetFusion")


@pytest.fixture()
def writable_reader(test_ods_path):
    # function-scoped: writes must never leak into the session-scoped
    # `reader`/`sheet1`/... fixtures used by the read-only tests.
    return ODSReader(test_ods_path)


@pytest.fixture()
def workbook(tmp_path, test_ods_path):
    """A copy of TEST.ods, alone in its folder."""
    path = tmp_path / "workbook.ods"
    shutil.copy(test_ods_path, path)
    return path


def convert_with_libreoffice(src_path, fmt, outdir):
    """Convert `src_path` to `fmt` via `soffice --headless --convert-to` -
    a real LibreOffice actually opening and re-exporting the file, not
    just odsslicer reading its own output back. Raises if the conversion
    fails or produces nothing (a strong "this file is invalid ODF"
    signal); returns the produced file's path otherwise.

    `fmt="fods"` (Flat ODF, plain readable XML) is the most useful target
    for inspecting *what* survived - values, formulas (LibreOffice
    recomputes them on export), styles, merges - since it's a single
    human-diffable file rather than another zip. Note LibreOffice quietly
    rounds some measurements to its own internal precision on export
    (e.g. `0.5pt` -> `0.51pt`, `5cm` -> `5.001cm`) - assert on presence/
    prefix rather than exact numeric strings.
    """
    result = subprocess.run(
        [SOFFICE, "--headless", "--convert-to", fmt, "--outdir", str(outdir), str(src_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"soffice --convert-to {fmt} failed:\n{result.stdout}\n{result.stderr}")
    extension = fmt.split(":", 1)[0]  # "csv:<filter>:<options>" writes a .csv
    out_path = Path(outdir) / f"{Path(src_path).stem}.{extension}"
    if not out_path.exists():
        raise RuntimeError(f"soffice did not produce {out_path}:\n{result.stdout}\n{result.stderr}")
    return out_path


@pytest.fixture()
def libreoffice_export(tmp_path):
    def _export(ods_path, fmt="fods"):
        return convert_with_libreoffice(ods_path, fmt, tmp_path)

    return _export


# CSV export options: comma, double quote, UTF-8, from line 1, and token 9,
# "save cell contents as shown" - what LibreOffice displays, not the value
_CSV_AS_SHOWN = (
    "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true,false,false"
)


def libreoffice_shows(ods_path, outdir):
    """The first sheet of `ods_path` as LibreOffice displays it: the text of
    each cell, row by row, as far as its last used row and column."""
    csv_path = convert_with_libreoffice(ods_path, _CSV_AS_SHOWN, outdir)
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


# the namespaces `_fragment` declares on top of odsslicer's own: those of what
# a cell may hold anchored to it, which the blank document declares too
_FRAGMENT_NAMESPACES = {
    **_ODF_NAMESPACES,
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
}


def _fragment(xml):
    """The top-level elements of an ODF XML fragment, detached."""
    declarations = " ".join(
        f'xmlns:{prefix}="{uri}"' for prefix, uri in _FRAGMENT_NAMESPACES.items()
    )
    soup = BeautifulSoup(f"<fragment {declarations}>{xml}</fragment>", "xml")
    root = soup.find("fragment")
    return [child.extract() for child in list(root.children) if isinstance(child, Tag)]


def ods_with_sheet(path, table_xml, styles_xml=""):
    """Save at `path` a document whose only sheet, Sheet1, is made of
    `table_xml` - `<table:table-column>` and `<table:table-row>` elements
    written as another application would write them, since odsslicer's own
    writer never produces most of what a sheet can hold - with
    `styles_xml` added to its automatic styles. Returns `path`."""
    reader = ODSReader.new()
    table = reader.tables[0]
    for child in list(table.children):
        child.extract()
    for element in _fragment(table_xml):
        table.append(element)
    for element in _fragment(styles_xml):
        reader._automatic_styles().append(element)
    reader.save(path)
    return path


def document_in(language, country):
    """A blank document whose default language is `language`-`country`,
    either one absent for `None` - as `ODSReader` reads it: `save()` copies
    `styles.xml`, where it lies, from the template."""
    reader = ODSReader.new()
    default = reader.styles_data.find(
        "style:default-style", attrs={"style:family": "table-cell"}
    )
    props = default.find("style:text-properties")
    for attr, value in (("fo:language", language), ("fo:country", country)):
        if value is None:
            props.attrs.pop(attr, None)
        else:
            props.attrs[attr] = value
    return reader


def text_cell(text):
    """A string cell, as `table_xml` for `ods_with_sheet`."""
    return (
        '<table:table-cell office:value-type="string">'
        f"<text:p>{text}</text:p></table:table-cell>"
    )


def note_cell(text):
    """An empty cell with a note, as `table_xml` for `ods_with_sheet`."""
    return (
        "<table:table-cell><office:annotation><dc:creator>me</dc:creator>"
        f"<text:p>{text}</text:p></office:annotation></table:table-cell>"
    )


def shape_cell(name):
    """An empty cell with a rectangle anchored to it, as LibreOffice writes a
    shape or a chart anchored "to cell"."""
    return (
        f'<table:table-cell><draw:rect draw:name="{name}" svg:width="3cm"'
        ' svg:height="1cm" svg:x="0cm" svg:y="0cm"><text:p/></draw:rect>'
        "</table:table-cell>"
    )


def saved_table(path, index=0):
    """The `<table:table>` element number `index` of the file at `path`, as
    saved: parsed from its content.xml, where `ODSReader` would first clamp
    the rows of the grid to its width."""
    with zipfile.ZipFile(path) as package:
        content = BeautifulSoup(package.read("content.xml"), "xml")
    return content.find_all("table:table")[index]


def addresses_holding(table, test):
    """The addresses of the cells of `table`, a `<table:table>` element, whose
    element passes `test`, where the file lays them out: a repeated element
    counts once for every row and column it stands for."""
    found = []
    row = 0
    for row_tag in table.find_all("table:table-row"):
        rows = int(row_tag.get("table:number-rows-repeated", "1"))
        col = 0
        for cell in row_tag.find_all(
            ["table:table-cell", "table:covered-table-cell"], recursive=False
        ):
            cols = int(cell.get("table:number-columns-repeated", "1"))
            if test(cell):
                found += [
                    string_address(r, c)
                    for r in range(row, row + rows)
                    for c in range(col, col + cols)
                ]
            col += cols
        row += rows
    return found


def empty_cells(repeat=1, style=None):
    """`repeat` empty cells written as one element, as applications do."""
    attrs = f' table:style-name="{style}"' if style else ""
    if repeat > 1:
        attrs += f' table:number-columns-repeated="{repeat}"'
    return f"<table:table-cell{attrs}/>"


def table_row(*cells, repeat=1):
    """A `<table:table-row>` of `cells`, repeated `repeat` times."""
    attrs = f' table:number-rows-repeated="{repeat}"' if repeat > 1 else ""
    return f"<table:table-row{attrs}>{''.join(cells)}</table:table-row>"


def cells_with_content(sheet):
    """`{address: value}` for every cell of `sheet` holding something."""
    return {
        cell.address: cell.value
        for cells in sheet.rows
        for cell in cells
        if not cell.is_empty
    }


# A, B and C 1, 2 and 3 cm wide, A a print title - "Columns to repeat" - as
# LibreOffice writes them: its definition in a group of its own
PRINT_TITLE_XML = (
    '<table:table-header-columns><table:table-column table:style-name="w1"/>'
    "</table:table-header-columns>"
    '<table:table-column table:style-name="w2"/><table:table-column table:style-name="w3"/>'
    + table_row(text_cell("a"), text_cell("b"), text_cell("c"))
)
PRINT_TITLE_WIDTHS_XML = "".join(
    f'<style:style style:name="w{n}" style:family="table-column">'
    f'<style:table-column-properties style:column-width="{n}cm"/></style:style>'
    for n in (1, 2, 3)
)


# A1:B1 filled, rows 2 to 6 one repeated row element - as LibreOffice writes
# a run of identical rows - and A7 filled. Something has to follow the run:
# LibreOffice drops trailing empty rows, which is what hid issue #5.
REPEATED_RUN_XML = (
    '<table:table-column table:number-columns-repeated="2"/>'
    + table_row(text_cell("a"), text_cell("b"))
    + table_row(empty_cells(2), repeat=5)
    + table_row(text_cell("end"), empty_cells())
)


@pytest.fixture()
def repeated_run_ods(tmp_path):
    return ods_with_sheet(tmp_path / "run.ods", REPEATED_RUN_XML)
