from odsslicer import ODSReader
from odsslicer.classes import Sheet
from pathlib import Path
import datetime as dt



FILE = Path("Notes.ods")
ods = ODSReader(FILE)
ods.export_content_xml(pretty=True)
s = ods.sheet('MPX4')

i = 53
A = s[i: i]

