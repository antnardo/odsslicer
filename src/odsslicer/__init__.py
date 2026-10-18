"""odsslicer: read and write `.ods` spreadsheets with numpy-style indexing.

    from odsslicer import ODSReader

    reader = ODSReader("workbook.ods")
    sheet = reader.sheet("Sheet1")
    sheet["A1"].value               # a typed value: str, float, bool, date, time... or None
    sheet["A1:B3"].to_numpy()       # any block, row first, as numpy indexes
    sheet["C1"].formula = "SUM(A1:A10)"
    reader.save("out.ods", recalculate=True)   # LibreOffice computes the formulas

`ODSReader` holds the document and its `Sheet`s; indexing a sheet gives a
`Cell`, or an `ArrayValues` of them for a range. Styles, number formats,
comments and the document's properties are read and written through the
classes below. odsslicer has no calculation engine: `recalculate` hands a
file to a local LibreOffice, and `find_libreoffice`, `libreoffice_command`
and `libreoffice_environment` let other code run it the same way.

Everything public is importable from here, and the submodules are the
implementation, but for `xmltree`: its `qn` spells the namespaced names to
search the XML escape hatches with (`reader.data`, `sheet.table`,
`cell.cell`, lxml trees and elements). DOCS.md documents every feature with
an example.
"""

from odsslicer.addresses import string_address, string_to_col
from odsslicer.cell import Cell
from odsslicer.comment import Comment
from odsslicer.constants import FORMATS
from odsslicer.libreoffice import (
    LIBREOFFICE_COMMAND,
    find_libreoffice,
    libreoffice_command,
    libreoffice_environment,
    recalculate,
)
from odsslicer.numberformat import NumberFormat, NumberFormatSpec
from odsslicer.package import EncryptedDocumentError, MalformedDocumentError
from odsslicer.properties import DocumentProperties
from odsslicer.reader import ODSReader
from odsslicer.selection import ArrayValues
from odsslicer.sheet import Sheet
from odsslicer.styles import Border, CellStyle, ColumnStyle, RowStyle, TableStyle

__all__ = [
    "FORMATS",
    "LIBREOFFICE_COMMAND",
    "ArrayValues",
    "Border",
    "Cell",
    "CellStyle",
    "ColumnStyle",
    "Comment",
    "DocumentProperties",
    "EncryptedDocumentError",
    "MalformedDocumentError",
    "NumberFormat",
    "NumberFormatSpec",
    "ODSReader",
    "RowStyle",
    "Sheet",
    "TableStyle",
    "find_libreoffice",
    "libreoffice_command",
    "libreoffice_environment",
    "recalculate",
    "string_address",
    "string_to_col",
]
