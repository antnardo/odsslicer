"""xmltree, odsslicer's access layer to lxml - and the pitfalls it exists
for, each shown twice: what lxml does when bs4 code is translated by hand,
then what the layer does instead.

The parity tests run the same edit through bs4 and through the layer, and
compare the results in canonical form (C14N): the layer behaves as the bs4
code it replaced at 1.0, prefixes aside."""

import re
import zipfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from lxml import etree

from conftest import FIXTURES_DIR, libreoffice_shows, ods_with_prefixes, requires_soffice
from odsslicer import ODSReader, xmltree
from odsslicer.xmltree import (
    NAMESPACES,
    attributes,
    child,
    children,
    clone,
    descendants,
    elements,
    following,
    insert_after,
    new,
    parse,
    preceding,
    prefix_of,
    prefixed,
    qn,
    remove,
    serialize,
    set_text,
    text,
    unset,
    unwrap,
)

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

    def test_reader_finds_every_sheet_whatever_the_prefix(self, rebound_dir, test_ods_path):
        # bs4 matched 'table:table' against the prefix as written: such a
        # file read as having no sheet at all before 1.0
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})
        assert ODSReader(rebound).sheets_names == ODSReader(test_ods_path).sheets_names

    def test_reader_reads_every_value_whatever_the_prefix(self, rebound_dir, test_ods_path):
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})
        assert [sheet.to_list() for sheet in ODSReader(rebound).sheets] == [
            sheet.to_list() for sheet in ODSReader(test_ods_path).sheets
        ]
        assert ODSReader(rebound).sheet("Sheet1")["A7"].style.number_format is not None

    def test_writing_into_a_rebound_file_keeps_its_prefix(self, rebound_dir, test_ods_path):
        rebound = ods_with_prefixes(test_ods_path, rebound_dir / "TEST.ods", {TABLE: "tab"})
        reader = ODSReader(rebound)
        sheet = reader.sheet("Sheet1")
        sheet["D12"].value = "grown"  # past the data: new rows and cells
        sheet.insert_rows(1, 2)
        reader.save()
        content = zipfile.ZipFile(rebound).read("content.xml")
        assert b"<table:" not in content and b"xmlns:table=" not in content
        assert ODSReader(rebound).sheet("Sheet1")["D14"].value == "grown"

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
        assert (
            etree.tostring(row)
            == (
                f'<tab:table-row xmlns:tab="{TABLE}"><tab:table-cell tab:style-name="ce1"/></tab:table-row>'
            ).encode()
        )

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


class TestAttributes:
    def test_attributes_take_the_specifications_prefix(self):
        cell = etree.fromstring(f'<tab:table-cell xmlns:tab="{TABLE}" tab:style-name="ce1" tab:formula="x"/>')
        assert attributes(cell) == {"table:style-name": "ce1", "table:formula": "x"}

    def test_attributes_of_an_unnamed_namespace_take_the_documents_prefix(self):
        props = etree.fromstring(
            f'<style:text-properties xmlns:style="{NAMESPACES["style"]}" xmlns:fo="{NAMESPACES["fo"]}"'
            ' xmlns:officeooo="http://openoffice.org/2009/office" officeooo:rsid="0012" fo:color="red"/>'
        )
        assert attributes(props) == {"officeooo:rsid": "0012", "fo:color": "red"}

    def test_attributes_of_an_unbound_namespace_keep_the_uri(self):
        element = etree.Element("x", {"{urn:example}y": "1"})
        element.nsmap  # noqa: B018 - lxml made a prefix up for it, ns0
        assert list(attributes(element)) in (["ns0:y"], ["{urn:example}y"])

    def test_unset_removes_an_attribute_and_ignores_one_not_there(self):
        cell = new("table:table-cell", {"table:style-name": "ce1"})
        unset(cell, qn("table:style-name"))
        unset(cell, qn("table:formula"))
        assert dict(cell.attrib) == {}

    def test_new_declares_the_namespaces_of_its_attributes(self):
        # set afterwards, on a detached element, `xlink:href` would be `ns0:href`
        a = new("text:a", {"xlink:href": "https://example.com"})
        root = etree.fromstring("<root/>")
        root.append(a)
        assert b"ns0" not in etree.tostring(root)
        assert a.nsmap["xlink"] == NAMESPACES["xlink"]

    def test_naive_attribute_set_on_a_detached_element_gets_a_prefix_of_lxmls(self):
        a = new("text:a")
        a.set(qn("xlink:href"), "https://example.com")
        assert b"ns0:href" in etree.tostring(a)


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
        for element in list(p):
            a.append(element)  # its tail goes along, as wanted
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
    def test_child_is_the_first_child_so_named(self):
        cell = lxml_fragment(NOTED)
        assert text(child(cell, "text:p")) == "v"
        assert child(cell, "text:span") is None

    def test_elements_leave_comments_out(self):
        p = lxml_fragment("<text:p>a<!-- c --><text:s/><?pi x?>b<text:tab/></text:p>")
        assert len(p) == 4  # what iterating it walks
        assert [element.tag for element in elements(p)] == [qn("text:s"), qn("text:tab")]

    def test_naive_iter_includes_the_element_itself(self):
        span = lxml_fragment("<text:span><text:span/></text:span>")
        assert len(list(span.iter(qn("text:span")))) == 2

    def test_naive_getroottree_of_a_removed_element_is_still_its_documents(self):
        # an element taken out of its tree stays in the same lxml document:
        # `getroottree()` answers that document's root, not the element.
        # Finding which part a write went to (`ODSReader._touched_tag`)
        # climbs `getparent()` instead, as bs4's `parent` does
        root = lxml_fragment("<text:span><text:span/></text:span>").getparent()
        inner = root[0][0]
        remove(inner)
        assert inner.getparent() is None
        assert inner.getroottree().getroot() is root

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
    """What a cell says, ODF's whitespace elements included (0.14.3); what
    it reads and writes is in `TestParagraphTextParity` below."""

    def test_naive_text_drops_the_encoded_spaces(self):
        assert text(lxml_fragment('<text:p>a<text:s text:c="3"/>b</text:p>')) == "ab"


# ---------------------------------------------------------------------------
# parsing and serialising
# ---------------------------------------------------------------------------

NOT_XML = bytes(range(256)) * 4  # what an encrypted part looks like to a parser
# a part cut short, which a recovering parser completes without a word
TRUNCATED = f'<office:document-content {DECLARATIONS}><office:body><table:table table:name="A">'.encode()


class TestParse:
    def test_bs4_turns_a_part_that_is_not_xml_into_an_empty_document(self):
        # what odsslicer did before 0.15.0 with an encrypted file: no sheet,
        # and an empty content.xml written back by save()
        soup = BeautifulSoup(NOT_XML, "xml")
        assert soup.encode("utf-8") == b'<?xml version="1.0" encoding="utf-8"?>\n'

    @pytest.mark.parametrize("markup", [NOT_XML, TRUNCATED], ids=["encrypted", "truncated"])
    def test_parse_rejects_a_part_that_is_not_well_formed(self, markup):
        with pytest.raises(etree.XMLSyntaxError):
            parse(markup)

    def test_bs4_completes_a_truncated_part_without_a_word(self):
        # what odsslicer did before 1.0: a truncated part read with cells missing
        assert BeautifulSoup(TRUNCATED, "xml").find("table:table")["table:name"] == "A"

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
        assert serialize(parse(markup), markup) == declaration + '<x a="1">été</x>'.encode()


# ---------------------------------------------------------------------------
# Parity with the bs4 code each operation replaced
# ---------------------------------------------------------------------------
#
# Until 1.0 these cases ran through two layers, this one and `xmlbs`, its bs4
# counterpart, the code calling them switching from one to the other when
# odsslicer dropped BeautifulSoup. Each still compares what the operation
# leaves with what the bs4 code it replaced left, in canonical form (C14N),
# prefixes declared on the fragment's root. `benchmarks/mutate_layers.py`
# breaks each guard of the layer in turn and checks that a test here fails.

SPANS = "<text:p>a<text:span>b</text:span>c<text:span>d</text:span>e</text:p>"


def _bs4_root(xml):
    # whitespace kept, as odsslicer kept it when it parsed a part with bs4
    soup = BeautifulSoup(f"<root {DECLARATIONS}>{xml}</root>", "xml", preserve_whitespace_tags={"root"})
    return soup.find("root")


def _lxml_root(xml):
    return etree.fromstring(f"<root {DECLARATIONS}>{xml}</root>")


def _bs4_new(name):
    """A new bs4 element named `name`, as the bs4 code made one."""
    prefix, _, local = name.partition(":")
    return BeautifulSoup("", "xml").new_tag(local, namespace=NAMESPACES[prefix], nsprefix=prefix)


class Lxml:
    """How a test reaches into a tree, whatever the layer does to it."""

    layer = xmltree
    root = staticmethod(_lxml_root)

    @staticmethod
    def kids(element):
        return [child for child in element if isinstance(child.tag, str)]

    @staticmethod
    def first(element, name):
        return next(element.iterdescendants(qn(name)))

    @staticmethod
    def parent(element):
        return element.getparent()

    @staticmethod
    def canonical(root):
        return etree.tostring(root, method="c14n")

    @staticmethod
    def plain(element):
        """`element` serialised, without namespace declarations."""
        return re.sub(r' xmlns:\w+="[^"]*"', "", etree.tostring(element, encoding="unicode", with_tail=False))


@pytest.fixture()
def side():
    return Lxml


def expected(xml):
    """The canonical form of the fragment `xml`, to compare a tree with."""
    return Lxml.canonical(_lxml_root(xml))


def bs4_did(xml, edit):
    """What the bs4 code the layer replaced left of `xml`: `edit` applied
    to the first element of the fragment, then the fragment canonical."""
    root = _bs4_root(xml)
    edit(root.find(True, recursive=False))
    return etree.tostring(etree.fromstring(str(root).encode()), method="c14n")


# ---------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------


class TestNew:
    def test_new_element_appended_matches_the_expected_tree(self, side):
        root = side.root("<table:table-row/>")
        row = side.kids(root)[0]
        row.append(side.layer.new("table:table-cell", {"table:style-name": "ce1"}))
        assert side.canonical(root) == expected(
            '<table:table-row><table:table-cell table:style-name="ce1"/></table:table-row>'
        )

    @pytest.mark.parametrize("name", ["text:s", "text:p", "table:table-cell", "number:text", "dc:creator"])
    def test_new_element_is_empty_and_self_closing(self, side, name):
        assert side.plain(side.layer.new(name)) == f"<{name}/>"

    def test_new_element_is_detached(self, side):
        assert side.parent(side.layer.new("text:p")) is None

    def test_new_with_an_unknown_prefix_raises_keyerror(self, side):
        with pytest.raises(KeyError):
            side.layer.new("tab:table-cell")


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


class TestTextParity:
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
    def test_text_matches_bs4_get_text(self, side, xml):
        element = side.kids(side.root(xml))[0]
        assert side.layer.text(element) == _bs4_root(xml).find(True).get_text()

    @pytest.mark.parametrize("value", ["neuf", ""])
    def test_set_text_replaces_every_child(self, side, value):
        root = side.root(LINKED)
        p = side.kids(root)[0]
        side.layer.set_text(p, value)
        assert side.plain(p) == f"<text:p>{value}</text:p>"  # empty, not self-closing: as bs4 writes it
        assert side.canonical(root) == bs4_did(LINKED, lambda p: setattr(p, "string", value))


class TestParagraphTextParity:
    @pytest.mark.parametrize(
        ("xml", "shown"),
        [
            ("<text:p>plain</text:p>", "plain"),
            ("<text:p/>", ""),
            ('<text:p>a<text:s text:c="3"/>b</text:p>', "a   b"),
            ("<text:p><text:s/>lead<text:tab/>tab<text:line-break/>next</text:p>", " lead\ttab\nnext"),
            (
                '<text:p>a<text:span>b<text:s text:c="2"/>c</text:span>d<text:s text:c="x"/>e</text:p>',
                "ab  cd e",
            ),
            ("<text:p>a<!-- not said -->b</text:p>", "ab"),
            (LINKED, "Voir ici pour le détail"),
        ],
    )
    def test_paragraph_text_reads_the_encoded_whitespace(self, side, xml, shown):
        assert side.layer.paragraph_text(side.kids(side.root(xml))[0]) == shown

    @pytest.mark.parametrize(
        ("value", "inner"),
        [
            ("plain", "plain"),
            ("", ""),
            ("a  b", "a <text:s/>b"),
            ("   lead", '<text:s text:c="3"/>lead'),
            ("a\tb", "a<text:tab/>b"),
            (" ", "<text:s/>"),
            ("a   b    c\t\td ", 'a <text:s text:c="2"/>b <text:s text:c="3"/>c<text:tab/><text:tab/>d '),
            ("x \t y", "x <text:tab/> y"),
        ],
    )
    def test_set_paragraph_text_encodes_the_whitespace_as_libreoffice(self, side, value, inner):
        root = side.root(LINKED)
        p = side.kids(root)[0]
        side.layer.set_paragraph_text(p, value)
        assert side.canonical(root) == expected(f"<text:p>{inner}</text:p>")
        assert side.layer.paragraph_text(p) == value


# ---------------------------------------------------------------------------
# moving elements around: the text after an element is its tail in lxml
# ---------------------------------------------------------------------------


class TestRemove:
    @pytest.mark.parametrize(
        ("xml", "index"),
        [
            (TOTAL, 0),
            ("<text:p><text:span>a</text:span>b<text:s/>c</text:p>", 0),
            ("<text:p>a<text:s/>b<text:span>c</text:span>d</text:p>", 1),
            ("<table:table-row>\n  <table:table-cell/>\n  <table:table-cell/>\n</table:table-row>", 1),
        ],
    )
    def test_remove_leaves_what_bs4_decompose_left(self, side, xml, index):
        root = side.root(xml)
        removed = side.layer.remove(side.kids(side.kids(root)[0])[index])
        assert side.canonical(root) == bs4_did(
            xml, lambda el: el.find_all(True, recursive=False)[index].decompose()
        )
        assert side.parent(removed) is None

    def test_remove_gives_back_the_element_whole_without_the_text_after_it(self, side):
        root = side.root(TOTAL)
        span = side.layer.remove(side.kids(side.kids(root)[0])[0])
        assert side.plain(span) == "<text:span>:</text:span>"
        assert side.layer.text(side.kids(root)[0]) == "Total 42 €"


class TestClone:
    def test_clone_leaves_the_text_after_the_original_out(self, side):
        root = side.root(TOTAL)
        p = side.kids(root)[0]
        copied = side.layer.clone(side.kids(p)[0])
        assert side.parent(copied) is None
        p.append(copied)
        assert side.layer.text(p) == "Total: 42 €:"

    def test_clone_inserted_next_to_its_original_says_the_text_once(self, side):
        root = side.root(TOTAL)
        span = side.kids(side.kids(root)[0])[0]
        side.layer.insert_after(span, side.layer.clone(span))
        assert side.canonical(root) == expected(
            "<text:p>Total<text:span>:</text:span><text:span>:</text:span> 42 €</text:p>"
        )

    def test_clone_is_deep(self, side):
        root = side.root(LINKED)
        copied = side.layer.clone(side.kids(root)[0])
        assert side.layer.text(copied) == "Voir ici pour le détail"


class TestInsert:
    @pytest.mark.parametrize("index", [0, 1])
    def test_insert_after_matches_bs4(self, side, index):
        root = side.root(SPANS)
        side.layer.insert_after(side.kids(side.kids(root)[0])[index], side.layer.new("text:s"))
        new = _bs4_new("text:s")
        assert side.canonical(root) == bs4_did(
            SPANS, lambda p: p.find_all(True, recursive=False)[index].insert_after(new)
        )

    @pytest.mark.parametrize("index", [0, 1])
    def test_insert_before_matches_bs4(self, side, index):
        root = side.root(SPANS)
        side.layer.insert_before(side.kids(side.kids(root)[0])[index], side.layer.new("text:s"))
        new = _bs4_new("text:s")
        assert side.canonical(root) == bs4_did(
            SPANS, lambda p: p.find_all(True, recursive=False)[index].insert_before(new)
        )

    def test_insert_after_a_removed_element_puts_it_back_where_it_was(self, side):
        root = side.root(SPANS)
        p = side.kids(root)[0]
        first, second = side.kids(p)
        side.layer.insert_after(first, side.layer.remove(second))
        assert side.canonical(root) == expected(
            "<text:p>a<text:span>b</text:span><text:span>d</text:span>ce</text:p>"
        )


class TestUnwrap:
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
    def test_unwrap_matches_bs4(self, side, xml):
        root = side.root(xml)
        side.layer.unwrap(side.first(root, "text:a"))
        assert side.canonical(root) == bs4_did(xml, lambda p: p.find("text:a").unwrap())

    def test_unwrap_keeps_the_text_around_the_link(self, side):
        root = side.root(LINKED)
        p = side.kids(root)[0]
        side.layer.unwrap(side.first(root, "text:a"))
        assert side.layer.text(p) == "Voir ici pour le détail"
        assert side.kids(p) == []


class TestWrapContents:
    @staticmethod
    def bs4_hyperlink(p):
        # what `cell.hyperlink = url` did, up to 0.15.1
        a = _bs4_new("text:a")
        for node in list(p.children):
            a.append(node.extract())
        p.append(a)

    @pytest.mark.parametrize(
        "xml",
        [
            "<text:p>Voir <text:span>ici</text:span> et là</text:p>",
            "<text:p>plain</text:p>",
            "<text:p/>",
            '<text:p><text:s/>a<text:s text:c="2"/>b</text:p>',
            "<text:p><text:span>a</text:span>b</text:p>",
        ],
    )
    def test_wrap_contents_matches_bs4(self, side, xml):
        root = side.root(xml)
        p = side.kids(root)[0]
        said = side.layer.paragraph_text(p)
        a = side.layer.new("text:a")
        side.layer.wrap_contents(p, a)
        assert side.canonical(root) == bs4_did(xml, self.bs4_hyperlink)
        assert side.layer.paragraph_text(a) == said
        assert side.kids(p) == [a]


# ---------------------------------------------------------------------------
# names and search
# ---------------------------------------------------------------------------


class TestNames:
    def test_names_of_an_element(self, side):
        row = side.kids(side.root("<table:table-row/>"))[0]
        assert side.layer.local_name(row) == "table-row"
        assert side.layer.name_of(row) == "table:table-row"
        assert side.layer.namespace_of(row) == TABLE

    def test_name_of_a_new_element_is_the_name_it_was_made_with(self, side):
        assert side.layer.name_of(side.layer.new("number:text")) == "number:text"

    def test_rename_keeps_the_attributes_and_the_content(self, side):
        xml = '<table:covered-table-cell office:value="1"><text:p>1</text:p></table:covered-table-cell>'
        root = side.root(xml)
        cell = side.kids(root)[0]
        side.layer.rename(cell, "table:table-cell")
        assert side.layer.name_of(cell) == "table:table-cell"
        assert side.canonical(root) == expected(
            '<table:table-cell office:value="1"><text:p>1</text:p></table:table-cell>'
        )


class TestChildren:
    @pytest.mark.parametrize(
        "xml",
        [
            "<text:p>a</text:p><text:p>b</text:p>",
            "<office:annotation><text:p>note</text:p></office:annotation><text:p>value</text:p>",
            "",
            "<text:span>x</text:span>",
            "<!-- c --><text:p>a</text:p>text<text:p>b</text:p>",
            "<loext:p>other namespace</loext:p><text:p>a</text:p>",
        ],
    )
    @pytest.mark.parametrize("name", ["text:p", "office:annotation"])
    def test_children_are_what_find_all_finds_among_children(self, side, xml, name):
        cell = side.kids(side.root(f"<table:table-cell>{xml}</table:table-cell>"))[0]
        reference = _bs4_root(f"<table:table-cell>{xml}</table:table-cell>").find(True)
        found = list(side.layer.children(cell, name))
        assert [side.plain(el) for el in found] == [
            str(el) for el in reference.find_all(name, recursive=False)
        ]

    def test_children_leave_a_notes_paragraphs_out(self, side):
        cell = side.kids(side.root(NOTED))[0]
        assert [side.layer.text(p) for p in side.layer.children(cell, "text:p")] == ["v"]

    def test_children_is_a_list(self, side):
        # the code tests it for emptiness: an lxml iterator would always be true
        cell = side.kids(side.root(NOTED))[0]
        assert isinstance(side.layer.children(cell, "text:p"), list)
        assert side.layer.children(cell, "text:s") == []
