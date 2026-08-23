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
from odsslicer.classes import Border, NumberFormat


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


@requires_soffice
def test_libreoffice_reads_back_a_created_number_format_and_conditional_formatting(
    writable_reader, tmp_path, libreoffice_export
):
    r = writable_reader
    negative = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
    base = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="€")
    base.add_condition("value()<0", negative)

    s = r.sheet("Sheet1")
    s["A1"].value = -12.5
    s["A1"].style.number_format = base
    out = tmp_path / "out.ods"
    r.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    idx = xml.find('office:value="-12.5"')
    assert idx != -1
    cell_xml = xml[idx - 200 : idx + 300]
    assert "€" in cell_xml
    cell_style_name = re.search(r'table:style-name="([^"]+)"[^>]*office:value="-12.5"', cell_xml).group(1)

    # the cell's style always points at the *base* format (the one holding
    # .conditions/style:map) - LibreOffice itself resolves which variant
    # actually applies for a given value, same as CellStyle.number_format
    # already does on read (see NumberFormat.resolve)
    base_format_name = re.search(
        rf'<style:style style:name="{cell_style_name}"[^>]*style:data-style-name="([^"]+)"', xml
    ).group(1)
    base_format_xml = re.search(
        rf'<number:currency-style style:name="{base_format_name}"[^>]*>.*?</number:currency-style>', xml, re.DOTALL
    ).group(0)
    condition_target = re.search(r'style:apply-style-name="([^"]+)"', base_format_xml).group(1)
    target_xml = re.search(
        rf'<number:currency-style style:name="{condition_target}"[^>]*>.*?</number:currency-style>', xml, re.DOTALL
    ).group(0)
    assert 'fo:color="#ff0000"' in target_xml


@requires_soffice
def test_libreoffice_reads_back_a_sheet_after_delete_row_and_column(
    writable_reader, tmp_path, libreoffice_export
):
    s = writable_reader.sheet("Sheet1")
    s.delete_row(1)
    s.delete_column(1)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert "<text:p>texte simple</text:p>" in xml
    # the deleted row's value (3.4) is nowhere left in the sheet
    assert 'office:value="3.4"' not in xml


@requires_soffice
def test_libreoffice_reads_back_a_copy(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "A2+A3"
    s["C1"].style.bold = True
    s.copy("A1:C1", "E5")
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert "<text:p>texte simple</text:p>" in xml  # A1's value, copied to E5
    # the copied formula's reference shifted by the same offset as the copy
    assert re.search(r'table:formula="of:=\[\.E6\]\+\[\.E7\]"', xml)


@requires_soffice
def test_libreoffice_reads_back_document_properties(writable_reader, tmp_path, libreoffice_export):
    import datetime as dt

    p = writable_reader.properties
    p.title = "Mon classeur de test"
    p.keywords = ["test", "ods", "python"]
    p["Client"] = "Acme Corp"
    p["Montant"] = 42.5
    p["Valide"] = True
    p["Echeance"] = dt.date(2026, 12, 31)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    meta = re.search(r"<office:meta>.*?</office:meta>", xml, re.DOTALL).group(0)
    assert "<dc:title>Mon classeur de test</dc:title>" in meta
    assert meta.count("<meta:keyword>") == 3
    assert '<meta:user-defined meta:name="Client">Acme Corp</meta:user-defined>' in meta
    assert '<meta:user-defined meta:name="Montant" meta:value-type="float">42.5</meta:user-defined>' in meta
    assert '<meta:user-defined meta:name="Valide" meta:value-type="boolean">true</meta:user-defined>' in meta
    assert (
        '<meta:user-defined meta:name="Echeance" meta:value-type="date">2026-12-31</meta:user-defined>' in meta
    )


@requires_soffice
def test_libreoffice_reads_back_a_delete_row_with_adjusted_formulas(
    writable_reader, tmp_path, libreoffice_export
):
    r = writable_reader
    s1 = r.sheet("Sheet1")
    s2 = r.sheet("Sheet2Repeat")
    s1["C5"].formula = "A6+A7"
    s2["A1"].formula = "Sheet1.A6+Sheet1.A7"
    s1.delete_row(3)
    out = tmp_path / "out.ods"
    r.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    # LibreOffice itself parses both the same-sheet and the cross-sheet
    # reference at their new, shifted addresses
    assert re.search(r'table:formula="of:=\[\.A5\]\+\[\.A6\]"', xml)
    assert re.search(r'table:formula="of:=\[Sheet1\.A5\]\+\[Sheet1\.A6\]"', xml)


@requires_soffice
def test_libreoffice_opens_a_value_rendered_from_a_real_format_with_no_example(tmp_path, libreoffice_export):
    # a document with a single cell - genuinely nothing for the
    # learn-by-example heuristic to work from, so the written text comes
    # entirely from _render_number_from_format reading the real NumberFormat
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, "currency", decimal_places=2, currency_symbol="$", grouping=True)
    s["A1"].style.number_format = fmt
    s["A1"].value = 1234.5
    out = tmp_path / "out.ods"
    r.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    # LibreOffice opened it, accepted office:value-type/office:currency, and
    # recomputed its own (locale-formatted) display text from the real format
    # - proof the underlying data (not just our own cached text guess) is valid
    assert 'office:value-type="currency"' in xml
    assert 'office:value="1234.5"' in xml
    assert re.search(r"<text:p>1.234[,.]50\s*\$</text:p>", xml)


@requires_soffice
def test_libreoffice_reads_back_a_comment_without_corrupting_the_value(
    writable_reader, tmp_path, libreoffice_export
):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note\nSur deux lignes"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert "<office:annotation" in xml
    assert "<text:p>Une note</text:p>" in xml
    assert "<text:p>Sur deux lignes</text:p>" in xml
    # LibreOffice itself reads A1's actual value separately from the note
    assert "<text:p>texte simple</text:p>" in xml


@requires_soffice
def test_libreoffice_reads_back_a_sort_with_shifted_formulas(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "Charlie"
    s["B1"].value = 3.0
    s["C1"].formula = "B1*10"
    s["A2"].value = "Alice"
    s["B2"].value = 1.0
    s["C2"].formula = "B2*10"
    s.sort("A1:C2", by=1, ascending=True)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert "<text:p>Alice</text:p>" in xml
    # Alice's row (now row 1) still has a same-row formula
    assert re.search(r'table:formula="of:=\[\.B1\]\*10"[^>]*office:value="10"', xml)


@requires_soffice
def test_libreoffice_reads_back_a_renamed_and_reordered_sheet(writable_reader, tmp_path, libreoffice_export):
    r = writable_reader
    s2 = r.sheet("Sheet2Repeat")
    s2["A1"].formula = "Sheet1.A2"
    r.rename_sheet("Sheet1", "Mon Bilan")
    r.move_sheet("SheetFusion", 0)
    out = tmp_path / "out.ods"
    r.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    # sheet order: SheetFusion first, then the renamed sheet
    names = re.findall(r'<table:table table:name="([^"]+)"', xml)
    assert names[:2] == ["SheetFusion", "Mon Bilan"]
    # the cross-sheet formula follows the rename, correctly quoted
    assert re.search(r"table:formula=\"of:=\[&apos;Mon Bilan&apos;\.A2\]\"", xml)
