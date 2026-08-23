# -*- coding: utf-8 -*-
"""
Consistency checks against a real, local LibreOffice install (via its
`--headless` CLI), not just odsslicer's own BeautifulSoup-based reader.

odsslicer's read path is comparatively lenient - it parses whatever XML is
there. These tests instead hand a file odsslicer *wrote* to actual
LibreOffice (`soffice --headless --convert-to fods`, producing Flat ODF -
a single, human-readable XML file) and inspect what LibreOffice itself
made of it: does it open at all, did it recompute the formula, did the
forked style/merge survive with the right structure. That's the strongest
available signal that a write is genuinely valid ODF, not just
self-consistent with our own reader.

Skipped automatically if no `soffice`/`libreoffice` binary is on PATH (not
installed in CI by default) - install LibreOffice locally to run these.
"""
import re

from conftest import requires_soffice

from odsslicer import ODSReader
from odsslicer.classes import Border


@requires_soffice
def test_libreoffice_opens_a_written_file_and_keeps_values(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "hello"
    s["A2"].value = 42.5
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    fods = libreoffice_export(out, "fods")
    xml = fods.read_text(encoding="utf-8")
    assert "<text:p>hello</text:p>" in xml
    assert 'office:value="42.5"' in xml


@requires_soffice
def test_libreoffice_evaluates_a_written_formula(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = 10.0
    s["A2"].formula = "A1*2"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    # LibreOffice itself opened the file, recomputed the formula, and
    # cached the result on export - proof it parsed table:formula correctly
    assert re.search(r'table:formula="of:=\[\.A1\]\*2"[^>]*office:value="20"', xml)


@requires_soffice
def test_libreoffice_reads_back_a_forked_cell_style(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.value = "styled"
    c.style.bold = True
    c.style.font_color = "#FF0000"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    style_name = c.attrs["table:style-name"]
    style_def = re.search(
        rf'<style:style style:name="{style_name}"[^>]*>.*?</style:style>', xml, re.DOTALL
    )
    assert style_def is not None, f"style {style_name!r} not found in LibreOffice's own re-export"
    assert 'fo:font-weight="bold"' in style_def.group(0)
    assert 'fo:color="#ff0000"' in style_def.group(0)  # LO lowercases hex colors on export


@requires_soffice
def test_libreoffice_reads_back_a_border_with_all_four_sides_explicit(
    writable_reader, tmp_path, libreoffice_export
):
    # setting only .border_top must still show all 4 sides explicitly in
    # LibreOffice's own reading of the file - see the "carry the other 3
    # sides over" behaviour documented on CellStyle
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.value = "bordered"
    c.style.border_top = "0.5pt solid #000000"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    style_name = c.attrs["table:style-name"]
    style_def = re.search(
        rf'<style:style style:name="{style_name}"[^>]*>.*?</style:style>', xml, re.DOTALL
    ).group(0)
    # LibreOffice rounds 0.5pt to its own internal precision (0.51pt) on export
    top = re.search(r'fo:border-top="([^"]*)"', style_def).group(1)
    assert Border(top) == Border("0.51pt solid #000000") or Border(top) == Border(
        "0.5pt solid #000000"
    )
    assert 'fo:border-bottom="none"' in style_def
    assert 'fo:border-left="none"' in style_def
    assert 'fo:border-right="none"' in style_def


@requires_soffice
def test_libreoffice_reads_back_a_merge(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "master"
    s["B2"].value = "hidden"
    s.merge("A1:B2")
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert re.search(
        r'<table:table-cell[^>]*table:number-columns-spanned="2"[^>]*table:number-rows-spanned="2"',
        xml,
    )


@requires_soffice
def test_libreoffice_reads_back_row_column_table_styles(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s.row_style(0).height = "2cm"
    s.column_style(0).width = "5cm"
    s.style.tab_color = "#123456"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert re.search(r'style:row-height="2(\.\d+)?cm"', xml)
    assert re.search(r'style:column-width="5(\.\d+)?cm"', xml)
    assert 'table:tab-color="#123456"' in xml


@requires_soffice
def test_libreoffice_opens_a_document_created_from_scratch(tmp_path, libreoffice_export):
    table = ODSReader.new()
    sheet = table.sheet("Sheet1")
    sheet["A1"].value = "Total"
    sheet["B1"].formula = "SUM(A2:A10)"
    out = tmp_path / "new.ods"
    table.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert "<text:p>Total</text:p>" in xml
    assert "of:=SUM([.A2:.A10])" in xml
