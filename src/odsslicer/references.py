"""What points at cells follows them: after rows or columns of a sheet are
inserted or deleted, and after a sheet is renamed, every formula of the
document and every drawing still points at the cells it did.

A structural edit of one sheet reaches the whole document - another sheet's
formula names it, a chart plots it - so the sweep has to start from the
document, not from the sheet: `Sheet` did the formulas of a structural edit
and `ODSReader` those of a renaming, each with its own loop, and both handed
the drawings to the reader. `_References` holds the two sweeps, and the check
that comes before an edit: a chart that cannot be read must refuse the edit
before anything moves.

Nothing here is public (`__all__` is empty): the structural edits and
`ODSReader.rename_sheet` make a `_References`.
"""

from typing import TYPE_CHECKING

from odsslicer.formulas import _Remap, _remap_odf_formula_references, _rename_odf_formula_sheet

if TYPE_CHECKING:
    from odsslicer.reader import ODSReader

__all__: list[str] = []


class _References:
    """The formulas and drawings of `reader`'s document, following an edit.
    A sheet of no document - `reader` None - has none to follow."""

    def __init__(self, reader: "ODSReader | None") -> None:
        self._reader = reader

    def read_first(self) -> None:
        """Parse the content of the document's charts, if not done yet,
        before a structural edit changes anything: a chart's part that is
        not well-formed raises `MalformedDocumentError` with the document as
        it was, rather than once the edit is halfway through (see
        `_Package.parse_objects`)."""
        if self._reader is not None:
            self._reader._package.parse_objects()

    def remap(self, sheet_name: str, remap: _Remap) -> None:
        """After a structural edit of sheet `sheet_name` (rows or columns
        deleted or inserted), rewrite every formula in the whole document -
        that sheet's own, and any other sheet's formula that references into
        it by name - through `remap` (see `_deletion_remap`/
        `_insertion_remap`), so each reference still points at the cell it
        did before.

        Charts follow too, their ranges and the cell their frame ends in
        (see `_Drawings.remap`, issue #15); pivot-table source ranges, named
        ranges, conditional-format and validation ranges are left as they
        are. One full-document sweep per call - which is why `delete_rows`
        batches N rows into one call."""
        if self._reader is None:
            return
        for sheet in self._reader.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell._formula is None:
                        continue
                    adjusted = _remap_odf_formula_references(cell._formula, sheet_name, sheet.name, remap)
                    if adjusted != cell._formula:
                        cell._write_formula(adjusted)
        self._reader._drawings.remap(sheet_name, remap)

    def rename(self, old_name: str, new_name: str) -> None:
        """After sheet `old_name` was renamed `new_name`, rewrite every
        formula naming it, and every drawing (see `_Drawings.rename`)."""
        if self._reader is None:
            return
        for sheet in self._reader.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell._formula is None:
                        continue
                    renamed = _rename_odf_formula_sheet(cell._formula, old_name, new_name)
                    if renamed != cell._formula:
                        cell._write_formula(renamed)
        self._reader._drawings.rename(old_name, new_name)
