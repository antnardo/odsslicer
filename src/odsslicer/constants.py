# -*- coding: utf-8 -*-
"""Shared constants: ODF cell-type conversions, size limits, sentinel objects."""

import datetime as dt
import re

from bs4 import BeautifulSoup


RE_STRING_CELL = re.compile(r"([A-Z]+)?([0-9]+)?(:)?([A-Z]+)?([0-9]+)?")
TAG_CELL = ["table:table-cell", "table:covered-table-cell"]
EMPTY_CELL_BS = BeautifulSoup("<table:table-cell/>", "xml")

# https://wiki.documentfoundation.org/Faq/Calc/022
MAX_ROWS_PER_SHEET = 2**20  # way too big for python
MAX_COLS_PER_SHEET = 2**10  # AMJ (can be handled here)
MAX_CELLS_PER_SHEET = 2**30
MAX_CHARS_PER_CELL = 2**15  # ? needs updating
MAX_SHEETS = 10_000

MAX_REPEAT_ROWS = 1_000  # Above this : if row contains one ampty cell : discarded
MAX_REPEAT_COLS = 10  # idem but for every row (much more complicated to detect)

FORMATS = {
    "string": str,
    "float": float,
    "percentage": float,
    "currency": float,
    "date": lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
    "time": lambda s: dt.datetime.strptime(s, "PT%HH%MM%SS").time(),
    # ODF stores booleans as the strings "true"/"false" (office:boolean-value);
    # plain `bool(s)` would treat "false" as truthy since it's a non-empty string.
    "boolean": lambda s: None if s is None else s == "true",
    None: lambda x: None,
}
