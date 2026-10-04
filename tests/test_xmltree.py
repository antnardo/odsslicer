"""xmltree, the lxml access layer of the feasibility study - and the
pitfalls it exists for, each shown twice: what lxml does when bs4 code is
translated by hand, then what the layer does instead.

The parity tests run the same edit through bs4 and through the layer, and
compare the results in canonical form (C14N): the layer is meant to behave
as the bs4 code it replaces, prefixes aside."""

import zipfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from lxml import etree

from conftest import FIXTURES_DIR, libreoffice_shows, ods_with_prefixes, requires_soffice
from odsslicer import ODSReader
from odsslicer.xmltree import (
    NAMESPACES,
    children,
    clone,
    descendants,
    following,
    insert_after,
    new,
    paragraph_text,
    parse,
    preceding,
    prefix_of,
    prefixed,
    qn,
    remove,
    serialize,
    set_paragraph_text,
    set_text,
    text,
    unwrap,
)
from odsslicer.xmlutils import _paragraph_text, _parse_xml, _set_paragraph_text

TABLE = NAMESPACES["table"]
DECLARATIONS = " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NAMESPACES.items())

# Mixed content, as a cell's paragraph holds it: the text after an element
# is that element's tail in lxml
TOTAL = "<text:p>Total<text:span>:</text:span> 42 €</text:p>"
LINKED = '<text:p>Voir <text:a xlink:href="https://example.com">ici</text:a> pour le détail</text:p>'


def lxml_fragment(xml):
    """The first element of an ODF XML fragment, as lxml has it."""
    return etree.fromstring(f"<root {DECLARATIONS}>{xml}</root>")[0]


def bs4_fragment(xml):
    """The same, as bs4 has it - whitespace kept, as odsslicer parses."""
    soup = BeautifulSoup(f"<root {DECLARATIONS}>{xml}</root>", "xml", preserve_whitespace_tags={"root"})
    return soup.find("root").find(True)


def canonical(element):
    """`element` in canonical form, each namespace declared where it is
    used: comparable across both libraries."""
    return etree.tostring(element, method="c14n", exclusive=True)


def same(bs4_element, lxml_element):
    """Whether an edit left the same tree in both libraries. A bs4 element
    prints without the declarations its fragment's root holds, so the root
    is printed, and its element read back."""
    printed = etree.fromstring(str(bs4_element.parent).encode())[0]
    return canonical(printed) == canonical(lxml_element)


@pytest.fixture()
def rebound_ods(tmp_path, test_ods_path):
    """TEST.ods with its table namespace bound to `tab:`, as XML allows."""
    return ods_with_prefixes(test_ods_path, tmp_path / "rebound" / "TEST.ods", {TABLE: "tab"})


@pytest.fixture()
def rebound_dir(tmp_path):
    (tmp_path / "rebound").mkdir()
    return tmp_path / "rebound"


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------


def test_qn_spells_the_namespace_uri():
    assert qn("table:table-cell") == f"{{{TABLE}}}table-cell"


def test_qn_with_an_unknown_prefix_raises_keyerror():
    with pytest.raises(KeyError):
        qn("tab:table-cell")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (f"{{{TABLE}}}style-name", "table:style-name"),
        ("{urn:example}x", "{urn:example}x"),
        ("plain", "plain"),
    ],
)
def test_prefixed_gives_the_specification_prefix_back(name, expected):
    assert prefixed(name) == expected


# ---------------------------------------------------------------------------
# pitfall: prefixes are the document's to choose
# ---------------------------------------------------------------------------


class TestPrefixes:
    def test_rebinding_changes_the_prefix_and_nothing_else(self, rebound_dir, test_ods_path):
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})

        def shape(path):
            root = etree.fromstring(zipfile.ZipFile(path).read("content.xml"))
            return [(el.tag, sorted(el.attrib.items()), el.text, el.tail) for el in root.iter()]

        content = zipfile.ZipFile(rebound).read("content.xml")
        assert b"<tab:table " in content and b"<table:" not in content
        assert shape(rebound) == shape(test_ods_path)

    @pytest.mark.xfail(
        strict=True,
        reason="bs4 matches 'table:table' against the prefix as written: such a file reads as having "
        "no sheet until content.xml moves to lxml - remove this mark then",
    )
    def test_reader_finds_every_sheet_whatever_the_prefix(self, rebound_dir, test_ods_path):
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})
        assert ODSReader(rebound).sheets_names == ODSReader(test_ods_path).sheets_names

    def test_descendants_find_every_sheet_whatever_the_prefix(self, rebound_dir, test_ods_path):
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})
        root = parse(zipfile.ZipFile(rebound).read("content.xml")).getroot()
        names = [table.get(qn("table:name")) for table in descendants(root, "table:table")]
        assert names == ["Sheet1", "Sheet2Repeat", "SheetEmpty", "SheetFusion"]

    @requires_soffice
    def test_libreoffice_reads_a_rebound_file_as_the_original(self, tmp_path, rebound_dir, test_ods_path):
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})
        original = tmp_path / "TEST.ods"
        original.write_bytes(test_ods_path.read_bytes())
        shown = libreoffice_shows(original, tmp_path)
        assert shown and libreoffice_shows(rebound, rebound_dir) == shown

    def test_new_element_takes_the_documents_prefix(self):
        row = etree.fromstring(f'<tab:table-row xmlns:tab="{TABLE}"/>')
        row.append(new("table:table-cell", {"table:style-name": "ce1"}))
        assert etree.tostring(row) == (
            f'<tab:table-row xmlns:tab="{TABLE}"><tab:table-cell tab:style-name="ce1"/></tab:table-row>'
        ).encode()

    def test_new_element_declares_the_specification_prefix_where_none_is_bound(self):
        root = etree.fromstring("<root/>")
        root.append(new("table:table-cell"))
        assert etree.tostring(root) == f'<root><table:table-cell xmlns:table="{TABLE}"/></root>'.encode()

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ('xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"', "of"),
            (
                f'xmlns:table="{TABLE}" xmlns:openformula="urn:oasis:names:tc:opendocument:xmlns:of:1.2"',
                "openformula",
            ),
            (f'xmlns:table="{TABLE}"', None),
        ],
    )
    def test_prefix_of_reads_the_documents_own_binding(self, declared, expected):
        # a formula's language prefix, `of:=`, is resolved this way and no other
        cell = etree.fromstring(f"<root {declared}><cell/></root>")[0]
        assert prefix_of(cell, NAMESPACES["of"]) == expected


# ---------------------------------------------------------------------------
# pitfall: the text after an element is its tail
# ---------------------------------------------------------------------------


class TestTail:
    def test_naive_remove_takes_the_following_text_along(self):
        # what `tag.decompose()` becomes, translated word for word
        p = lxml_fragment(TOTAL)
        p.remove(p[0])
        assert text(p) == "Total"

    def test_remove_leaves_the_following_text(self):
        p = lxml_fragment(TOTAL)
        span = remove(p[0])
        assert text(p) == "Total 42 €"
        assert span.getparent() is None and span.tail is None

    @pytest.mark.parametrize(
        ("xml", "index"),
        [
            (TOTAL, 0),
            ("<text:p><text:span>a</text:span>b<text:s/>c</text:p>", 0),
            ("<text:p>a<text:s/>b<text:span>c</text:span>d</text:p>", 1),
            ("<table:table-row>\n  <table:table-cell/>\n  <table:table-cell/>\n</table:table-row>", 1),
        ],
    )
    def test_remove_matches_bs4_decompose(self, xml, index):
        soup_root, lxml_root = bs4_fragment(xml), lxml_fragment(xml)
        soup_root.find_all(True, recursive=False)[index].decompose()
        remove(lxml_root[index])
        assert same(soup_root, lxml_root)

    def test_naive_copy_inserted_next_to_its_original_says_the_text_twice(self):
        import copy

        p = lxml_fragment(TOTAL)
        p[0].addnext(copy.deepcopy(p[0]))
        assert text(p) == "Total: 42 €: 42 €"

    def test_clone_leaves_the_text_out(self):
        p = lxml_fragment(TOTAL)
        copied = clone(p[0])
        assert copied.tail is None
        p.append(copied)
        assert text(p) == "Total: 42 €:"

    def test_naive_addnext_lands_past_the_following_text(self):
        p = lxml_fragment(TOTAL)
        p[0].addnext(new("text:s"))
        assert etree.tostring(p[-1]).startswith(b"<text:s")  # after " 42 €"

    @pytest.mark.parametrize("index", [0, 1])
    def test_insert_after_matches_bs4(self, index):
        xml = "<text:p>a<text:span>b</text:span>c<text:span>d</text:span>e</text:p>"
        soup_root, lxml_root = bs4_fragment(xml), lxml_fragment(xml)
        soup_root.find_all(True, recursive=False)[index].insert_after(bs4_fragment("<text:s/>"))
        insert_after(lxml_root[index], new("text:s"))
        assert same(soup_root, lxml_root)

    def test_naive_unwrap_loses_the_text_after_the_link(self):
        # bs4's `a.unwrap()` has no lxml counterpart: the obvious stand-in
        # takes the link's text into the paragraph, then drops the link
        p = lxml_fragment(LINKED)
        a = p[0]
        p.text += a.text
        p.remove(a)
        assert text(p) == "Voir ici"

    def test_unwrap_keeps_the_text_around_the_link(self):
        p = lxml_fragment(LINKED)
        unwrap(p[0])
        assert text(p) == "Voir ici pour le détail"
        assert len(p) == 0

    def test_naive_move_of_a_paragraphs_content_loses_the_text_before_its_first_child(self):
        # what `cell.hyperlink = url` does since 0.14.3, `a.append(child.extract())`
        # for every child: the text before the first child is no child in lxml
        p = lxml_fragment("<text:p>Voir <text:span>ici</text:span> et là</text:p>")
        a = new("text:a")
        for child in list(p):
            a.append(child)  # its tail goes along, as wanted
        p.append(a)
        assert text(p) == "Voir ici et là"  # still there, but outside the link
        assert text(a) == "ici et là"

    @pytest.mark.parametrize(
        "xml",
        [
            LINKED,
            '<text:p><text:a xlink:href="u">tout</text:a></text:p>',
            '<text:p>a<text:a xlink:href="u">b<text:span>c</text:span>d</text:a>e<text:s/>f</text:p>',
            '<text:p><text:s/>a<text:a xlink:href="u"><text:span>b</text:span></text:a>c</text:p>',
            '<text:p>a<text:a xlink:href="u"/>b</text:p>',
        ],
    )
    def test_unwrap_matches_bs4(self, xml):
        soup_root, lxml_root = bs4_fragment(xml), lxml_fragment(xml)
        soup_root.find("text:a").unwrap()
        unwrap(lxml_root.find(qn("text:a")))
        assert same(soup_root, lxml_root)


# ---------------------------------------------------------------------------
# search and text
# ---------------------------------------------------------------------------

# a cell holding a value paragraph and a note, which has paragraphs of its own
NOTED = (
    "<table:table-cell><office:annotation><text:p>note</text:p></office:annotation>"
    "<text:p>v</text:p></table:table-cell>"
)
# numbered elements, to follow the order a search returns them in
TREE = (
    '<text:section n="0"><text:p n="1"><text:span n="2"/>'
    '<text:span n="3"><text:s n="4"/></text:span></text:p>'
    '<text:p n="5"><text:s n="6"/></text:p><text:list n="7"><text:p n="8"/></text:list>'
    '<text:p n="9"/></text:section>'
)


class TestSearch:
    def test_naive_iter_includes_the_element_itself(self):
        span = lxml_fragment("<text:span><text:span/></text:span>")
        assert len(list(span.iter(qn("text:span")))) == 2

    def test_descendants_leave_the_element_out(self):
        span = lxml_fragment("<text:span><text:span/></text:span>")
        assert len(list(descendants(span, "text:span"))) == 1

    def test_children_leave_a_notes_paragraphs_out(self):
        cell = lxml_fragment(NOTED)
        assert [text(p) for p in children(cell, "text:p")] == ["v"]
        assert [text(p) for p in descendants(cell, "text:p")] == ["note", "v"]

    @pytest.mark.parametrize("start", ["4", "5", "8", "9"])
    def test_preceding_matches_bs4_previous_elements(self, start):
        names = ("text:p", "text:span", "text:s")
        soup_start = bs4_fragment(TREE).find(attrs={"n": start})
        expected = [
            el["n"] for el in soup_start.previous_elements if getattr(el, "name", None) in ("p", "span", "s")
        ]
        lxml_start = lxml_fragment(TREE).find(f".//*[@n='{start}']")
        assert [el.get("n") for el in preceding(lxml_start, *names)] == expected

    @pytest.mark.parametrize("start", ["1", "3", "5", "8"])
    def test_following_matches_bs4_next_elements(self, start):
        names = ("text:p", "text:span", "text:s")
        soup_start = bs4_fragment(TREE).find(attrs={"n": start})
        expected = [
            el["n"] for el in soup_start.next_elements if getattr(el, "name", None) in ("p", "span", "s")
        ]
        lxml_start = lxml_fragment(TREE).find(f".//*[@n='{start}']")
        assert [el.get("n") for el in following(lxml_start, *names)] == expected


class TestText:
    def test_naive_text_attribute_stops_at_the_first_child(self):
        assert lxml_fragment(TOTAL).text == "Total"

    @pytest.mark.parametrize(
        "xml",
        [
            TOTAL,
            LINKED,
            NOTED,
            "<text:p>a<!-- not said -->b<text:s/>c</text:p>",
            "<text:p/>",
            "<text:p>   </text:p>",
        ],
    )
    def test_text_matches_bs4_get_text(self, xml):
        assert text(lxml_fragment(xml)) == bs4_fragment(xml).get_text()

    def test_set_text_replaces_every_child(self):
        p = lxml_fragment(LINKED)
        set_text(p, "neuf")
        assert etree.tostring(p, method="c14n", exclusive=True) == b"<text:p>neuf</text:p>".replace(
            b"<text:p>", f'<text:p xmlns:text="{NAMESPACES["text"]}">'.encode()
        )


class TestParagraphText:
    """What a cell says, ODF's whitespace elements included (0.14.3): the
    lxml versions behave as the bs4 ones in `xmlutils`."""

    @pytest.mark.parametrize(
        "xml",
        [
            "<text:p>plain</text:p>",
            "<text:p/>",
            '<text:p>a<text:s text:c="3"/>b</text:p>',
            "<text:p><text:s/>lead<text:tab/>tab<text:line-break/>next</text:p>",
            '<text:p>a<text:span>b<text:s text:c="2"/>c</text:span>d<text:s text:c="x"/>e</text:p>',
            "<text:p>a<!-- not said -->b</text:p>",
            LINKED,
        ],
    )
    def test_paragraph_text_matches_bs4(self, xml):
        assert paragraph_text(lxml_fragment(xml)) == _paragraph_text(bs4_fragment(xml))

    def test_naive_text_drops_the_encoded_spaces(self):
        assert text(lxml_fragment('<text:p>a<text:s text:c="3"/>b</text:p>')) == "ab"

    @pytest.mark.parametrize(
        "value", ["plain", "", "a  b", "   lead", "a\tb", " ", "a   b    c\t\td ", "x \t y"]
    )
    def test_set_paragraph_text_matches_bs4(self, value):
        soup_p, lxml_p = bs4_fragment(LINKED), lxml_fragment(LINKED)
        _set_paragraph_text(soup_p, value)
        set_paragraph_text(lxml_p, value)
        assert same(soup_p, lxml_p)
        assert paragraph_text(lxml_p) == value


# ---------------------------------------------------------------------------
# parsing and serialising
# ---------------------------------------------------------------------------

NOT_XML = bytes(range(256)) * 4  # what an encrypted part looks like to a parser
# a part cut short, which a recovering parser completes without a word
TRUNCATED = f'<office:document-content {DECLARATIONS}><office:body><table:table table:name="A">'.encode()


class TestParse:
    def test_bs4_turns_a_part_that_is_not_xml_into_an_empty_document(self):
        # what odsslicer does today with an encrypted file: no sheet, and an
        # empty content.xml written back by save()
        assert _parse_xml(NOT_XML).encode("utf-8") == b'<?xml version="1.0" encoding="utf-8"?>\n'

    @pytest.mark.parametrize("markup", [NOT_XML, TRUNCATED], ids=["encrypted", "truncated"])
    def test_parse_rejects_a_part_that_is_not_well_formed(self, markup):
        with pytest.raises(etree.XMLSyntaxError):
            parse(markup)

    def test_bs4_completes_a_truncated_part_without_a_word(self):
        assert _parse_xml(TRUNCATED).find("table:table")["table:name"] == "A"

    def test_parse_never_reads_an_external_entity(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        markup = f'<!DOCTYPE x [<!ENTITY e SYSTEM "{secret.as_uri()}">]><x>&e;</x>'.encode()
        assert "secret" not in text(parse(markup).getroot())

    @pytest.mark.parametrize(
        "path",
        sorted([*FIXTURES_DIR.glob("*.ods"), *(FIXTURES_DIR / "wild").glob("*.ods")]),
        ids=lambda path: path.name,
    )
    def test_serialize_gives_back_what_every_part_of_a_fixture_says(self, path: Path):
        with zipfile.ZipFile(path) as package:
            parts = {name: package.read(name) for name in package.namelist() if name.endswith(".xml")}
        for name, markup in parts.items():
            if not markup.strip():
                continue
            out = serialize(parse(markup), markup)
            assert out.split(b">", 1)[0] == markup.split(b">", 1)[0], name  # the declaration as written
            assert etree.tostring(etree.fromstring(out), method="c14n") == etree.tostring(
                etree.fromstring(markup), method="c14n"
            ), name

    @pytest.mark.parametrize(
        "declaration",
        [
            b'<?xml version="1.0" encoding="UTF-8"?>\n',
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n',
            b"",
        ],
    )
    def test_serialize_writes_the_declaration_the_part_had(self, declaration):
        markup = declaration + b"<x a='1'>&#233;t&#233;</x>"
        assert serialize(parse(markup), markup) == declaration + "<x a=\"1\">été</x>".encode()
