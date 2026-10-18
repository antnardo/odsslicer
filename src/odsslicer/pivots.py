"""Pivot tables ("data pilot tables" in ODF): their definition written into
`<table:data-pilot-tables>`, for a spreadsheet to compute.

odsslicer writes only the definition, as it writes formulas without
computing them; unlike a formula, LibreOffice does not recompute a pivot
table on open (see `Sheet.create_pivot_table`). Its fields are a
`_PivotFields`, checked against the source's header row before anything is
written; the whole definition is a `_PivotDefinition`, the record the
element is built from. `Sheet` did all of it in one method of six
parameters, reaching into the reader for the document's list of pivot
tables.

Nothing here is public (`__all__` is empty): its API is
`Sheet.create_pivot_table`.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from lxml import etree

from odsslicer.addresses import string_address
from odsslicer.formulas import (
    _PIVOT_DATA_FUNCTIONS,
    _SHEET_QUALIFIED_RE,
    _quote_odf_sheet_name,
    _unquote_odf_sheet_name,
)
from odsslicer.xmltree import child, children, new, qn

if TYPE_CHECKING:
    import re

    from odsslicer.reader import ODSReader
    from odsslicer.sheet import Sheet

__all__: list[str] = []


@dataclass(frozen=True, slots=True)
class _PivotFields:
    """The fields of a pivot table, by the name their column has in the
    source's header row: row and column categories, and data fields with
    the function aggregating them (`Sheet.create_pivot_table` lists them)."""

    rows: "tuple[str, ...]" = ()
    columns: "tuple[str, ...]" = ()
    values: "tuple[tuple[str, str], ...]" = ()

    @classmethod
    def of(
        cls, rows: "list[str] | None", columns: "list[str] | None", values: "dict[str, str] | None"
    ) -> "_PivotFields":
        return cls(tuple(rows or ()), tuple(columns or ()), tuple((values or {}).items()))

    def check(self, headers: "list[object]") -> None:
        """`ValueError` for a field not in the source's header row
        `headers`, or an aggregation function ODF does not know."""
        for field in (*self.rows, *self.columns, *(field for field, _ in self.values)):
            if field not in headers:
                raise ValueError(f"field {field!r} not found in source header row {headers!r}")
        for field, func in self.values:
            if func not in _PIVOT_DATA_FUNCTIONS:
                raise ValueError(
                    f"unknown aggregation function {func!r} for field {field!r} - "
                    f"expected one of {sorted(_PIVOT_DATA_FUNCTIONS)}"
                )

    def elements(self) -> "list[etree._Element]":
        """A `<table:data-pilot-field>` per field, rows first, then columns,
        then data."""
        fields = [
            *((field, "row", "auto") for field in self.rows),
            *((field, "column", "auto") for field in self.columns),
            *((field, "data", func) for field, func in self.values),
        ]
        return [
            new(
                "table:data-pilot-field",
                {
                    "table:source-field-name": field,
                    "table:orientation": orientation,
                    "table:function": function,
                },
            )
            for field, orientation, function in fields
        ]


@dataclass(frozen=True, slots=True)
class _PivotDefinition:
    """A pivot table as ODF defines it: its name, the cell its result starts
    at and the range it reads, both sheet-qualified addresses, and its
    fields."""

    name: str
    target: str
    source: str
    fields: _PivotFields

    def element(self) -> etree._Element:
        """The `<table:data-pilot-table>` this definition is."""
        pivot = new("table:data-pilot-table")
        pivot.set(qn("table:name"), self.name)
        pivot.set(qn("table:target-range-address"), self.target)
        source_range_tag = new("table:source-cell-range")
        source_range_tag.set(qn("table:cell-range-address"), self.source)
        pivot.append(source_range_tag)
        for field in self.fields.elements():
            pivot.append(field)
        return pivot


class _PivotTables:
    """The pivot tables `sheet` holds the result of."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet

    def create(self, source: str, target: str, fields: _PivotFields, name: "str | None") -> None:
        """`Sheet.create_pivot_table`, which says what it does."""
        sheet = self._sheet
        m = cast("re.Match[str]", _SHEET_QUALIFIED_RE.match(source))
        source_sheet_name = _unquote_odf_sheet_name(m.group("sheet")) if m.group("sheet") else sheet.name
        source_sheet = (
            sheet
            if source_sheet_name == sheet.name
            else cast("ODSReader", sheet.reader).sheet(source_sheet_name)
        )
        row0, row1, col0, col1 = source_sheet._resolve_range(m.group("addr"))
        fields.check([source_sheet._cell(row0, c).value for c in range(col0, col1 + 1)])

        tables = self._container()
        existing = children(tables, "table:data-pilot-table")
        pivot_name = name or f"DataPilotTable{len(existing) + 1}"
        if any(t.get(qn("table:name")) == pivot_name for t in existing):
            raise ValueError(f"a pivot table named {pivot_name!r} already exists")

        target_row0, _, target_col0, _ = sheet._resolve_range(target)
        definition = _PivotDefinition(
            name=pivot_name,
            target=f"{_quote_odf_sheet_name(sheet.name)}.{string_address(target_row0, target_col0)}",
            source=f"{_quote_odf_sheet_name(source_sheet_name)}."
            f"{string_address(row0, col0)}:{string_address(row1, col1)}",
            fields=fields,
        )
        tables.append(definition.element())

    def _container(self) -> etree._Element:
        """The document's `<table:data-pilot-tables>`, made if it has none."""
        spreadsheet = cast(etree._Element, cast("ODSReader", self._sheet.reader)._spreadsheet())
        tables = child(spreadsheet, "table:data-pilot-tables")
        if tables is None:
            tables = new("table:data-pilot-tables")
            spreadsheet.append(tables)
        return tables
