"""Comment: a cell's note."""

import datetime as dt

import pytest

from odsslicer import ODSReader

# ---------------------------------------------------------------------------
# Cell.comment (Comment, office:annotation)
# ---------------------------------------------------------------------------


def test_cell_with_no_comment_is_none(reader):
    s = reader.sheet("Sheet1")
    assert s["A1"].comment is None


def test_setting_a_comment_creates_one(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    assert s["A1"].comment.text == "Une note"


def test_multiline_comment_round_trips(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Ligne 1\nLigne 2\nLigne 3"
    assert s["A1"].comment.text == "Ligne 1\nLigne 2\nLigne 3"


def test_comment_does_not_corrupt_the_cells_own_value(writable_reader):
    # regression: office:annotation nests its own text:p - reading/writing
    # a cell's value must never pick up the comment's paragraph instead
    s = writable_reader.sheet("Sheet1")
    assert s["A1"].value == "texte simple"
    s["A1"].comment = "Une note"
    assert s["A1"].value == "texte simple"
    assert s["A1"].text == "texte simple"


def test_writing_a_value_does_not_remove_an_existing_comment(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    s["A1"].value = "nouvelle valeur"
    assert s["A1"].value == "nouvelle valeur"
    assert s["A1"].comment.text == "Une note"


def test_comment_author_date_and_visibility_read_back_as_written(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    c = s["A1"].comment
    assert c.author is None
    assert c.date is None
    assert c.visible is False

    c.author = "Antonin"
    c.date = dt.datetime(2026, 8, 23, 10, 30)
    c.visible = True
    assert c.author == "Antonin"
    assert c.date == dt.datetime(2026, 8, 23, 10, 30)
    assert c.visible is True


def test_setting_comment_to_none_removes_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    s["A1"].comment = None
    assert s["A1"].comment is None


def test_setting_comment_text_again_replaces_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Premiere note"
    s["A1"].comment = "Deuxieme note"
    assert s["A1"].comment.text == "Deuxieme note"


def test_comment_date_requires_a_datetime(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    with pytest.raises(TypeError):
        s["A1"].comment.date = "2026-08-23"


def test_setting_a_non_string_comment_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(TypeError):
        s["A1"].comment = 42


def test_comment_on_a_repeated_cell_only_affects_that_cell(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["A1"].comment = "Just A1"
    assert s["A1"].comment.text == "Just A1"
    assert s["C1"].comment is None  # shared the same compressed element originally


def test_a_comment_reads_back_after_a_save(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].comment = "Une note"
    c = s["A1"].comment
    c.author = "Antonin"
    c.date = dt.datetime(2026, 8, 23, 10, 30)
    c.visible = True
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["A1"].value == "texte simple"
    comment = reread["A1"].comment
    assert comment.text == "Une note"
    assert comment.author == "Antonin"
    assert comment.date == dt.datetime(2026, 8, 23, 10, 30)
    assert comment.visible is True
