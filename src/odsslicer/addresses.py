"""Spreadsheet addresses: column letters and column numbers, and the text
addresses `sheet[...]` accepts ("A1", "A1:B3", "A:B", "1:2") read as the
indexes and slices it takes.

Columns are numbered in bijective base 26 - "Z" is followed by "AA", there
is no digit zero - which is why `string_to_col` and `string_address` are
not a plain change of base. `string_address` and `string_to_col` are public,
exported by the package; `_parse_address` is what indexing a sheet with
text calls, and raises `ValueError` saying what is wrong with an address,
where `Sheet.address`, until 1.0, misread some and raised without a
message."""

import re
from typing import Any

__all__ = ["string_address", "string_to_col"]

# one end of an address: column letters, a row number, or both, in that order
_ADDRESS_END = re.compile(r"(?P<col>[A-Z]+)?(?P<row>[0-9]+)?")
_EXPECTED = (
    'expected upper-case column letters and a row number, as in "A1", "A", "1", "A1:B3", "A:B" or "1:2"'
)


def string_to_col(s: str) -> int:
    """Column letters -> 0-indexed column: "A" -> 0, "Z" -> 25, "AA" -> 26,
    "AZ" -> 51, "BA" -> 52... (the usual spreadsheet bijective base-26)."""
    long = len(s)
    c = [26 ** (long - i - 1) * (ord(char) - 64) for i, char in enumerate(s)]
    return sum(c) - 1


def string_address(row: int, col: int) -> str:
    """0-indexed (row, col) -> "A1"-style address, e.g. (0, 27) -> "AB1"."""
    # bijective base-26 (no digit 0, hence the "n - 1" at each step)
    n = col + 1
    c = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        c = chr(65 + r) + c
    return f"{c}{row + 1}"


def _address_end(address: str, end: str) -> "tuple[int | None, int | None]":
    """`(row, col)` of one end of `address`, 0-indexed, `None` for the part
    it leaves out: "B3" -> (2, 1), "B" -> (None, 1), "3" -> (2, None)."""
    match = _ADDRESS_END.fullmatch(end)
    if not end or match is None:
        raise ValueError(f"{address!r} is not a cell or range address: {_EXPECTED}")
    row = int(match["row"]) - 1 if match["row"] else None
    if row == -1:
        raise ValueError(f"{address!r}: rows are numbered from 1")
    col = string_to_col(match["col"]) if match["col"] else None
    return row, col


def _parse_address(address: str, n_rows: int) -> Any:
    """What `sheet[address]` indexes for a text address, row first:

    - "B3" -> `(2, 1)`, one cell;
    - "3" -> `2`, a row; "B" -> `(slice(n_rows), 1)`, a column, `n_rows`
      long, the sheet's height;
    - "B3:C5" -> `(slice(2, 5), slice(1, 3))`, collapsed to an integer
      along an axis the range spans once: "B3:D3" -> `(2, slice(1, 4))`,
      "B3:B5" -> `(slice(2, 5), 1)`, "B3:B3" -> `(2, 1)`;
    - "B:C" -> `(slice(n_rows), slice(1, 3))`; "3:5" -> `slice(2, 5)`.

    Raises `ValueError` saying what is wrong: an address that is none of
    these, a row numbered 0, a range whose ends are of different kinds
    ("A:2", "A1:B"), or that runs backwards ("B:A", "A3:A1").

    It was `Sheet.address` until 1.0, which read "A:B1" as "A:B" and "A0"
    as the cell in column A of the last row, and raised `TypeError` on
    "A1:B": a check on each end, then on the pair, says what the one
    regular expression over the whole address could not.
    """
    start, colon, stop = address.partition(":")
    row1, col1 = _address_end(address, start)
    if not colon:
        if col1 is None:
            return row1
        if row1 is None:
            return slice(n_rows), col1
        return row1, col1
    row2, col2 = _address_end(address, stop)
    backwards = ValueError(f"{address!r}: a range runs from its first row and column to its last")
    if row1 is None and row2 is None and col1 is not None and col2 is not None:
        if col2 < col1:
            raise backwards
        return slice(n_rows), slice(col1, col2 + 1)
    if col1 is None and col2 is None and row1 is not None and row2 is not None:
        if row2 < row1:
            raise backwards
        return slice(row1, row2 + 1)
    if row1 is None or row2 is None or col1 is None or col2 is None:
        raise ValueError(
            f'{address!r}: a range joins two cells ("A1:B3"), two columns ("A:B") or two rows ("1:2")'
        )
    if row2 < row1 or col2 < col1:
        raise backwards
    rows = row1 if row1 == row2 else slice(row1, row2 + 1)
    cols = col1 if col1 == col2 else slice(col1, col2 + 1)
    return rows, cols
