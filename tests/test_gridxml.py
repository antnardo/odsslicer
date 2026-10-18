"""The XML of a sheet read without a `Cell`."""

import pytest

from conftest import CELL_ELEMENTS, _fragment
from odsslicer import Cell, xmltree


@pytest.mark.parametrize("xml", CELL_ELEMENTS)
def test_holds_content_reads_the_element_as_a_cell_reads_it(xml):
    from odsslicer.gridxml import _holds_content

    (element,) = _fragment(xml)
    cell = Cell(element)
    assert _holds_content(element) == (
        not cell.is_empty or bool(xmltree.children(element, "office:annotation"))
    )
