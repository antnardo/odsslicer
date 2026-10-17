"""xmlbs, the bs4 bodies of xmltree's operations - and xmltree itself: every
case runs through both, and both have to leave the tree the bs4 code they
replace left (compared in canonical form, C14N, prefixes declared on the
fragment's root).

These are the parity tests of the move to lxml (`ROADMAP.md`, step 2): when
the switch gives these functions xmltree's bodies, the code calling them
keeps behaving as it did on bs4 as long as this file passes for both.
`benchmarks/mutate_layers.py` breaks each guard of both layers in turn and
checks that a test here fails."""

import copy
import re
import zipfile

import pytest
from bs4 import BeautifulSoup, Tag
from lxml import etree

from conftest import FIXTURES_DIR
from odsslicer import xmlbs, xmltree
from odsslicer.xmltree import NAMESPACES, qn
from odsslicer.xmlutils import _parse_xml

TABLE = NAMESPACES["table"]
DECLARATIONS = " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NAMESPACES.items())

# Mixed content, as a cell's paragraph holds it: the text after an element
# is that element's tail in lxml
TOTAL = "<text:p>Total<text:span>:</text:span> 42 €</text:p>"
LINKED = '<text:p>Voir <text:a xlink:href="https://example.com">ici</text:a> pour le détail</text:p>'
SPANS = "<text:p>a<text:span>b</text:span>c<text:span>d</text:span>e</text:p>"
# a cell holding a value paragraph and a note, which has paragraphs of its own
NOTED = (
    "<table:table-cell><office:annotation><text:p>note</text:p></office:annotation>"
    "<text:p>v</text:p></table:table-cell>"
)


def _bs4_root(xml):
    # whitespace kept, as odsslicer parses a package part
    soup = BeautifulSoup(f"<root {DECLARATIONS}>{xml}</root>", "xml", preserve_whitespace_tags={"root"})
    return soup.find("root")


def _lxml_root(xml):
    return etree.fromstring(f"<root {DECLARATIONS}>{xml}</root>")


class Bs4:
    """How a test reaches into a bs4 tree, whatever the layer does to it."""

    layer = xmlbs
    root = staticmethod(_bs4_root)

    @staticmethod
    def kids(element):
        return element.find_all(True, recursive=False)

    @staticmethod
    def first(element, name):
        return element.find(name)

    @staticmethod
    def parent(element):
        return element.parent

    @staticmethod
    def canonical(root):
        return etree.tostring(etree.fromstring(str(root).encode()), method="c14n")

    @staticmethod
    def plain(element):
        """`element` serialised, without namespace declarations."""
        return re.sub(r' xmlns:\w+="[^"]*"', "", str(element))


class Lxml:
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
        return re.sub(r' xmlns:\w+="[^"]*"', "", etree.tostring(element, encoding="unicode", with_tail=False))


@pytest.fixture(params=[Bs4, Lxml], ids=["bs4", "lxml"])
def side(request):
    return request.param


def expected(xml):
    """The canonical form of the fragment `xml`, to compare a tree with."""
    return Lxml.canonical(_lxml_root(xml))


def bs4_did(xml, edit):
    """What the bs4 code the layer replaces left of `xml`: `edit` applied
    to the first element of the fragment, then the fragment canonical."""
    root = _bs4_root(xml)
    edit(root.find(True, recursive=False))
    return Bs4.canonical(root)


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

    @pytest.mark.parametrize(("xml", "declared"), [("<text:p>x</text:p>", False), ("<text:span/>", True)])
    def test_new_near_a_document_declares_its_namespace_only_where_no_element_has_its_name(
        self, xml, declared
    ):
        # bs4 only: lxml declares by itself on insertion, and ignores `near`
        element = xmlbs.new("text:p", near=_bs4_root(xml))
        assert str(element) == (f'<text:p xmlns:text="{NAMESPACES["text"]}"/>' if declared else "<text:p/>")

    def test_new_near_writes_its_declaration_after_the_attributes(self):
        # as bs4 writes any `xmlns:` attribute, and as it wrote the element
        # `_new_qualified_tag` used to build for want of a template
        element = xmlbs.new("text:p", {"text:style-name": "P1"}, near=_bs4_root("<text:span/>"))
        assert str(element) == f'<text:p text:style-name="P1" xmlns:text="{NAMESPACES["text"]}"/>'

    def test_new_near_is_accepted_and_ignored_by_lxml(self):
        root = _lxml_root("<text:span/>")
        root.append(xmltree.new("text:p", near=root))
        assert etree.tostring(root).endswith(b"<text:span/><text:p/></root>")  # the root binds `text:`

    @staticmethod
    def blank_template(root, name):
        """How odsslicer made a new element up to 0.15.1: a blank copy of one
        the document has, else one built with its own declaration."""
        template = (
            root.find(name)
            or getattr(root, "find_previous", lambda *a: None)(name)
            or getattr(root, "find_next", lambda *a: None)(name)
        )
        if template is None:
            prefix = name.split(":", 1)[0]
            return BeautifulSoup(f'<{name} xmlns:{prefix}="{NAMESPACES[prefix]}"/>', "xml").find(name)
        made = copy.deepcopy(template)
        made.attrs.clear()
        for child in list(made.children):
            child.extract()
        return made

    @pytest.mark.parametrize(
        "path",
        sorted([*FIXTURES_DIR.glob("*.ods"), *(FIXTURES_DIR / "wild").glob("*.ods")]),
        ids=lambda path: path.name,
    )
    def test_new_near_writes_what_a_blank_copy_of_the_document_wrote(self, path):
        names = ["table:table-cell", "table:table-row", "text:p", "text:a", "dc:creator", "office:annotation"]
        names += ["style:style", "style:map", "number:text", "number:boolean", "table:data-pilot-table"]
        with zipfile.ZipFile(path) as package:
            documents = [_parse_xml(package.read(part)) for part in ("content.xml", "styles.xml")]
        for document in documents:
            # the document, elements of it, and one taken out of it: a new
            # style is filled in before it is inserted
            roots = [
                document,
                *document.find_all(["table:table", "table:table-cell", "style:style"], limit=20),
            ]
            roots.append(copy.deepcopy(document.find("style:style") or document.find(True)))
            for root in roots:
                for name in names:
                    made, before = xmlbs.new(name, near=root), self.blank_template(root, name)
                    assert (str(made), made.prettify()) == (str(before), before.prettify()), name

    def test_new_with_an_unknown_prefix_raises_keyerror(self, side):
        with pytest.raises(KeyError):
            side.layer.new("tab:table-cell")


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


class TestText:
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


class TestParagraphText:
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


class TestDiscard:
    @pytest.mark.parametrize(
        ("xml", "index"),
        [
            (TOTAL, 0),
            ("<table:table-row>\n  <table:table-cell/>\n  <table:table-cell/>\n</table:table-row>", 1),
        ],
    )
    def test_discard_leaves_what_bs4_decompose_left(self, side, xml, index):
        root = side.root(xml)
        side.layer.discard(side.kids(side.kids(root)[0])[index])
        assert side.canonical(root) == bs4_did(
            xml, lambda el: el.find_all(True, recursive=False)[index].decompose()
        )

    def test_discard_empties_a_bs4_element_at_once(self):
        # what frees its memory without waiting for the garbage collector
        span = _bs4_root(TOTAL).find("text:span")
        xmlbs.discard(span)
        assert span.decomposed


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
        new = Bs4.layer.new("text:s")
        assert side.canonical(root) == bs4_did(
            SPANS, lambda p: p.find_all(True, recursive=False)[index].insert_after(new)
        )

    @pytest.mark.parametrize("index", [0, 1])
    def test_insert_before_matches_bs4(self, side, index):
        root = side.root(SPANS)
        side.layer.insert_before(side.kids(side.kids(root)[0])[index], side.layer.new("text:s"))
        new = Bs4.layer.new("text:s")
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
        a = Bs4.layer.new("text:a")
        for child in list(p.children):
            a.append(child.extract())
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

    def test_children_is_a_list_in_both_layers(self, side):
        # the code tests it for emptiness: an lxml iterator would always be true
        cell = side.kids(side.root(NOTED))[0]
        assert isinstance(side.layer.children(cell, "text:p"), list)
        assert side.layer.children(cell, "text:s") == []


def test_both_layers_have_the_same_operations():
    shared = {
        "children",
        "clone",
        "discard",
        "insert_after",
        "insert_before",
        "local_name",
        "name_of",
        "namespace_of",
    }
    shared |= {"new", "paragraph_text", "remove", "rename", "set_paragraph_text", "set_text", "text"}
    shared |= {"unwrap", "wrap_contents"}
    assert set(xmlbs.__all__) == shared
    assert shared <= set(xmltree.__all__)


def test_a_bs4_element_is_a_tag():
    assert isinstance(xmlbs.new("text:p"), Tag)
