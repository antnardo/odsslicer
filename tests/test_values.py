"""What a Python value is written as in a cell, and what reading it back gives."""

import datetime as dt
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pytest

from odsslicer import ODSReader
from odsslicer.xmltree import qn


def test_write_unsupported_type_raises_typeerror(writable_reader):
    class Foo:
        pass

    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].value = Foo()


def test_write_unsupported_type_leaves_the_cell_as_it_was(writable_reader):
    # regression: it raised once the cell had lost its value attributes
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = 3.0
    with pytest.raises(TypeError):
        s["A1"].value = [1, 2]
    assert (s["A1"].value, s["A1"].cell.get(qn("office:value"))) == (3.0, "3.0")


def test_write_unsupported_type_past_the_sheet_leaves_it_its_size(writable_reader):
    # regression: the sheet grew to the cell before the value was refused
    s = writable_reader.sheet("Sheet1")
    size = s.size
    with pytest.raises(TypeError):
        s[s.n_rows + 5, s.n_cols + 5].value = object()
    assert s.size == size


# ---------------------------------------------------------------------------
# Values from numpy - and so pandas - or a database (issue #11): written as
# the value they stand for, as Python types; NaN is a missing value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "written", "read_back"),
    [
        pytest.param(np.int64(1250), 1250, 1250.0, id="int64"),
        pytest.param(np.uint8(7), 7, 7.0, id="uint8"),
        pytest.param(np.bool_(True), True, True, id="bool_"),
        pytest.param(np.float64(1.5), 1.5, 1.5, id="float64"),
        pytest.param(np.float32(0.5), 0.5, 0.5, id="float32"),
        pytest.param(np.longdouble(2.5), 2.5, 2.5, id="longdouble"),
        pytest.param(np.str_("x"), "x", "x", id="str_"),
        pytest.param(np.array(3), 3, 3.0, id="0-d array"),
        pytest.param(
            np.datetime64("2026-09-20"), dt.date(2026, 9, 20), dt.date(2026, 9, 20), id="datetime64[D]"
        ),
        pytest.param(
            np.datetime64("2026-09-20T10:11:12.123456789"),
            dt.datetime(2026, 9, 20, 10, 11, 12, 123456),
            dt.datetime(2026, 9, 20, 10, 11, 12, 123456),
            id="datetime64[ns]",
        ),
        # a duration, in the format of a duration (issue #20)
        pytest.param(
            np.timedelta64(90, "m"),
            dt.timedelta(minutes=90),
            dt.timedelta(minutes=90),
            id="timedelta64",
        ),
        pytest.param(
            np.timedelta64(30, "h"),
            dt.timedelta(hours=30),
            dt.timedelta(hours=30),
            id="timedelta64 past a day",
        ),
        pytest.param(Decimal("2310.50"), 2310.5, 2310.5, id="Decimal"),
        pytest.param(Fraction(1, 4), 0.25, 0.25, id="Fraction"),
    ],
)
def test_a_value_from_numpy_or_a_database_is_written_as_what_it_stands_for(
    tmp_path, value, written, read_back
):
    # regression: TypeError, but for float64 and str_, which stayed numpy's
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = value
    assert s["A1"].value == written
    assert type(s["A1"].value) is type(written)
    r.save(tmp_path / "out.ods")
    back = ODSReader(tmp_path / "out.ods").sheet("Sheet1")["A1"].value
    assert back == read_back
    assert type(back) is type(read_back)


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(np.float64("nan"), id="float64 nan"),
        pytest.param(Decimal("NaN"), id="Decimal NaN"),
        pytest.param(np.datetime64("NaT", "D"), id="datetime64 NaT"),
        pytest.param(np.timedelta64("NaT", "s"), id="timedelta64 NaT"),
    ],
)
def test_a_missing_value_leaves_the_cell_empty(writable_reader, missing):
    # regression: NaN was written as office:value="nan", which LibreOffice
    # reads as 0 - and NaT raised TypeError
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = missing
    assert s["A1"].value is None
    assert s["A1"].is_empty


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("inf"), id="inf"),
        pytest.param(-np.inf, id="-inf"),
        pytest.param(Decimal("Infinity"), id="Decimal Infinity"),
        pytest.param(np.datetime64("20000-01-01"), id="datetime64 past year 9999"),
    ],
)
def test_a_value_no_cell_can_hold_raises_valueerror_and_writes_nothing(writable_reader, value):
    s = writable_reader.sheet("Sheet1")
    before, size = s["A1"].value, s.size
    with pytest.raises(ValueError):
        s["A1"].value = value
    with pytest.raises(ValueError):
        s[size[0] + 5, 0].value = value  # past the grid, which must not grow
    assert s["A1"].value == before
    assert s.size == size


@pytest.mark.parametrize(
    ("value", "raw"),
    [
        (dt.datetime(2023, 11, 30, 13), "2023-11-30T13:00:00"),
        # a date-time even at midnight
        (dt.datetime(2023, 11, 30), "2023-11-30T00:00:00"),
        # the microseconds used to be dropped
        (dt.time(12, 30, 15, 500_000), "PT12H30M15.5S"),
        (dt.timedelta(hours=128, minutes=45), "PT128H45M00S"),
        (dt.timedelta(hours=-1, minutes=-30), "-PT01H30M00S"),
    ],
)
def test_a_written_date_time_or_duration_round_trips(tmp_path, value, raw):
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = value
    assert s["A1"].raw_value == raw
    assert s["A1"].value == value
    out = tmp_path / "out.ods"
    r.save(out)
    reread = ODSReader(out).sheet("Sheet1")
    assert reread["A1"].value == value
    assert type(reread["A1"].value) is type(value)
    assert reread["A1"].format == s["A1"].format


@pytest.mark.parametrize(
    ("value", "read"),
    [
        (dt.timedelta(hours=7, minutes=30), dt.timedelta(hours=7, minutes=30)),
        (dt.time(7, 30), dt.time(7, 30)),
    ],
)
def test_a_value_under_a_day_reads_back_as_the_kind_written(tmp_path, value, read):
    # regression: a duration under a day read back as a time of day - the
    # format it gets now, [HH]:MM:SS, tells it apart (issue #20)
    r = ODSReader.new()
    r.sheet("Sheet1")["A1"].value = value
    assert r.sheet("Sheet1")["A1"].value == read
    r.save(tmp_path / "out.ods")
    back = ODSReader(tmp_path / "out.ods").sheet("Sheet1")["A1"].value
    assert back == read
    assert type(back) is type(read)


def test_an_aware_datetime_is_written_in_utc(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A8"].value = dt.datetime(2023, 11, 30, 13, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    assert s["A8"].raw_value == "2023-11-30T08:00:00"
    assert s["A8"].value == dt.datetime(2023, 11, 30, 8)
