# -*- coding: utf-8 -*-
"""Backwards-compatibility shim.

`odsslicer` used to be a single module (`odsslicer.classes`); it is now split
into focused modules (`addresses`, `constants`, `xmlutils`, `formulas`,
`styles`, `cell`, `sheet`, `properties`, `libreoffice`, `reader`). Everything
that was importable from here still is - `from odsslicer.classes import Sheet`
keeps working - but new code should import from `odsslicer` directly.
"""
from .addresses import string_address, string_to_col
from .cell import ArrayValues, Cell, Comment
from .constants import (
    EMPTY_CELL_BS,
    FORMATS,
    MAX_CELLS_PER_SHEET,
    MAX_CHARS_PER_CELL,
    MAX_COLS_PER_SHEET,
    MAX_REPEAT_COLS,
    MAX_REPEAT_ROWS,
    MAX_ROWS_PER_SHEET,
    MAX_SHEETS,
    RE_STRING_CELL,
    TAG_CELL,
)
from .libreoffice import LIBREOFFICE_COMMAND, recalculate
from .properties import DocumentProperties
from .reader import ODSReader
from .sheet import Sheet
from .styles import Border, CellStyle, ColumnStyle, NumberFormat, RowStyle, TableStyle
