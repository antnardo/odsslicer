# -*- coding: utf-8 -*-
"""
Created 2021

@author: elessar

ods = ODSReader(file) : contient les sheets
ods.sheets_names : liste des noms
ods.sheets : liste de Sheets
sheet = ods.sheet(name)

sheet["A1"] : cell A1 (0, 0)
sheet[0, 0] : idem
sheet[0], sheet["1"]
sheet[:, 0], sheet["A"]
sheet["A1:B3"], sheet[0:3, 0:2]
Si non défini : renvoit None avec la bonne taille
Le shape correspond exactement à ce qu'on s'attend en numpy

Retourne un ArrayValue : liste de Cell ou liste de liste de Cells
Cell contient toutes les infos de formattage
ArrayValue.to_list() pour n'avoir que les données
ArrayValue.to_numpy()
(marche aussi avec Sheet)
Attention, comme numpy, un format (n x 1) (1 colonne) est de dimension 2
On peut l'avoir en (n) avec .to_vector()
"""
from .classes import ODSReader, FORMATS, CellStyle, NumberFormat, Border, RowStyle, ColumnStyle, TableStyle