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
import datetime as dt
import re

import pytest
from conftest import (
    document_in,
    empty_cells,
    libreoffice_shows,
    ods_with_sheet,
    requires_soffice,
    table_row,
    text_cell,
)

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
def test_libreoffice_keeps_every_property_across_repeated_style_forks(
    writable_reader, tmp_path, libreoffice_export
):
    # regression (issue #1): forking a second time off an automatic style used
    # to produce a file LibreOffice rendered with the last property only - the
    # fork inherited through style:parent-style-name, which LibreOffice honours
    # for named styles only. odsslicer's own read-back could not catch this
    # (it resolves the chain itself), so the assertion has to be on what
    # LibreOffice made of the file.
    s = writable_reader.sheet("Sheet1")
    a, b = s["A1"], s["B1"]
    a.value = "styled"
    b.value = "styled too"
    a.style.bold = True
    a.style.border_left = "2pt solid #000000"
    b.style = a.style  # shared automatic style: writing to b forks off it
    b.style.background_color = "#ffff00"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    style_def = re.search(
        rf'<style:style style:name="{b.attrs["table:style-name"]}"[^>]*>.*?</style:style>',
        xml,
        re.DOTALL,
    )
    assert style_def is not None, "LibreOffice dropped the forked style entirely"
    assert 'fo:background-color="#ffff00"' in style_def.group(0)
    assert 'fo:font-weight="bold"' in style_def.group(0)
    assert re.search(r'fo:border-left="[^"]*solid #000000"', style_def.group(0))


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
    # LibreOffice re-serializes lengths in its profile's measurement unit -
    # cm on an fr-locale machine, inches on the en-US CI runner (2cm =
    # 0.7874in, 5cm = 1.9685in) - so accept either representation
    assert re.search(r'style:row-height="(2(\.\d+)?cm|0\.78\d*in)"', xml)
    assert re.search(r'style:column-width="(5(\.\d+)?cm|1\.9[67]\d*in)"', xml)
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
def test_libreoffice_computes_formulas_and_merges_after_insertions(
    writable_reader, tmp_path, libreoffice_export
):
    # the rewritten references must mean, to LibreOffice itself, the cells
    # they meant before - checked through the results it computes - and a
    # merge straddling the insertion point must come out grown
    r = writable_reader
    s1, s2 = r.sheet("Sheet1"), r.sheet("Sheet2Repeat")
    s1["C1"].formula = "SUM(A2:A3)+$A$7"  # 3.4 + 3 + 2 = 8.4
    s2["A1"].formula = "Sheet1.A6*10"  # 2 * 10 = 20
    fusion = r.sheet("SheetFusion")
    s1.insert_rows(2, 3)  # inside A2:A3, above A6 and A7
    s1.insert_column(0)
    fusion.insert_row(3)  # inside A3:A5
    out = tmp_path / "out.ods"
    r.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert re.search(r'table:formula="of:=SUM\(\[\.B2:\.B6\]\)\+\[\.\$B\$10\]"[^>]*office:value="8.4"', xml)
    assert re.search(r'table:formula="of:=\[Sheet1\.B9\]\*10"[^>]*office:value="20"', xml)
    assert re.search(r'table:number-rows-spanned="4"', xml)


@requires_soffice
def test_libreoffice_opens_a_full_grid_file_after_insertions_without_truncating(tmp_path, libreoffice_export):
    # LibreOffice files declare the full grid through filler rows/columns;
    # inserting into one must not push it past LibreOffice's maximum, which
    # would make it drop data on open
    from conftest import FIXTURES_DIR

    table = ODSReader(FIXTURES_DIR / "wild" / "libreoffice26_linux_streets.ods")
    sheet = table.sheet("Feuille1")
    last_value = sheet[sheet.n_rows - 1, sheet.n_cols - 1].value
    sheet.insert_rows(1, 50)
    sheet.insert_columns(1, 3)
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out = src_dir / "out.ods"
    table.save(out)

    reopened = ODSReader(libreoffice_export(out, "ods")).sheet("Feuille1")
    assert reopened.size == sheet.size
    assert reopened[sheet.n_rows - 1, sheet.n_cols - 1].value == last_value


@requires_soffice
def test_libreoffice_shows_values_written_past_a_run_of_repeated_rows_where_written(
    repeated_run_ods, tmp_path
):
    # issue #5: rows 2 to 6 all showed D = 1 and F = 2, and 3 went to H
    table = ODSReader(repeated_run_ods)
    table.sheet("Sheet1")["D2:D4"].value = [[1], [2], [3]]
    table.save()
    assert libreoffice_shows(repeated_run_ods, tmp_path) == [
        ["a", "b", "", ""],
        ["", "", "", "1"],
        ["", "", "", "2"],
        ["", "", "", "3"],
        ["", "", "", ""],
        ["", "", "", ""],
        ["end", "", "", ""],
    ]


@requires_soffice
def test_libreoffice_shows_values_written_around_empty_columns_where_written(tmp_path):
    # issue #6: Z1 landed in AX1, the 24 empty columns between A and Z being
    # left out of the grid but not out of the file
    path = ods_with_sheet(
        tmp_path / "gap.ods",
        '<table:table-column table:number-columns-repeated="26"/>'
        + table_row(text_cell("a"), empty_cells(24), text_cell("z")),
    )
    table = ODSReader(path)
    table.sheet("Sheet1")["Z1"].value = "new z"
    table.sheet("Sheet1")["B1"].value = "b"
    table.save()
    assert libreoffice_shows(path, tmp_path) == [["a", "b", *[""] * 23, "new z"]]


@requires_soffice
@pytest.mark.parametrize(
    ("language", "country", "shown"),
    [
        ("fr", "FR", ["07/03/22", "07/03/22 13:45", "09:30:00"]),
        ("en", "US", ["03/07/22", "03/07/22 01:45 PM", "09:30:00 AM"]),
        (None, None, ["2022-03-07", "2022-03-07 13:45:30", "09:30:00"]),
    ],
)
def test_libreoffice_shows_dates_written_into_unformatted_cells_as_typed_ones(
    tmp_path, language, country, shown
):
    # issue #7: past the declared column, LibreOffice showed serial numbers
    # (44627); within it, its own fallback, wrapping 128:45 around the clock
    shown = [*shown, "128:45:00", "-01:30:00"]  # durations: the same everywhere
    table = document_in(language, country)
    sheet = table.sheet("Sheet1")
    values = [
        dt.date(2022, 3, 7),
        dt.datetime(2022, 3, 7, 13, 45, 30),
        dt.time(9, 30),
        dt.timedelta(hours=128, minutes=45),
        dt.timedelta(hours=-1, minutes=-30),
    ]
    for row, value in enumerate(values):
        sheet[row, 0].value = value
        sheet[row, 2].value = value
    out = tmp_path / "dates.ods"
    table.save(out)
    shows = libreoffice_shows(out, tmp_path)
    assert shows == [[text, "", text] for text in shown]
    # the text odsslicer caches is what LibreOffice shows
    assert [sheet[row, 0].text for row in range(len(values))] == shown


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
def test_libreoffice_round_trips_a_multi_line_cell(writable_reader, tmp_path, libreoffice_export):
    # a multi-line cell means one <text:p> per line: check LibreOffice keeps
    # all of them (it rewrites the cell its own way on export), and that
    # odsslicer reads back what LibreOffice itself wrote
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "ligne 1\nligne 2\nligne 3"
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out = src_dir / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert "<text:p>ligne 1</text:p><text:p>ligne 2</text:p><text:p>ligne 3</text:p>" in xml
    reopened = ODSReader(libreoffice_export(out, "ods")).sheet("Sheet1")
    assert reopened["A1"].value == "ligne 1\nligne 2\nligne 3"


@requires_soffice
def test_libreoffice_reads_written_date_times_and_durations_as_meant(
    tmp_path, libreoffice_export
):
    # issue #4: the serial number LibreOffice computes from each written
    # value (days since 1899-12-30) proves it reads the value as meant
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    values = [
        (dt.datetime(2023, 11, 30, 13), 45260 + 13 / 24),
        (dt.timedelta(hours=128, minutes=45), 128.75 / 24),
        (dt.timedelta(hours=-1, minutes=-30), -1.5 / 24),
        (dt.time(12, 30, 15, 500_000), (12 * 3600 + 30 * 60 + 15.5) / 86400),
        (
            dt.datetime(2023, 11, 30, 13, tzinfo=dt.timezone(dt.timedelta(hours=5))),
            45260 + 8 / 24,
        ),
    ]
    for row, (value, _) in enumerate(values, start=1):
        s[f"A{row}"].value = value
        s[f"B{row}"].formula = f"A{row}*1"
    out = tmp_path / "out.ods"
    r.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    computed = re.findall(
        r'table:formula="of:=\[\.A\d\]\*1"[^>]*office:value="([^"]*)"', xml
    )
    assert [float(v) for v in computed] == pytest.approx(
        [serial for _, serial in values]
    )


@requires_soffice
def test_odsslicer_reads_back_date_times_and_durations_as_libreoffice_saves_them(
    tmp_path, libreoffice_export
):
    # the other way round: LibreOffice re-saves these cells its own way
    # (hours past a day, a leading minus, a trimmed fraction of a second)
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    dmy_hms = NumberFormat.create(
        r,
        "date",
        components=[
            ("day", "long"),
            ("text", "/"),
            ("month", "long"),
            ("text", "/"),
            ("year", "long"),
            ("text", " "),
            ("hours", "long"),
            ("text", ":"),
            ("minutes", "long"),
            ("text", ":"),
            ("seconds", "long"),
        ],
    )
    hms = NumberFormat.create(
        r,
        "time",
        components=[
            ("hours", "long"),
            ("text", ":"),
            ("minutes", "long"),
            ("text", ":"),
            ("seconds", "long"),
        ],
    )
    values = [
        (dmy_hms, dt.datetime(2023, 11, 30, 13, 0, 0, 500_000)),
        (hms, dt.timedelta(hours=128, minutes=45)),
        (hms, dt.timedelta(hours=-1, minutes=-30)),
        (hms, dt.time(12, 30, 15, 500_000)),
        (dmy_hms, dt.datetime(2023, 11, 30)),
    ]
    for row, (fmt, value) in enumerate(values, start=1):
        s[f"A{row}"].value = value
        s[f"A{row}"].style.number_format = fmt
    # the ods->ods conversion below writes into tmp_path: the source lives elsewhere
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    out = src_dir / "out.ods"
    r.save(out)

    reread = ODSReader(libreoffice_export(out, "ods")).sheet("Sheet1")
    assert [reread[row, 0].value for row in range(4)] == [
        value for _, value in values[:4]
    ]
    assert reread["A2"].raw_value == "PT128H45M00S"
    # LibreOffice saves a date-time falling on midnight as a bare date
    assert reread["A5"].value == dt.date(2023, 11, 30)


@requires_soffice
def test_libreoffice_reads_back_a_comment_without_corrupting_the_value(
    writable_reader, tmp_path, libreoffice_export
):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note\nSur deux lignes"
    src_dir = tmp_path / "src"  # the ods->ods conversion below outputs into
    src_dir.mkdir()             # tmp_path, so the source must live elsewhere
    out = src_dir / "out.ods"
    writable_reader.save(out)

    # round-trip through LibreOffice itself: it re-reads our file and
    # re-writes it as .ods, and odsslicer reads the annotation back from
    # LibreOffice's own output. (Not asserted on the fods export: LibreOffice
    # 24.2 drops annotations from Flat ODF output - an export quirk of that
    # filter, the .ods round-trip is the behavior users actually rely on.)
    roundtripped = libreoffice_export(out, "ods")
    reread = ODSReader(roundtripped).sheet("Sheet1")
    assert reread["A1"].comment is not None
    assert reread["A1"].comment.text == "Une note\nSur deux lignes"
    # and the cell's own value stayed separate from the note
    assert reread["A1"].value == "texte simple"


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


@requires_soffice
def test_libreoffice_reads_back_a_hyperlink(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "Anthropic"
    s["C1"].hyperlink = "https://anthropic.com"
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    assert re.search(r'<text:a xlink:href="https://anthropic\.com/?"[^>]*>Anthropic</text:a>', xml)


@requires_soffice
def test_libreoffice_reads_back_a_pivot_table_definition(writable_reader, tmp_path, libreoffice_export):
    s = writable_reader.sheet("SheetEmpty")
    rows = [("Category", "Region", "Amount"), ("A", "North", 10), ("B", "South", 20)]
    for i, (cat, reg, amt) in enumerate(rows):
        s[i, 0].value = cat
        s[i, 1].value = reg
        s[i, 2].value = amt
    s.create_pivot_table(
        "A1:C3", "E1", rows=["Category"], columns=["Region"], values={"Amount": "sum"}, name="MyPivot"
    )
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    xml = libreoffice_export(out, "fods").read_text(encoding="utf-8")
    # LibreOffice itself parsed, accepted, and re-serialized the definition
    # (it expands the single-cell target to a range and adds its own
    # application-data/buttons/data-pilot-level, all harmless)
    pivot = re.search(r'<table:data-pilot-table table:name="MyPivot".*?</table:data-pilot-table>', xml, re.DOTALL)
    assert pivot is not None
    pivot_xml = pivot.group(0)
    assert 'table:source-field-name="Category" table:orientation="row"' in pivot_xml
    assert 'table:source-field-name="Region" table:orientation="column"' in pivot_xml
    assert 'table:source-field-name="Amount" table:orientation="data" table:function="sum"' in pivot_xml


# ---------------------------------------------------------------------------
# recalculate() / save(recalculate=True): delegate computing to LibreOffice
# ---------------------------------------------------------------------------

def _write_stale_formula_and_pivot(reader):
    s = reader.sheet("Sheet1")
    assert s["A5"].formula_friendly == "=SUM(A2:A3)" and s["A5"].value == 6.4
    s["A2"].value = 100.0  # A5's cached 6.4 is now stale (A3 is 3.0 -> should become 103.0)
    s["C1"].formula = "A2*2"  # fresh formula, no cached value at all
    se = reader.sheet("SheetEmpty")
    for i, (cat, amt) in enumerate([("Category", "Amount"), ("A", 10), ("B", 20), ("A", 5)]):
        se[i, 0].value = cat
        se[i, 1].value = amt
    se.create_pivot_table("A1:B4", "D1", rows=["Category"], values={"Amount": "sum"})


@requires_soffice
def test_save_with_recalculate_computes_stale_and_fresh_formulas(writable_reader, tmp_path):
    _write_stale_formula_and_pivot(writable_reader)
    out = tmp_path / "out.ods"
    writable_reader.save(out, recalculate=True)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["A5"].value == 103.0  # stale cached value was recomputed, not trusted
    assert reread["C1"].value == 200.0  # fresh formula got a value
    assert reread["A5"].formula_friendly == "=SUM(A2:A3)"  # formulas themselves preserved


@requires_soffice
def test_save_with_recalculate_materializes_pivot_tables(writable_reader, tmp_path):
    _write_stale_formula_and_pivot(writable_reader)
    out = tmp_path / "out.ods"
    writable_reader.save(out, recalculate=True)

    se = ODSReader(out).sheet("SheetEmpty")
    # LibreOffice wrote the pivot's output grid at the target (D1): header row,
    # one row per category, a grand total - the part odsslicer never computes
    grid = {(se[i, 3].value, se[i, 4].value) for i in range(se.n_rows)}
    assert ("Category", "Sum - Amount") in grid
    assert ("A", 15.0) in grid
    assert ("B", 20.0) in grid
    assert ("Total Result", 35.0) in grid


@requires_soffice
def test_recalculate_function_on_an_existing_file(writable_reader, tmp_path):
    from odsslicer import recalculate

    _write_stale_formula_and_pivot(writable_reader)
    out = tmp_path / "out.ods"
    writable_reader.save(out)  # plain save: nothing computed yet
    assert ODSReader(out).sheet("Sheet1")["C1"].value is None

    recalculate(out)
    assert ODSReader(out).sheet("Sheet1")["C1"].value == 200.0


@requires_soffice
def test_recalculate_is_idempotent(writable_reader, tmp_path):
    from odsslicer import recalculate

    _write_stale_formula_and_pivot(writable_reader)
    out = tmp_path / "out.ods"
    writable_reader.save(out, recalculate=True)
    recalculate(out)  # a second pass on an already-computed file must work too
    assert ODSReader(out).sheet("Sheet1")["A5"].value == 103.0


# ---------------------------------------------------------------------------
# recalculate(update_links=True): formulas reading another workbook
# ---------------------------------------------------------------------------


def _write_students(path, name):
    table = ODSReader.new("Students")
    students = table.sheet("Students")
    students["A1"].value = "Id"
    students["B1"].value = "Name"
    students["A2"].value = 1.0
    students["B2"].value = name
    table.save(path)


def _workbook_reading(students):
    # Students.B2 of the other file, reached both ways: from an address
    # built at run time (A1), and by a reference written out (A2)
    table = ODSReader.new()
    sheet = table.sheet("Sheet1")
    sheet["B1"].value = f"'{students.as_uri()}'#$Students.A1"
    sheet["A1"].formula = "OFFSET(INDIRECT($B$1);1;1)"
    sheet["A2"].formula = f"of:=['{students.as_uri()}'#$Students.B2]"
    return table


def _names_read(workbook):
    sheet = ODSReader(workbook).sheet("Sheet1")
    return sheet["A1"].value, sheet["A2"].value


@requires_soffice
def test_recalculate_without_update_links_gives_err540_for_other_workbooks(tmp_path):
    from odsslicer import recalculate

    students, workbook = tmp_path / "students.ods", tmp_path / "workbook.ods"
    _write_students(students, "Alice")
    _workbook_reading(students).save(workbook)
    recalculate(workbook)
    assert _names_read(workbook) == ("Err:540", "Err:540")


@requires_soffice
@pytest.mark.parametrize(
    "folder",
    ["workbooks", "Classe d'été (2026) & co"],
    ids=["plain-folder", "folder-named-with-quote-and-parentheses"],
)
def test_save_with_update_links_reads_other_workbooks(tmp_path, folder):
    # LibreOffice has to trust the workbook's folder, and compares folder
    # URLs as strings: ' & ( ), which it leaves unescaped, must match too
    (tmp_path / folder).mkdir()
    students = tmp_path / folder / "students.ods"
    workbook = tmp_path / folder / "workbook.ods"
    _write_students(students, "Alice")
    _workbook_reading(students).save(workbook, recalculate=True, update_links=True)
    assert _names_read(workbook) == ("Alice", "Alice")


@requires_soffice
def test_recalculate_with_update_links_rereads_a_changed_workbook(tmp_path):
    from odsslicer import recalculate

    students, workbook = tmp_path / "students.ods", tmp_path / "workbook.ods"
    _write_students(students, "Alice")
    _workbook_reading(students).save(workbook)
    recalculate(workbook, update_links=True)
    assert _names_read(workbook) == ("Alice", "Alice")
    # the workbook now holds LibreOffice's own copy of what it read from
    # students.ods: the next run must read the file again, not reuse it
    changed = ODSReader(students)
    changed.sheet("Students")["B2"].value = "Alicia"
    changed.save()
    recalculate(workbook, update_links=True)
    assert _names_read(workbook) == ("Alicia", "Alicia")
