from odsslicer import ODSReader
from odsslicer.classes import Sheet
from pathlib import Path
import datetime as dt

assert Sheet.string_address(1, 1) == "B2"
assert Sheet.string_address(1, 26) == "AA2"
assert Sheet.address("A1") == (0, 0)
assert Sheet.address("Z2") == (1, 25)
assert Sheet.address("AA1") == (0, 26)
for s in ["1A", "A1=", "A:2", "2:A", "B:A"]:
    try:
        Sheet.address(s)
    except ValueError:
        pass
    else:
        raise AssertionError
row, col = Sheet.address("A1:A10")
assert (row.start, row.stop, row.step) == (0, 10, None)
assert col == 0
row, col = Sheet.address("A1:C1")
assert row == 0
assert (col.start, col.stop, col.step) == (0, 3, None)
row, col = Sheet.address("B2:C5")
assert (row.start, row.stop, row.step) == (1, 5, None)
assert (col.start, col.stop, col.step) == (1, 3, None)
row, col = Sheet.address("A1:A1")
assert row == 0 and col == 0


FILE = Path("TEST.ods")
ods = ODSReader(FILE)
ods.export_content_xml(pretty=True)
s = ods.sheet('Sheet1')

# Test address conversions
assert s["A1"] == s[0, 0]
assert s["1"] == s[0]
assert s["A"] == s[:, 0]
assert s["A1:B3"] == s[0:3, 0:2]

assert len(s[1:1]) == 0

# Test values
assert s["A1"].value == "texte simple"
assert s["B1"].value == "seconde colonne"
assert s["A2"].value == 3.4
assert s["A3"].value == 3
assert s["A4"].value == "3/2"
assert s["A5"].value == 6.4 and s["A5"].is_formula
assert s["A6"].value == 2
assert s["A7"].value == 2
assert s["A8"].value == dt.date(2021, 2, 28)
assert s["A9"].value == dt.time(15, 0, 0)

# Test repeat columns and rows and values
s = ods.sheet('Sheet2Repeat')
assert s.to_numpy().shape == s.size == (9, 6)
assert s["A1:D2"].to_list() == [[1, 1, 1, 1], [1, 1, 1, 1]]
assert s["F9"].value == 5
assert s["F8"].value is None

# Test shapes and outside values
s = ods.sheet('SheetEmpty')
assert s["A1"].value is None
assert s["ZZ1"].value is None
assert s["ZZZ2222222"].value is None
assert s["B1"].value is None
assert s["A2"].value is None
assert s["A1:C1"].to_list() == [None]*3
assert s["A1:C2"].to_list() == [[None]*3, [None]*3]
assert s[0:10:2, 0].to_list() == [[None] for _ in range(5)]
assert s[0:10:2, :2].to_list() == [[None, None] for _ in range(5)]
assert s[0, 0:5:2].to_list() == [None]*3

s = ods.sheet("SheetFusion")
assert s.size == (9, 4)
assert s["A4"].value == 5  # hidden in cols
assert s["B4"].value == 7  # not hidden
assert s["C1"].value == 3  # hidden in rows
assert s["C9"].value == 1  # hidden and repeated
