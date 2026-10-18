"""ArrayValues: a selection of a sheet - a row, a column, a block - as
`sheet[...]` returns it, read and written as one.

Split from `cell.py` at 1.0's audit: a selection is no cell but cells, and
writing to one decides how a value spreads over them - the same value to
every cell, or a sequence unpacked cell by cell (`_is_broadcastable_scalar`)
- before each `Cell` writes its own.
"""

import datetime as dt
import decimal
import numbers
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from odsslicer.cell import Cell

__all__ = ["ArrayValues"]


def _is_broadcastable_scalar(value: object) -> bool:
    """True for a value that should be written as-is to every cell of a
    multi-cell selection, rather than unpacked element-wise (a `str` is
    iterable but clearly meant as one value, not one cell per character).
    numpy's scalars count, and a numpy array of no dimension (issue #11)."""
    return (
        value is None
        or isinstance(
            value,
            (str, numbers.Number, decimal.Decimal, np.generic, dt.date, dt.time, dt.timedelta),
        )
        or (isinstance(value, np.ndarray) and value.ndim == 0)
    )


class ArrayValues:
    def __init__(self, array: Any) -> None:
        self.array = array
        self.dimension = self._get_dimension(array)
        self.size = self._get_size()

    def _get_size(self) -> tuple[int, ...]:
        size = []
        cut = self.array
        for _ in range(self.dimension):
            size.append(len(cut))
            if not cut:
                break  # an empty level has nothing deeper to measure
            cut = cut[0]
        return tuple(size)

    @classmethod
    def _get_dimension(cls, array: object) -> int:
        """How deeply the selection is nested: 0 for a single `Cell`, 1 for a
        row/column, 2 for a block - measured from the first element, since
        every level holds the same kind."""
        try:
            return cls._get_dimension(array[0]) + 1  # type: ignore[index]
        except IndexError:
            # an empty list is an empty *selection* - every row of a sheet
            # with no rows, say - so it is a (zero-length) row, not a cell
            return 1
        except TypeError:
            return 0  # a Cell: not indexable, so this is the bottom

    def _iter_cells(self) -> "Iterator[Cell]":
        """Yield every underlying `Cell`, regardless of this selection's
        dimension (a single cell, a row/column, or a 2D block)."""
        if self.dimension == 0:
            yield self.array
        else:
            for item in self.array:
                yield from ArrayValues(item)._iter_cells()

    def __repr__(self) -> str:
        return f"ArrayValue({self.array})"

    def __getitem__(self, i: int) -> Any:
        return self.array[i]

    def __len__(self) -> int:
        return len(self.array)

    def to_numpy(self) -> Any:
        return np.array(self.to_list())

    @property
    def value(self) -> Any:
        return self.cell.value

    @value.setter
    def value(self, new_value: Any) -> None:
        if self.dimension == 0:
            self.cell.value = new_value
            return
        if _is_broadcastable_scalar(new_value):
            for item in self.array:
                ArrayValues(item).value = new_value
            return
        values = list(new_value)
        if len(values) != len(self.array):
            raise ValueError(f"shape mismatch: {len(self.array)} cell(s) but {len(values)} value(s) given")
        for item, v in zip(self.array, values, strict=True):
            ArrayValues(item).value = v

    @property
    def formula(self) -> Any:
        return self.cell.formula

    @formula.setter
    def formula(self, new_formula: "str | None") -> None:
        """Write a formula to every cell in this selection.

        For a multi-cell selection, the same pattern is applied to each cell -
        if it contains `{r}`/`{c}` placeholders (see `Cell.formula`), each
        cell expands them using its own row/column, so e.g. writing the
        pattern `"$A{r-1}+1"` across `sheet["A2:A10"]` makes A2 reference A1,
        A3 reference A2, and so on.
        """
        if self.dimension == 0:
            self.cell.formula = new_formula
            return
        for item in self.array:
            ArrayValues(item).formula = new_formula

    @property
    def formula_friendly(self) -> Any:
        return self.cell.formula_friendly

    @property
    def cell(self) -> "Cell":
        assert self.dimension == 0
        return self.array

    def to_list(self) -> Any:
        if self.dimension == 0:
            return self.value
        if self.dimension == 1:
            return [cell.value for cell in self.array]
        if self.dimension == 2:
            return [[cell.value for cell in row] for row in self.array]
        raise ValueError(f"a selection cannot be {self.dimension} levels deep")

    def to_vector(self) -> "ArrayValues":
        assert self.dimension == 2 and self.size[1] == 1
        return ArrayValues([row[0] for row in self.array])

    def __eq__(self, array: Any) -> bool:
        return array.to_list() == self.to_list()
