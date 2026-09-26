"""The formats a date or time written into an unformatted cell gets
(issue #7), and the LibreOffice format codes they are written in."""

import datetime as dt
from types import SimpleNamespace

import pytest

from odsslicer.dateformats import (
    _STANDARD_FORMATS,
    _default_format_code,
    _format_code_components,
)
from odsslicer.styles import _render_date_time_from_format

_DMY = [("day", "long"), ("text", "/"), ("month", "long"), ("text", "/")]


@pytest.mark.parametrize(
    ("code", "components", "elapsed"),
    [
        ("DD/MM/YY", [*_DMY, ("year", "")], False),
        ("DD/MM/YYYY", [*_DMY, ("year", "long")], False),
        (
            "HH:MM:SS AM/PM",
            [
                ("hours", "long"), ("text", ":"), ("minutes", "long"), ("text", ":"),
                ("seconds", "long"), ("text", " "), ("am-pm", ""),
            ],
            False,
        ),
        (
            "[HH]:MM:SS",
            [
                ("hours", "long"), ("text", ":"), ("minutes", "long"), ("text", ":"),
                ("seconds", "long"),
            ],
            True,
        ),
        # M is minutes after hours and before seconds, the month elsewhere
        (
            "HH:MM DD.MM.YY",
            [
                ("hours", "long"), ("text", ":"), ("minutes", "long"), ("text", " "),
                ("day", "long"), ("text", "."), ("month", "long"), ("text", "."),
                ("year", ""),
            ],
            False,
        ),
        (
            'YYYY"년" M"월" D"일" H"시" M"분" S"초"',
            [
                ("year", "long"), ("text", "년 "), ("month", ""), ("text", "월 "),
                ("day", ""), ("text", "일 "), ("hours", ""), ("text", "시 "),
                ("minutes", ""), ("text", "분 "), ("seconds", ""), ("text", "초"),
            ],
            False,
        ),
        # quoted and escaped text is literal, letters included
        (r'D.MM.YYYY" г." \h', [
            ("day", ""), ("text", "."), ("month", "long"), ("text", "."),
            ("year", "long"), ("text", " г. h"),
        ], False),
    ],
)
def test_format_code_components(code, components, elapsed):
    assert _format_code_components(code) == (components, elapsed)


@pytest.mark.parametrize(
    "code",
    [
        "NNNN D MMMM YYYY",
        "DD MMM YY",
        "[NatNum1]YYYY/MM/DD",
        "[$-40C]DD/MM/YY",
        "HH:MM:SS,00",
    ],
)
def test_format_code_components_refuses_what_has_no_numeric_component(code):
    # names of days and months, other digits, calendars: nothing a default
    # date format may need, nor the renderer render
    with pytest.raises(ValueError, match="unsupported"):
        _format_code_components(code)


@pytest.mark.parametrize("tag", sorted(_STANDARD_FORMATS))
def test_every_standard_format_parses(tag):
    for code in _STANDARD_FORMATS[tag]:
        _format_code_components(code)


def _render(code, value):
    components, elapsed = _format_code_components(code)
    family = "time" if isinstance(value, (dt.time, dt.timedelta)) else "date"
    number_format = SimpleNamespace(
        family=family,
        components=[(kind, style or "short") for kind, style in components],
        _tag={"number:truncate-on-overflow": "false"} if elapsed else {},
    )
    return _render_date_time_from_format(number_format, value, family)


_SAMPLE = dt.datetime(2022, 3, 7, 13, 45, 30)


@pytest.mark.parametrize(
    ("tag", "shown"),
    [
        # as LibreOffice 25.8 shows them: a date, a date-time, a time
        ("fr-FR", ("07/03/22", "07/03/22 13:45", "13:45:30")),
        ("en-US", ("03/07/22", "03/07/22 01:45 PM", "01:45:30 PM")),
        ("de-DE", ("07.03.22", "07.03.22 13:45", "13:45:30")),
        ("it-IT", ("07/03/2022", "07/03/2022 13:45", "13:45:30")),
        ("et-EE", ("07.03.2022", "13:45 07.03.22", "13:45:30")),
        ("ja-JP", ("3月7日", "2022/3/7 13:45", "13:45:30")),
        (
            "ko-KR",
            ("2022년 3월 7일", "2022년 3월 7일 13시 45분 30초", "13시 45분 30초"),
        ),
    ],
)
def test_standard_formats_show_as_in_libreoffice(tag, shown):
    date_code, datetime_code, time_code = _STANDARD_FORMATS[tag]
    assert (
        _render(date_code, _SAMPLE.date()),
        _render(datetime_code, _SAMPLE),
        _render(time_code, _SAMPLE.time()),
    ) == shown


@pytest.mark.parametrize(
    ("kind", "language", "country", "expected"),
    [
        ("date", "fr", "FR", ("DD/MM/YY", "fr", "FR")),
        ("datetime", "en", "US", ("MM/DD/YY HH:MM AM/PM", "en", "US")),
        ("time", "en", "GB", ("HH:MM:SS", "en", "GB")),
        # a language alone: its first country in the table
        ("date", "fr", None, ("DD/MM/YY", "fr", "FR")),
        ("date", "pt", None, ("DD-MM-YYYY", "pt", "PT")),
        # a locale LibreOffice writes with another calendar, a country or a
        # language outside the table, no language at all: ISO 8601
        ("date", "ar", "EG", ("YYYY-MM-DD", None, None)),
        ("date", "fr", "SN", ("YYYY-MM-DD", None, None)),
        ("datetime", "zxx", None, ("YYYY-MM-DD HH:MM:SS", None, None)),
        ("time", None, None, ("HH:MM:SS", None, None)),
        # a duration counts its hours in full, in every locale
        ("duration", "fr", "FR", ("[HH]:MM:SS", None, None)),
        ("duration", "en", "US", ("[HH]:MM:SS", None, None)),
    ],
)
def test_default_format_code(kind, language, country, expected):
    assert _default_format_code(kind, language, country) == expected
