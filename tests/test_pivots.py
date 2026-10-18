"""Pivot tables: the definition odsslicer writes, for a spreadsheet to compute."""

import pytest

from conftest import every, first
from odsslicer import ODSReader
from odsslicer.xmltree import qn


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
    tables = first(reader.data, "table:data-pilot-tables")
    return first(tables, "table:data-pilot-table", table_name=name) if tables is not None else None


# ---------------------------------------------------------------------------
# Sheet.create_pivot_table (the ODF definition only, no calculation)
# ---------------------------------------------------------------------------


def test_create_pivot_table_writes_the_definition(writable_reader):
    r = writable_reader
    s = r.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table(
        "A1:C5", "E1", rows=["Category"], columns=["Region"], values={"Amount": "sum"}, name="MyPivot"
    )

    tag = _find_pivot_table(r, "MyPivot")
    assert tag is not None
    assert tag.get(qn("table:target-range-address")) == "SheetEmpty.E1"
    source = first(tag, "table:source-cell-range")
    assert source.get(qn("table:cell-range-address")) == "SheetEmpty.A1:C5"

    fields = every(tag, "table:data-pilot-field")
    by_name = {f.get(qn("table:source-field-name")): f for f in fields}
    assert by_name["Category"].get(qn("table:orientation")) == "row"
    assert by_name["Region"].get(qn("table:orientation")) == "column"
    assert by_name["Amount"].get(qn("table:orientation")) == "data"
    assert by_name["Amount"].get(qn("table:function")) == "sum"


def test_create_pivot_table_no_computed_result_is_written(writable_reader):
    # the whole point: no calculation engine, same as formulas - only the
    # definition is written, the target cell stays untouched/empty
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    s.create_pivot_table("A1:C5", "E1", rows=["Category"], values={"Amount": "sum"})
    assert s["E1"].value is None


def test_create_pivot_table_names_each_table_in_turn_by_default(writable_reader):
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


def test_create_pivot_table_fields_by_position_raise_type_error(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    with pytest.raises(TypeError):
        s.create_pivot_table("A1:C5", "E1", ["Category"], None, {"Amount": "sum"})
    assert _find_pivot_table(writable_reader, "DataPilotTable1") is None


def test_create_pivot_table_unknown_function_raises(writable_reader):
    s = writable_reader.sheet("SheetEmpty")
    _fill_pivot_source(s)
    with pytest.raises(ValueError):
        s.create_pivot_table("A1:C5", "E1", values={"Amount": "bogus"})


def test_create_pivot_table_reads_a_source_on_another_sheet(writable_reader):
    r = writable_reader
    source = r.sheet("SheetEmpty")
    _fill_pivot_source(source)
    target = r.sheet("Sheet1")
    target.create_pivot_table(
        "SheetEmpty.A1:C5", "E1", rows=["Category"], values={"Amount": "sum"}, name="CrossSheetPivot"
    )
    tag = _find_pivot_table(r, "CrossSheetPivot")
    assert tag.get(qn("table:target-range-address")) == "Sheet1.E1"
    assert first(tag, "table:source-cell-range").get(qn("table:cell-range-address")) == "SheetEmpty.A1:C5"


def test_a_pivot_table_reads_back_after_a_save(writable_reader, tmp_path):
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
    assert tag.get(qn("table:target-range-address")) == "SheetEmpty.E1"
    # source data survived untouched
    assert reread.sheet("SheetEmpty")["A2"].value == "A"
