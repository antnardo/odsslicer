"""Shared constants: ODF cell-type conversions, the cell element names, the
blank cell a position past the sheet reads from, and the thresholds past which
`Sheet.load` leaves the trailing filler of a sheet out of its grid."""

from collections.abc import Callable
from typing import Any

from odsslicer.datetimes import _parse_date_value, _parse_time_value
from odsslicer.xmltree import new

TAG_CELL = ["table:table-cell", "table:covered-table-cell"]
# what a position past the sheet reads from: a blank cell, in no document -
# writing there grows the sheet first (`Cell._prepare_for_write`)
EMPTY_CELL = new("table:table-cell")

# Applications write past the data to declare a sheet's full size, or to format
# whole rows and columns: a run of a million empty rows at the bottom, empty
# cells repeated to the 16,384th column. Unrolled, they would be millions of
# `Cell`s. `Sheet.load` leaves out of the grid, and keeps in the file, a run of
# more than MAX_REPEAT_ROWS repeated empty rows after the last row holding
# data, and a repeated empty cell wider than MAX_REPEAT_ROWS at the end of a
# row; and more than MAX_REPEAT_COLS columns empty in every row at the right,
# which LibreOffice writes as far as the last formatted column. Fewer stay
# in the grid, as blank rows and columns a sheet may mean.
MAX_REPEAT_ROWS = 1_000
MAX_REPEAT_COLS = 10

FORMATS: "dict[str | None, Callable[[Any], Any]]" = {
    "string": str,
    "float": float,
    "percentage": float,
    "currency": float,
    # a datetime.date, or a datetime.datetime when the value has a time part
    "date": _parse_date_value,
    # a datetime.time within a day, a datetime.timedelta beyond (see datetimes.py)
    "time": _parse_time_value,
    # ODF stores booleans as the strings "true"/"false" (office:boolean-value);
    # plain `bool(s)` would treat "false" as truthy since it's a non-empty string.
    "boolean": lambda s: None if s is None else s == "true",
    None: lambda x: None,
}
