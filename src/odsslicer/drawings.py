"""The drawings of a document - charts, shapes, images anchored to cells - and
what they hold of the cells, rewritten when the cells move or their sheet is
renamed.

LibreOffice has a chart's ranges follow the cells (issue #15): in the chart's
own `content.xml`, on its `<draw:object>` for LibreOffice to know when to
redraw it, and in the cell a drawing anchored to a cell ends in,
`table:end-cell-address`, from which LibreOffice sizes it. `ODSReader` found
them and rewrote them, alongside everything else it did, until 1.0's audit;
`_Drawings` does only that, over the package, and keeps the one cache it
needs: the elements of `content.xml` recording an address, looked for once.
Remapping and renaming were two copies of one loop; they are one now,
`_rewrite`, told what to do with an address.

Nothing here is public (`__all__` is empty): `ODSReader` holds `_Drawings`,
and `references` has it follow an edit.
"""

from collections.abc import Callable
from typing import cast

from lxml import etree

from odsslicer.formulas import _Remap, _remap_range_list, _rename_range_list
from odsslicer.package import _Package, _root_of
from odsslicer.xmltree import qn

__all__: list[str] = []

# A chart's attributes holding cell ranges, in its own content.xml, by
# namespace URI (`xmltree.qn`)
_CHART_RANGE_ATTRS = tuple(
    qn(name)
    for name in (
        "table:cell-range-address",
        "chart:values-cell-range-address",
        "chart:label-cell-address",
        "chart:error-lower-range",
        "chart:error-upper-range",
    )
)
_OFFICE_CHART = qn("office:chart")
_END_CELL_ADDRESS = qn("table:end-cell-address")
_NOTIFY_ON_UPDATE = qn("draw:notify-on-update-of-ranges")


def _carrying(chart: "etree._ElementTree", attr: str) -> "list[etree._Element]":
    """The elements of `chart` with attribute `attr` (by URI), in document
    order, the root included - bs4's `find_all(attrs={attr: True})`, which
    searched the whole document. Wherever a range sits in a chart, it
    follows the cells."""
    return [element for element in chart.getroot().iter(etree.Element) if attr in element.attrib]


class _Drawings:
    """The drawings of the package's `content.xml`, and the charts they
    embed: finding them, and rewriting the addresses they hold."""

    def __init__(self, package: _Package) -> None:
        self._package = package
        # see `_elements`
        self._tags: tuple[list[etree._Element], list[etree._Element]] | None = None
        # by part name: whether an embedded object is a chart
        self._is_chart: dict[str, bool] = {}

    def _elements(self) -> "tuple[list[etree._Element], list[etree._Element]]":
        """The elements of `content.xml` recording the cell they end in,
        `table:end-cell-address`, and the embedded objects, `<draw:object>`,
        looked for once: a structural edit would otherwise scan the whole
        document for them, which took a dozen times longer than the edit on
        a 10,000-row sheet. odsslicer never creates any, and one deleted
        with its row is only left detached. A document holding neither, as
        its raw text shows, skips the scan - whatever the prefix, which a
        document may bind otherwise: the search itself goes by namespace."""
        if self._tags is None:
            content = self._package.content
            if b":end-cell-address" in content or b":object" in content:
                root = self._package.content_root()
                self._tags = (
                    [element for element in root.iter(etree.Element) if _END_CELL_ADDRESS in element.attrib],
                    list(root.iter(qn("draw:object"))),
                )
            else:
                self._tags = ([], [])
        # an element deleted with its row or column since is out of the
        # document: there is nothing left to remap, and its chart is no
        # longer drawn there - a drawing moved elsewhere is moved, not
        # deleted. lxml keeps a detached element readable, where bs4 emptied
        # it: it has to be told apart, by the root it climbs to
        ended, objects = self._tags
        root = self._package.content_root()
        if not all(_root_of(tag) is root for tag in (*ended, *objects)):
            self._tags = (
                [tag for tag in ended if _root_of(tag) is root],
                [tag for tag in objects if _root_of(tag) is root],
            )
        return self._tags

    def charts(self) -> "list[tuple[str, etree._ElementTree]]":
        """The content of every chart embedded in the document, each the
        `content.xml` of an object of the package (`Object 1/`...) that a
        `<draw:object>` points at, with the name it has in the package -
        parsed once, and kept for `save()`: parsing one is not editing it,
        so a caller about to rewrite its ranges has to `touch` it for
        `save()` to write the tree back. Another embedded object - a
        formula, say - is no chart, and stays as it is.

        Its elements are found by namespace, whatever prefixes it binds.
        An object's part that is not well-formed XML raises
        `MalformedDocumentError` - before the edit, see
        `_Package.parse_objects`."""
        wanted = []
        for tag in self._elements()[1]:
            href = tag.get(qn("xlink:href"), "").removeprefix("./").rstrip("/")
            if href:
                wanted.append(f"{href}/content.xml")
        parts = self._package.object_parts(wanted)
        charts = []
        for name in wanted:
            if name not in parts:
                continue
            if name not in self._is_chart:
                # `office:chart` anywhere, the root included, where bs4's `find` looked for it
                self._is_chart[name] = next(parts[name].getroot().iter(_OFFICE_CHART), None) is not None
            if self._is_chart[name]:
                charts.append((name, parts[name]))
        return charts

    def remap(self, target_sheet: str, remap: _Remap) -> None:
        """After a structural edit of `target_sheet`, have what drawings hold
        of its cells follow them, as LibreOffice has them follow (issue #15):
        a chart's ranges - in its own content, and listed again on its
        `<draw:object>` - and the cell a drawing anchored to a cell ends in.
        They used to stay as they were: a row inserted in a chart's data
        left the last day out of it, and the chart shrank by a row.

        Deleted, the end keeps its address - the cell taking its place - as
        LibreOffice keeps it: that of a range's first cell."""
        self._rewrite(
            end=lambda ends: _remap_range_list(ends, target_sheet, remap, "start"),
            ranges=lambda ranges: _remap_range_list(ranges, target_sheet, remap),
        )

    def rename(self, old_name: str, new_name: str) -> None:
        """`remap`'s counterpart for a sheet renamed."""

        def rename(ranges: str) -> str:
            return _rename_range_list(ranges, old_name, new_name)

        self._rewrite(end=rename, ranges=rename)

    def _rewrite(self, *, end: "Callable[[str], str]", ranges: "Callable[[str], str]") -> None:
        """Rewrite every address the drawings hold: the cell a drawing ends
        in through `end`, a list of ranges through `ranges`."""
        ended, objects = self._elements()
        for tag in ended:
            tag.set(_END_CELL_ADDRESS, end(cast(str, tag.attrib[_END_CELL_ADDRESS])))
        for tag in objects:
            if _NOTIFY_ON_UPDATE in tag.attrib:
                tag.set(_NOTIFY_ON_UPDATE, ranges(cast(str, tag.attrib[_NOTIFY_ON_UPDATE])))
        for name, chart in self.charts():
            self._package.touch(name)
            for attr in _CHART_RANGE_ATTRS:
                for element in _carrying(chart, attr):
                    element.set(attr, ranges(cast(str, element.get(attr))))
