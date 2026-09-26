"""The format a date or time written into a cell with no number format gets.

A date value with no format is a bare serial number to LibreOffice: past the
sheet's column definitions it shows as one (44627 for 2022-03-07), and in a
declared column it falls back to a date-and-time format, a duration of 128
hours then showing as 08:45:00 (issue #7). Typing a date instead gets the cell
the standard format of the document's locale, the format LibreOffice's own
number formatter returns for a date typed as the locale writes it (checked
against its source, `SvNFEngine::IsNumberFormat`, and by typing into a cell):

- a date: the locale's short date, `DD/MM/YY` in fr-FR, `MM/DD/YY` in en-US;
- a date-time: the same with the time, `DD/MM/YY HH:MM`;
- a time of day: `HH:MM:SS`, `HH:MM:SS AM/PM` in en-US;
- a duration of a day or more, or negative: `[HH]:MM:SS`, in every locale.

odsslicer gives a date written into a cell with no format the same one, the
locale being the document's default language. The formats come from a table,
not from the platform: Python's `locale` module only knows the locales
installed on the machine, and CLDR (through Babel) is a dependency that does
not even agree with LibreOffice - its French short date has a 4-digit year.
The table holds LibreOffice 25.8's standard formats for the locales most
spreadsheets are written in, generated from LibreOffice itself and each one
checked against what LibreOffice displays for a sample date-time. A locale
it lacks, or a document with no language, gets ISO 8601, as does a locale
whose formats LibreOffice writes with another calendar or other digits
(Arabic, Persian) or whose AM/PM markers are not "AM" and "PM" (Vietnamese).

The formats are written in LibreOffice's own format codes, with its English
keywords, which the rest of odsslicer turns into the components of a
`NumberFormat` (see `NumberFormat.create`).
"""

import re

# a date, a date-time, a time of day, by language-COUNTRY: generated from
# LibreOffice 25.8 (`getStandardFormat` for each type and locale, its
# localized keywords turned back into English), each checked against what it
# displays for 2022-03-07 13:45:30 and 2023-01-01 09:05:07. A language's first
# country is the one a document giving no country gets.
_STANDARD_FORMATS: "dict[str, tuple[str, str, str]]" = {
    "fr-FR": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "fr-BE": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "fr-CA": ("YYYY-MM-DD", "YYYY-MM-DD HH:MM", "HH:MM:SS"),
    "fr-CH": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "fr-LU": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "en-US": ("MM/DD/YY", "MM/DD/YY HH:MM AM/PM", "HH:MM:SS AM/PM"),
    "en-GB": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "en-CA": ("YYYY-MM-DD", "YYYY-MM-DD HH:MM", "HH:MM:SS"),
    "en-AU": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "en-NZ": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "en-IE": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "en-ZA": ("YYYY-MM-DD", "YYYY-MM-DD HH:MM:SS", "HH:MM:SS"),
    "en-IN": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "de-DE": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "de-AT": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "de-CH": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "de-LU": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "es-ES": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "es-MX": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "es-AR": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "es-CO": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "es-CL": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "es-PE": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "es-US": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "it-IT": ("DD/MM/YYYY", "DD/MM/YYYY HH:MM", "HH:MM:SS"),
    "it-CH": ("DD.MM.YYYY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "pt-PT": ("DD-MM-YYYY", "DD-MM-YY HH:MM", "HH:MM:SS"),
    "pt-BR": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "nl-NL": ("DD-MM-YY", "DD-MM-YY HH:MM", "HH:MM:SS"),
    "nl-BE": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "ca-ES": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "gl-ES": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "eu-ES": ("YY/MM/DD", "YY/MM/DD HH:MM", "HH:MM:SS"),
    "sv-SE": ("YYYY-MM-DD", "YY-MM-DD HH:MM", "HH:MM:SS"),
    "sv-FI": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "da-DK": ("DD-MM-YY", "DD-MM-YY HH:MM", "HH:MM:SS"),
    "nb-NO": ("DD.MM.YYYY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "nn-NO": ("DD.MM.YYYY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "fi-FI": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "is-IS": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "pl-PL": ("D.MM.YYYY", "YYYY-MM-DD HH:MM", "HH:MM:SS"),
    "cs-CZ": ("DD.MM.YYYY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "sk-SK": ("DD.MM.YYYY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "hu-HU": ("YYYY-MM-DD", "YY-MM-DD HH:MM", "H:MM:SS"),
    "ro-RO": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "bg-BG": ('D.MM.YYYY" г."', 'D.MM.YYYY" г.", H:MM" ч."', "H:MM:SS"),
    "el-GR": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "hr-HR": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS AM/PM"),
    "sl-SI": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "sr-RS": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "et-EE": ("DD.MM.YYYY", "HH:MM DD.MM.YY", "HH:MM:SS"),
    "lv-LV": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "lt-LT": ("YYYY-MM-DD", "YY-MM-DD HH:MM", "HH:MM:SS"),
    "ru-RU": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "uk-UA": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "be-BY": ("DD.MM.YY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "tr-TR": ("DD.MM.YYYY", "DD.MM.YY HH:MM", "HH:MM:SS"),
    "he-IL": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "ja-JP": ('M"月"D"日"', "YYYY/M/D H:MM", "HH:MM:SS"),
    "ko-KR": (
        'YYYY"년" M"월" D"일"',
        'YYYY"년" M"월" D"일" H"시" M"분" S"초"',
        'H"시" M"분" S"초"',
    ),
    "zh-CN": ('YY"年"M"月"D"日"', "YYYY/MM/DD HH:MM:SS", "HH:MM:SS"),
    "zh-TW": ("YYYY/M/D", 'YYYY"年"M"月"D"日" HH"時"MM"分"', "HH:MM:SS"),
    "zh-HK": ("DD/MM/YY", 'YYYY"年"M"月"D"日" HH"時"MM"分"SS"秒"', "HH:MM:SS"),
    "id-ID": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "ms-MY": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "th-TH": ("DD/MM/YY", "DD/MM/YY HH:MM", "HH:MM:SS"),
    "hi-IN": ("DD-MM-YYYY", "DD-MM-YY HH:MM", "HH:MM:SS"),
}

_ISO_FORMATS = ("YYYY-MM-DD", "YYYY-MM-DD HH:MM:SS", "HH:MM:SS")
_DURATION_FORMAT = "[HH]:MM:SS"

_KINDS = ("date", "datetime", "time")

_CODE_TOKEN_RE = re.compile(
    r'"(?P<quoted>[^"]*)"'  # literal text
    r"|\\(?P<escaped>.)"  # a literal character
    r"|\[(?P<elapsed>H+|M+|S+)\]"  # an elapsed-time field, [HH]:MM:SS
    r"|(?P<ampm>AM/PM)"
    r"|(?P<field>Y+|M+|D+|H+|S+)"
    # day or month names, eras, calendars..., and the 0s of ",00" seconds
    r"|(?P<unsupported>[A-Za-z\[0])"
    r"|(?P<literal>.)",
    re.DOTALL,
)
_FIELD_KINDS = {"Y": "year", "D": "day", "H": "hours", "S": "seconds"}


def _format_code_components(code: str) -> "tuple[list[tuple[str, str]], bool]":
    """The `NumberFormat` components of a LibreOffice date or time format code
    (`"DD/MM/YY HH:MM"`), and whether it counts elapsed time (`[HH]`) rather
    than wrap around the clock. A field's style is `"long"` for `YYYY`, or
    two letters of any other field, and `""` - short, ODF's default - for
    fewer; `M` is minutes after hours or before seconds, and the month
    elsewhere, as LibreOffice reads it. Raises `ValueError` for anything
    else a code can hold: day or month names, eras, calendars, fractions of
    a second."""
    tokens: list[tuple[str, str]] = []
    elapsed = False
    for m in _CODE_TOKEN_RE.finditer(code):
        letters = m["elapsed"] or m["field"]
        if m["unsupported"] or (letters and letters[0] in "MD" and len(letters) > 2):
            raise ValueError(f"unsupported in a default date format: {code!r}")
        if letters:
            elapsed = elapsed or m["elapsed"] is not None
            short = len(letters) <= (2 if letters[0] == "Y" else 1)
            tokens.append((_FIELD_KINDS.get(letters[0], "M"), "" if short else "long"))
        elif m["ampm"]:
            tokens.append(("am-pm", ""))
        else:
            quoted, escaped, literal = m["quoted"], m["escaped"], m["literal"]
            text = quoted if quoted is not None else escaped or literal
            if tokens and tokens[-1][0] == "text":
                tokens[-1] = ("text", tokens[-1][1] + text)
            elif text:
                tokens.append(("text", text))
    fields = [kind for kind, _ in tokens if kind != "text"]
    components = []
    for kind, style in tokens:
        if kind == "M":
            k = fields.index("M")  # this one: the ones before are resolved
            before = fields[k - 1] if k > 0 else None
            after = fields[k + 1] if k + 1 < len(fields) else None
            minutes = before == "hours" or after == "seconds"
            kind = fields[k] = "minutes" if minutes else "month"
        components.append((kind, style))
    return components, elapsed


def _default_format_code(
    kind: str, language: "str | None", country: "str | None"
) -> "tuple[str, str | None, str | None]":
    """The format code a `kind` of value - `"date"`, `"datetime"`, `"time"`,
    or `"duration"` for a timedelta of a day or more, or negative - gets in a
    document of `language` and `country`, with the language and country it
    belongs to, `None` for ISO 8601 and for a duration. A document naming a
    language but no country gets that language's first one in the table."""
    if kind == "duration":
        return _DURATION_FORMAT, None, None
    index = _KINDS.index(kind)
    for tag, formats in _STANDARD_FORMATS.items():
        tag_language, _, tag_country = tag.partition("-")
        if tag_language == language and country in (tag_country, None):
            return formats[index], tag_language, tag_country
    return _ISO_FORMATS[index], None, None
