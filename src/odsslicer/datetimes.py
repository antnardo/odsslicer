"""ODF date and time cell values: reading and writing `office:date-value` and
`office:time-value`.

ODF borrows both from XML Schema. A date value is an `xsd:date` or an
`xsd:dateTime` (`2023-11-30`, `2023-11-30T13:00:00.5`); a time value is an
`xsd:duration` (`PT128H45M00S`) - a spreadsheet stores a time as a length of
time, which only reads as a time of day when it is shorter than one. Fixed
`strptime` patterns handled nothing but a bare date and a whole-second duration
under 24 hours, and a single date-time or long duration made its whole sheet
unreadable (issue #4). `datetime.fromisoformat` is no way out while Python 3.10
is supported: before 3.11 it rejects `Z` and any fraction of a second that is
not exactly 3 or 6 digits long, and both occur in real files. Hence the two
grammars below.

LibreOffice (checked with 25.8) settles what the specification leaves open:

- it applies a UTC offset and drops it, writing the UTC time back without one,
  a spreadsheet having no time zones. The same happens here, rather than
  handing out aware datetimes that cannot even be compared with the naive ones
  of the next row;
- it refuses a duration with years or months, which have no fixed length. A
  zero one is accepted here, being unambiguous;
- it writes a duration in hours (`PT26H00M00S`, never `P1DT2H`), and a
  date-time that falls on midnight as a bare date.

The Python type follows the value, not the cell's number format, which would
cost a style lookup per cell at load time. A date value with a time part reads
as a `datetime.datetime`, one without as a `datetime.date` - so a column of
date-times holds both wherever LibreOffice dropped a midnight. A duration reads
as a `datetime.time` when it lies within a day, and as a `datetime.timedelta`
otherwise (24 hours or more, or negative): every time value odsslicer could read
before still reads the same, only those that used to raise come back as
timedeltas. `_comparable` puts the types such a column mixes on a common scale.
"""

import datetime as dt
import re
from typing import Any

_DATE_VALUE_RE = re.compile(
    r"(?P<year>-?\d{4,})-(?P<month>\d\d)-(?P<day>\d\d)"
    r"(?:T(?P<hour>\d\d):(?P<minute>\d\d):(?P<second>\d\d)(?P<fraction>[.,]\d+)?)?"
    r"(?:Z|(?P<offset_sign>[+-])(?P<offset_hours>\d\d):(?P<offset_minutes>\d\d))?"
)
# `T(?=\d)`: a time part holds at least one component, as `P` alone does not count
_TIME_VALUE_RE = re.compile(
    r"(?P<sign>-)?P(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<days>\d+)D)?"
    r"(?:T(?=\d)(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)(?P<fraction>[.,]\d+)?S)?)?"
)
_DURATION_PARTS = ("years", "months", "days", "hours", "minutes", "seconds")
_ONE_DAY = dt.timedelta(days=1)


def _microseconds(fraction: str | None) -> int:
    """A fraction of a second (`".5"`, `",123456789"`) rounded to the
    microsecond, Python's resolution - LibreOffice writes up to nine digits."""
    return round(float("0." + fraction[1:]) * 1_000_000) if fraction else 0


def _parse_date_value(raw: str) -> dt.date | dt.datetime:
    """An `office:date-value` as a `datetime.date`, or as a naive
    `datetime.datetime` when it has a time part - in UTC if it came with an
    offset. Raises `ValueError` for anything else."""
    m = _DATE_VALUE_RE.fullmatch(raw)
    if m is None:
        raise ValueError(f"not an ODF date value: {raw!r}")
    date = dt.date(int(m["year"]), int(m["month"]), int(m["day"]))
    if m["hour"] is None:
        return date  # an offset on a bare date changes nothing a spreadsheet shows
    hour, minute, second = int(m["hour"]), int(m["minute"]), int(m["second"])
    micro = _microseconds(m["fraction"])
    # 24:00:00 is XML Schema's end of the day, the next day's midnight
    end_of_day = hour == 24 and not (minute or second or micro)
    if minute > 59 or second > 59 or (hour > 23 and not end_of_day):
        raise ValueError(f"not an ODF date value: {raw!r}")
    try:
        value = _as_datetime(date) + dt.timedelta(
            hours=hour, minutes=minute, seconds=second, microseconds=micro
        )
        if m["offset_sign"]:
            offset = dt.timedelta(hours=int(m["offset_hours"]), minutes=int(m["offset_minutes"]))
            value -= offset if m["offset_sign"] == "+" else -offset
    except OverflowError as error:
        raise ValueError(f"ODF date value out of range: {raw!r}") from error
    return value


def _parse_time_value(raw: str) -> dt.time | dt.timedelta:
    """An `office:time-value` as a `datetime.time` when it lies within a day,
    as a `datetime.timedelta` otherwise. Raises `ValueError` for anything else,
    including a duration in years or months."""
    m = _TIME_VALUE_RE.fullmatch(raw)
    if m is None or all(m[part] is None for part in _DURATION_PARTS):
        raise ValueError(f"not an ODF duration: {raw!r}")
    if int(m["years"] or 0) or int(m["months"] or 0):
        raise ValueError(f"ODF duration in years or months, no fixed length: {raw!r}")
    try:
        value = dt.timedelta(
            days=int(m["days"] or 0),
            hours=int(m["hours"] or 0),
            minutes=int(m["minutes"] or 0),
            seconds=int(m["seconds"] or 0),
            microseconds=_microseconds(m["fraction"]),
        )
    except OverflowError as error:
        raise ValueError(f"ODF duration out of range: {raw!r}") from error
    if m["sign"]:
        value = -value
    if dt.timedelta(0) <= value < _ONE_DAY:
        return (dt.datetime.min + value).time()
    return value


def _format_date_value(value: dt.date) -> str:
    """The `office:date-value` of a `datetime.date` or `datetime.datetime`. A
    date-time keeps its time part even at midnight, so that it reads back as
    one; an aware one is written in UTC, as LibreOffice would read it anyway."""
    if not isinstance(value, dt.datetime):
        return value.isoformat()
    if value.utcoffset() is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    fraction = f".{value.microsecond:06d}".rstrip("0") if value.microsecond else ""
    return value.isoformat(timespec="seconds") + fraction


def _format_time_value(value: dt.time | dt.timedelta) -> str:
    """The `office:time-value` of a time of day or a duration, as LibreOffice
    writes it: in hours however many (`PT128H45M00S`), minus sign first when
    negative. A time of day's time zone, if any, is ignored."""
    sign, hours, minutes, seconds, micro = _clock_parts(_as_timedelta(value))
    fraction = f".{micro:06d}".rstrip("0") if micro else ""
    return f"{sign}PT{hours:02d}H{minutes:02d}M{seconds:02d}{fraction}S"


def _duration_text(value: dt.timedelta) -> str:
    """A duration as LibreOffice's `[HH]:MM:SS` format shows it (`128:45:00`,
    `-01:30:00`), for its displayed text when nothing in the document says
    otherwise - a fraction of a second shown as `time.isoformat()` shows it."""
    sign, hours, minutes, seconds, micro = _clock_parts(value)
    fraction = f".{micro:06d}" if micro else ""
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}{fraction}"


def _clock_parts(value: dt.timedelta) -> tuple[str, int, int, int, int]:
    """`(sign, hours, minutes, seconds, microseconds)` of a duration, whole
    days counted in the hours."""
    sign = "-" if value < dt.timedelta(0) else ""
    value = abs(value)
    hours, rest = divmod(value.days * 86_400 + value.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return sign, hours, minutes, seconds, value.microseconds


def _as_timedelta(value: dt.time | dt.timedelta) -> dt.timedelta:
    """A time of day as the duration since midnight - what ODF stores it as
    anyway; a duration as itself."""
    if isinstance(value, dt.timedelta):
        return value
    return dt.timedelta(
        hours=value.hour,
        minutes=value.minute,
        seconds=value.second,
        microseconds=value.microsecond,
    )


def _as_datetime(value: dt.date) -> dt.datetime:
    """A date as its midnight; a date-time as itself."""
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.combine(value, dt.time())


def _comparable(value: Any) -> Any:
    """`value` on a scale where the types a column of dates or of times can
    mix compare with each other: a date as its midnight, a time of day as the
    duration since midnight. Any other value is returned as is."""
    if isinstance(value, dt.time):
        return _as_timedelta(value)
    if isinstance(value, dt.date):
        return _as_datetime(value)
    return value
