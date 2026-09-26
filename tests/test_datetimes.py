"""The ODF date and duration grammars of `odsslicer.datetimes` (issue #4).

The raw values below are the forms LibreOffice 25.8 writes, or reads as such:
the expectations for time zones, `24:00:00`, years or months in a duration and
the missing seconds of a date-time follow what LibreOffice made of each one.
"""

import datetime as dt

import pytest

from odsslicer.datetimes import (
    _comparable,
    _duration_text,
    _format_date_value,
    _format_time_value,
    _parse_date_value,
    _parse_time_value,
)

UTC_PLUS_5 = dt.timezone(dt.timedelta(hours=5))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-11-30", dt.date(2023, 11, 30)),
        ("2023-11-30T13:00:00", dt.datetime(2023, 11, 30, 13)),
        ("2023-11-30T00:00:00", dt.datetime(2023, 11, 30)),
        ("2023-11-30T13:00:00.5", dt.datetime(2023, 11, 30, 13, 0, 0, 500_000)),
        ("2023-11-30T13:00:00,5", dt.datetime(2023, 11, 30, 13, 0, 0, 500_000)),
        ("2023-11-30T13:00:00.12346", dt.datetime(2023, 11, 30, 13, 0, 0, 123_460)),
        ("2023-11-30T13:00:00.123456789", dt.datetime(2023, 11, 30, 13, 0, 0, 123_457)),
        ("2023-11-30T23:59:59.9999999", dt.datetime(2023, 12, 1)),
        ("2023-11-30T24:00:00", dt.datetime(2023, 12, 1)),
        ("2023-11-30T13:00:00Z", dt.datetime(2023, 11, 30, 13)),
        ("2023-11-30T13:00:00+05:00", dt.datetime(2023, 11, 30, 8)),
        ("2023-11-30T23:30:00-01:00", dt.datetime(2023, 12, 1, 0, 30)),
        ("2023-11-30Z", dt.date(2023, 11, 30)),
        ("1899-12-30T09:30:00", dt.datetime(1899, 12, 30, 9, 30)),
        ("0001-01-01", dt.date(1, 1, 1)),
    ],
)
def test_parse_date_value_reads_dates_and_date_times(raw, expected):
    value = _parse_date_value(raw)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "30/11/2023",
        "2023-11-30T13:00",  # no seconds: LibreOffice refuses it too
        "2023-11-30 13:00:00",
        "2023-13-01",
        "2023-02-30",
        "2023-11-30T25:00:00",
        "2023-11-30T24:00:01",
        "2023-11-30T13:60:00",
        "0000-01-01",
        "-0001-01-01",
        "10000-01-01",
        "9999-12-31T24:00:00",
    ],
)
def test_parse_date_value_rejects_anything_else(raw):
    with pytest.raises(ValueError):
        _parse_date_value(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PT09H30M00S", dt.time(9, 30)),
        ("PT9H", dt.time(9)),
        ("PT90M", dt.time(1, 30)),
        ("PT0S", dt.time(0)),
        ("-PT0S", dt.time(0)),
        ("PT12H30M15.5S", dt.time(12, 30, 15, 500_000)),
        ("PT9H30M00,5S", dt.time(9, 30, 0, 500_000)),
        ("PT12H30M15.123456789S", dt.time(12, 30, 15, 123_457)),
        ("P0Y0M0DT9H", dt.time(9)),
        ("PT24H", dt.timedelta(days=1)),
        ("PT23H59M59.9999999S", dt.timedelta(days=1)),
        ("PT128H45M00S", dt.timedelta(hours=128, minutes=45)),
        ("P1DT2H", dt.timedelta(hours=26)),
        ("-PT01H30M00S", dt.timedelta(hours=-1, minutes=-30)),
        ("PT1086253H00M00S", dt.timedelta(hours=1_086_253)),
    ],
)
def test_parse_time_value_reads_a_time_or_else_a_timedelta(raw, expected):
    value = _parse_time_value(raw)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "P",
        "PT",
        "PT5",
        "PTH",
        "T9H",
        "PT9H30",
        "09:30:00",
        "PT1.5H",  # only seconds take a fraction
        "P1Y",  # years and months have no fixed length
        "P1M",
        "P99999999999D",
    ],
)
def test_parse_time_value_rejects_anything_else(raw):
    with pytest.raises(ValueError):
        _parse_time_value(raw)


@pytest.mark.parametrize(
    ("value", "raw"),
    [
        (dt.date(2023, 11, 30), "2023-11-30"),
        (dt.datetime(2023, 11, 30, 13), "2023-11-30T13:00:00"),
        (dt.datetime(2023, 11, 30), "2023-11-30T00:00:00"),
        (dt.datetime(2023, 11, 30, 13, 0, 0, 500_000), "2023-11-30T13:00:00.5"),
        (dt.datetime(2023, 11, 30, 13, 0, 0, 123_456), "2023-11-30T13:00:00.123456"),
        (dt.datetime(2023, 11, 30, 13, tzinfo=UTC_PLUS_5), "2023-11-30T08:00:00"),
    ],
)
def test_format_date_value_writes_what_libreoffice_reads(value, raw):
    assert _format_date_value(value) == raw


@pytest.mark.parametrize(
    ("value", "raw"),
    [
        (dt.time(8, 30), "PT08H30M00S"),
        (dt.time(12, 30, 15, 500_000), "PT12H30M15.5S"),
        (dt.timedelta(0), "PT00H00M00S"),
        (dt.timedelta(hours=128, minutes=45), "PT128H45M00S"),
        (dt.timedelta(days=1, hours=2), "PT26H00M00S"),
        (dt.timedelta(hours=-1, minutes=-30), "-PT01H30M00S"),
    ],
)
def test_format_time_value_writes_hours_the_way_libreoffice_does(value, raw):
    assert _format_time_value(value) == raw


@pytest.mark.parametrize(
    "value",
    [
        dt.date(2023, 11, 30),
        dt.datetime(2023, 11, 30),
        dt.datetime(2023, 11, 30, 13, 5, 9, 1),
        dt.datetime(1, 1, 1),
    ],
)
def test_a_date_value_round_trips(value):
    assert _parse_date_value(_format_date_value(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        dt.time(0),
        dt.time(23, 59, 59, 999_999),
        dt.timedelta(days=1),
        dt.timedelta(hours=128, minutes=45, microseconds=1),
        dt.timedelta(hours=-1, minutes=-30),
        dt.timedelta(days=-3, microseconds=5),
    ],
)
def test_a_time_value_round_trips(value):
    assert _parse_time_value(_format_time_value(value)) == value


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.timedelta(hours=128, minutes=45), "128:45:00"),
        (dt.timedelta(hours=-1, minutes=-30), "-01:30:00"),
        (dt.timedelta(hours=26, microseconds=500_000), "26:00:00.500000"),
    ],
)
def test_duration_text_counts_every_hour(value, text):
    assert _duration_text(value) == text


def test_comparable_orders_dates_among_date_times():
    values = [
        dt.datetime(2023, 11, 30, 8),
        dt.date(2023, 11, 30),
        dt.datetime(2023, 11, 29, 13),
    ]
    assert sorted(values, key=_comparable) == [values[2], values[1], values[0]]


def test_comparable_orders_times_among_durations():
    values = [dt.timedelta(hours=26), dt.time(9), dt.timedelta(hours=-1)]
    assert sorted(values, key=_comparable) == [values[2], values[1], values[0]]


def test_comparable_leaves_other_values_alone():
    assert [_comparable(v) for v in (None, "x", 1.5, True)] == [None, "x", 1.5, True]
