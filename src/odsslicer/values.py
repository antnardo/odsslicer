"""A cell's value between Python and ODF: which attribute of the cell element
holds it, by type, and what a value written to a cell becomes there.

ODF keeps a cell's value in one of four attributes - `office:value` for a
number, `office:date-value`, `office:time-value`, `office:boolean-value` -
named by `office:value-type`, and a string in the cell's paragraphs. Writing
a Python value means first deciding what it stands for (`_plain_value`:
numpy's scalars, a `Decimal`, NaN), then which type, attribute and text it
is written as, and what reading it back gives (`_encoded`): an aware
datetime turns naive, in UTC; a time beyond a day reads back as a
`timedelta`. `Cell.value`'s setter did all of it, in 90 lines between the
writes; it now asks here first, so that an unsupported value raises before
anything is written, and keeps the order of the writes - a date's format
before its displayed text, which depends on it.

Nothing here is public (`__all__` is empty): what it decides is read and
written through `Cell.value`.
"""

import datetime as dt
import decimal
import math
import numbers
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
from lxml import etree

from odsslicer.datetimes import _format_date_value, _format_time_value, _parse_date_value, _parse_time_value
from odsslicer.xmltree import qn, unset

__all__: list[str] = []

# The attributes a cell is read from, by namespace URI: `Cell.__init__` runs
# once per cell of a sheet being loaded, where `qn`'s lookup would add up
_VALUE_TYPE = qn("office:value-type")
_VALUE = qn("office:value")
_DATE_VALUE = qn("office:date-value")
_TIME_VALUE = qn("office:time-value")
_BOOLEAN_VALUE = qn("office:boolean-value")
_CALCEXT_VALUE_TYPE = qn("calcext:value-type")
#: the attributes a value is written to, every one cleared before a write
_VALUE_ATTRS = (_VALUE, _DATE_VALUE, _TIME_VALUE, _BOOLEAN_VALUE)
#: the attribute holding a value of each type - `office:value` for the others
_RAW_VALUE_ATTRIBUTES: "dict[str | None, str]" = {
    "date": _DATE_VALUE,
    "time": _TIME_VALUE,
    "boolean": _BOOLEAN_VALUE,
}
#: the number types a number written to a cell keeps
_NUMBER_TYPES = ("float", "percentage", "currency")


def _plain_value(value: object) -> object:
    """`value` as one of the Python types a cell holds, when it stands for
    one (issue #11): numpy's scalars - what pandas hands out, `df.to_numpy()`
    of a frame of integers say - and any other number, a `decimal.Decimal`
    from a database driver, a `fractions.Fraction`. NaN and numpy's NaT are
    missing values: `None`, an empty cell, as pandas writes one to a
    spreadsheet - LibreOffice reads `office:value="nan"` as 0, which sums
    and averages then count. An infinite number has no place in a cell:
    `ValueError`. Anything else comes back as it is.

    numpy's `timedelta64` is registered as an integer: it is looked at
    before integers are (`_plain_numpy_time`). A `str`, an `int`, a `float`
    - most of what is written - are told apart by their type first: the
    checks against numpy's types and the `numbers` ABCs cost more than the
    rest of the write's preparation together."""
    kind = type(value)
    if kind is str or kind is int:
        return value
    if kind is float:
        return _plain_float(cast(float, value))
    if isinstance(value, np.ndarray) and value.ndim == 0:
        value = value[()]
    if isinstance(value, (np.datetime64, np.timedelta64)):
        return _plain_numpy_time(value)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        return str(value)  # numpy's str_ too
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, (numbers.Real, decimal.Decimal)):
        return _plain_float(value)
    return value


def _plain_numpy_time(value: "np.datetime64 | np.timedelta64") -> "dt.date | dt.timedelta | None":
    """A numpy date or duration as `datetime`'s, NaT as `None`. Its `item()`
    gives an int for nanoseconds, or a date out of `datetime`'s range:
    dates and durations go through microseconds first."""
    if np.isnat(value):
        return None
    if isinstance(value, np.timedelta64):
        return value.astype("timedelta64[us]").item()
    days = np.datetime_data(value.dtype)[0] in ("Y", "M", "W", "D")
    converted = value.astype("datetime64[D]" if days else "datetime64[us]").item()
    if not isinstance(converted, dt.date):
        raise ValueError(f"{value!r} is out of the range of datetime.date")
    return converted


def _plain_float(value: "float | numbers.Real | decimal.Decimal") -> "float | None":
    """A real number as a `float`, NaN as `None`; `ValueError` for an
    infinite one."""
    number = float(value)
    if math.isnan(number):
        return None
    if math.isinf(number):
        raise ValueError(f"a cell cannot hold an infinite number: {value!r}")
    return number


@dataclass(frozen=True, slots=True)
class _Encoded:
    """A value as a cell is to hold it: its `office:value-type`, the
    attribute holding it and that attribute's text (`None` for a string,
    held by the paragraphs, or for no value), the value as reading it back
    gives it, and - for a date or a time - the kind of format a cell with
    none gets for it (`dateformats`): "date", "datetime", "time" or
    "duration"."""

    value_type: "str | None"
    attribute: "str | None"
    raw: "str | None"
    value: Any
    kind: "str | None" = None


def _encoded(value: object, current_type: "str | None") -> _Encoded:
    """How `value` - a plain one, see `_plain_value` - is written to a cell
    whose value is now of type `current_type`: a number keeps its cell's
    percentage or currency type. `TypeError` for a value a cell cannot
    hold."""
    if value is None:
        return _Encoded(None, None, None, None)
    if isinstance(value, bool):
        return _Encoded("boolean", _BOOLEAN_VALUE, "true" if value else "false", value)
    if isinstance(value, str):
        return _Encoded("string", None, None, value)
    if isinstance(value, (int, float)):
        value_type = current_type if current_type in _NUMBER_TYPES else "float"
        return _Encoded(value_type, _VALUE, str(value), value)
    if isinstance(value, dt.date):  # a datetime.datetime too
        raw = _format_date_value(value)
        # as reading it back gives it: an aware datetime turns naive, in UTC
        back = _parse_date_value(raw)
        return _Encoded(
            "date", _DATE_VALUE, raw, back, "datetime" if isinstance(back, dt.datetime) else "date"
        )
    if isinstance(value, (dt.time, dt.timedelta)):
        raw = _format_time_value(value)
        # the kind written, not the one read back: 7 h 30 written as a
        # duration shows as one, 07:30:00, where the format of a time of
        # day showed 07:30:00 AM in an en-US document (issue #16). Read
        # back, a time if it fits in a day, else a timedelta
        kind = "duration" if isinstance(value, dt.timedelta) else "time"
        return _Encoded("time", _TIME_VALUE, raw, _parse_time_value(raw), kind)
    raise TypeError(f"unsupported value type for a Cell: {type(value)}")


def _set_value_type(tag: etree._Element, value_type: "str | None") -> None:
    """Write `value_type` as the cell element's `office:value-type`, and as
    the `calcext:value-type` LibreOffice keeps beside it if it has one;
    `None` removes both."""
    if value_type is None:
        unset(tag, _VALUE_TYPE)
    else:
        tag.set(_VALUE_TYPE, value_type)
    if _CALCEXT_VALUE_TYPE in tag.attrib:
        if value_type is None:
            unset(tag, _CALCEXT_VALUE_TYPE)
        else:
            tag.set(_CALCEXT_VALUE_TYPE, value_type)
