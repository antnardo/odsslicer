import odsslicer as ods
from pathlib import Path
import datetime as dt

FILE = Path("example.ods")
table = ods.ODSReader(FILE)
print(repr(table))
print(table.sheets_names)
for sheet in table.sheets:
    print(repr(sheet))
    print(sheet.name)
    print(sheet.size)

s = table.sheet('Sheet1')

# Cell access
assert s["A1"] == s[0, 0] == s[0][0]
assert s["A2"] == s[1, 0]  # row first, as in numpy

# Cell class
# s["A1"] is a cell object
cell = s["A1"]
print(repr(cell))
# text is as printed in ods,
# str(cell) == cell.text or 'None' if cell.text is None
print(cell.value, cell.text, str(cell))
print(cell)  # reminder == str method
print(cell.format, cell.row, cell.col, cell.address)
print(cell.is_formula, cell.is_empty)

c = s["ZZZ100000"]  # does not raise an error: returns an empty cell: value=None
print(c.value, c.is_empty, c.col)

# Cell format values
print(list(ods.FORMATS.keys()))

assert s["B1"].value == "seconde colonne"
assert s["A2"].value == 3.4
assert s["A3"].value == 3
assert s["A4"].value == "3/2"
assert s["A5"].value == 6.4 and s["A5"].is_formula
assert s["A6"].value == 2 and s["A6"].format == "percentage"
print(s["A6"])  # text value (str)
assert s["A7"].value == 2 and s["A7"].format == "currency"
assert s["A8"].value == dt.date(2021, 2, 28)
assert s["A9"].value == dt.time(15, 0, 0)

# Arrays access
# ArrayValues Object : 0D = Cell, 1D = List[Cell] or 2D = List[List[Cell]]
# Allows conversions, equality is between lists and VALUES
assert s["A1:B1"] == s[0, 0:2]
assert s["A1:B3"] == s[0:3, 0:2]
assert s["A:B"] == s[:, 0:2]
# etc.

# Numerical conversions
print(repr(s["A2:B3"].to_numpy()))
print(s["A1:B1"].to_list())
print(s["A:B"].to_list())
print(s["1:2"].to_list())

# Iterations
for row in s:
    print(type(row), row)
    for cell in row:
        print(cell.format, cell.row, cell.col, cell.address, cell.value, str(cell), cell.is_formula, cell.is_empty)

for row in s[:]:  # you get an ArrayValue class, works identically
    print(type(row), row)
    for cell in row:
        print(cell, cell.value)  # print(cell) is print(str(cell)) is print(cell.text)

# Comparisons, only works with cells, not arrays, compares VALUES
# Beware of None values which do not compare...
print(s["A2"] < s["B2"])
