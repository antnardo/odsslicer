# -*- coding: utf-8 -*-
"""Spreadsheet address arithmetic: bijective base-26 column letters <-> 0-indexed columns."""


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
    return f"{c}{row+1}"
