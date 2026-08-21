# odsslicer

Lecteur Python pour les fichiers `.ods` (OpenDocument Spreadsheet, LibreOffice/OpenOffice Calc),
avec une API d'indexation inspirée de numpy : `sheet["A1"]`, `sheet[0, 0]`, `sheet["A1:B3"]`,
slices Python classiques, etc.

Le module parse directement `content.xml` (via BeautifulSoup) et gère les types de cellule
ODF (texte, nombre, pourcentage, devise, date, heure, booléen), les formules, ainsi que les
lignes/colonnes répétées et fusionnées.

Support d'écriture : `cell.value = ...` puis `reader.save(...)`. Les cellules répétées ou
fusionnées sont automatiquement dépliées/défusionnées en arrière-plan au premier accès en
écriture, et écrire au-delà de l'étendue actuelle d'une feuille l'agrandit automatiquement
(nouvelles lignes/colonnes) — voir [Écriture](#écriture-expérimentale) ci-dessous pour le
détail et les limites restantes.

## Installation

Pas encore publié sur PyPI (nom réservé : `odsslicer`). En attendant, copier le dossier
`odsslicer/` dans un projet qui l'a comme sous-dossier importable, ou l'ajouter au `PYTHONPATH`.

### Dépendances

- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) + `lxml` (parseur XML)
- [`numpy`](https://pypi.org/project/numpy/)

```bash
pip install beautifulsoup4 lxml numpy
```

## Usage rapide

```python
from odsslicer import ODSReader
from pathlib import Path

table = ODSReader(Path("classeur.ods"))
table.sheets_names        # ["Sheet1", "Sheet2", ...]
table.sheets               # liste de Sheet (mise en cache)
sheet = table.sheet("Sheet1")

sheet["A1"]                # cellule A1 (Cell)
sheet[0, 0]                 # équivalent : (ligne, colonne), 0-indexé
sheet[0]                    # ligne 1 entière (comme sheet["1"])
sheet[:, 0]                  # colonne A entière (comme sheet["A"])
sheet["A1:B3"]               # bloc, équivalent à sheet[0:3, 0:2]

sheet["ZZZ100000"]          # hors du tableau : renvoie une cellule vide (value=None), pas d'erreur
```

Une adresse ou un slice hors des données renvoie toujours des cellules vides (`value=None`)
de la bonne forme, plutôt qu'une erreur — la forme (shape) suit les mêmes conventions que
numpy (une colonne (n, 1) reste bien 2D, cf. `to_vector()` ci-dessous pour l'aplatir).

### Cellules (`Cell`)

```python
cell = sheet["A1"]
cell.value          # valeur typée (str / float / bool / datetime.date / datetime.time / None)
cell.text           # texte tel qu'affiché dans le tableur (toujours une str, ou None)
str(cell)            # == cell.text (ou "None")
cell.format          # "string" / "float" / "percentage" / "currency" / "date" / "time" / "boolean" / None
cell.row, cell.col   # position 0-indexée
cell.address         # adresse style tableur, ex. "A1", "AZ12"
cell.is_formula      # True si la cellule contient une formule ODF
cell.is_empty        # True si aucune valeur/texte/format n'est défini
```

`Cell` supporte les conversions numériques usuelles (`int()`, `float()`, `round()`, `abs()`,
`-`, `+`, `math.trunc/ceil/floor`) et les comparaisons (`==`, `<`, `>`, `<=`, `>=`), qui portent
toutes sur `cell.value`. Attention : comparer une cellule vide (`value=None`) à une cellule
numérique lève un `TypeError`, comme en Python normal (`None < 3.4`).

Les formats disponibles sont listés dans `odsslicer.FORMATS` (dict format ODF -> callable de
conversion).

### Tableaux (`ArrayValues`)

Toute sélection multi-cellules (`sheet[0]`, `sheet[:, 0]`, `sheet["A1:B3"]`, itération sur une
`Sheet`...) renvoie un objet `ArrayValues`, wrapper autour d'une liste de `Cell` (1D) ou d'une
liste de listes de `Cell` (2D) :

```python
arr = sheet["A1:B3"]
arr.dimension     # 0 (une cellule seule), 1 (ligne/colonne) ou 2 (bloc)
arr.size           # shape façon numpy, ex. (3, 2)
arr.to_list()       # valeurs brutes (list ou list de list), sans les objets Cell
arr.to_numpy()      # np.array des valeurs
arr.to_vector()     # pour une shape (n, 1) : renvoie un ArrayValues 1D de taille (n,)
```

L'égalité (`==`) entre deux `ArrayValues` compare les valeurs (`to_list()`), pas l'identité des
objets `Cell`.

### Itération

```python
for row in sheet:              # équivalent à sheet[:]
    for cell in row:
        print(cell.address, cell.value)
```

## Écriture (expérimentale)

`Cell.value` est réinscriptible — la nouvelle valeur remplace le contenu XML sous-jacent
directement en mémoire :

```python
from odsslicer import ODSReader

table = ODSReader("classeur.ods")
sheet = table.sheet("Sheet1")

sheet["A1"].value = "nouveau texte"
sheet["A2"].value = 42.5
sheet["A3"].value = None              # vide la cellule

table.save("classeur_modifie.ods")     # ou table.save() pour écraser le fichier source
```

Types acceptés en écriture : `str`, `int`/`float`, `bool`, `datetime.date`, `datetime.time`, et
`None` (vide la cellule). En écrivant un nombre sur une cellule déjà au format `percentage` ou
`currency`, ce format est conservé. Écrire sur une cellule qui contenait une formule efface
la formule (`is_formula` redevient `False`).

`ODSReader.save(path=None)` réécrit le `.ods` : `content.xml` est régénéré à partir de l'arbre
en mémoire, tous les autres membres du zip (`styles.xml`, `meta.xml`, `settings.xml`,
`manifest.xml`, miniature...) sont recopiés tels quels depuis le fichier source, et la
convention ODF (`mimetype` en premier, non compressé) est respectée. Sans argument, `save()`
écrase le fichier source.

### Dépliage automatique des cellules répétées et fusionnées

ODS compresse les lignes/colonnes identiques en un seul élément XML partagé entre plusieurs
`Cell`, et représente une fusion via une cellule maîtresse (haut-gauche, porteuse des attributs
`table:number-*-spanned`) plus des cellules `table:covered-table-cell` cachées. Écrire dans une
de ces cellules déclenche automatiquement, en arrière-plan, le "dépliage" de la structure
concernée — la ligne/colonne compressée est scindée en éléments XML individuels, et/ou la
fusion est défaite — avant que la nouvelle valeur ne soit posée :

```python
sheet["C5"].value = 42   # C5 faisait partie d'un bloc de 6 lignes compressées : le bloc
                          # est scindé en 6 lignes indépendantes, seule C5 change de valeur,
                          # les 35 autres cellules du bloc gardent leur valeur d'origine
```

Écrire dans une cellule fusionnée (maîtresse ou cachée) défait toute la fusion : chaque
cellule auparavant cachée redevient indépendante et révèle sa propre valeur — ODF la conserve
déjà en interne sous `table:covered-table-cell`, exactement comme le ferait LibreOffice en
défusionnant manuellement. Les objets `Cell` déjà obtenus avant l'écriture restent valides et
sont automatiquement repointés vers leur nouvel élément XML individuel ; `sheet.size` ne change
jamais suite à un dépliage (le nombre de lignes/colonnes logiques était déjà celui-là).

### Agrandissement automatique de la feuille

Écrire sur une adresse en dehors de l'étendue actuelle (`sheet.size`) agrandit la feuille au
lieu de lever une erreur : les lignes existantes sont élargies avec des cellules vides si la
colonne demandée dépasse la largeur actuelle, puis de nouvelles lignes (pleine largeur, vides)
sont ajoutées si la ligne demandée dépasse la hauteur actuelle — y compris pour agrandir une
feuille totalement vide (`sheet.size == (0, 0)`) depuis rien :

```python
sheet.size            # (9, 2)
sheet["E12"].value = "coin"
sheet.size            # (12, 5) : lignes 10-12 ajoutées, colonnes C-E ajoutées, tout le reste vide
```

`sheet.size`/`n_rows`/`n_cols` reflètent immédiatement la nouvelle étendue, et un simple accès
en lecture (`sheet.get_row(50)`, `sheet["Z1"].value` sans assignation) n'agrandit jamais rien —
seule une écriture (`.value = ...`) déclenche la croissance. Les nouvelles lignes/cellules
n'héritent d'aucun style particulier (formatage par défaut).

### Texte affiché : appris d'un exemple plutôt qu'une conversion brute

ODF ne stocke pas que la valeur d'une cellule (`office:value`) : il stocke aussi le texte tel
qu'affiché (`text:p`), typiquement formaté selon la locale du document (séparateur décimal,
suffixe `%`/`€`, format de date...). Plutôt que d'imposer un format arbitraire à l'écriture,
`odsslicer` cherche une **autre cellule du même format** dans le document (en priorité
l'ancien contenu de la cellule elle-même si elle avait déjà une valeur), compare sa valeur
brute à son texte affiché pour en déduire un patron (séparateur décimal, nombre de décimales,
préfixe/suffixe, ou motif de date `%d/%m/%y` etc.), vérifie que ce patron reproduit bien
l'exemple, puis l'applique à la nouvelle valeur :

```python
sheet["A6"].text    # "200,00 %" (valeur 2.0)
sheet["A6"].value = 0.5
sheet["A6"].text    # "50,00 %" — même style que l'ancien contenu de la cellule

sheet["A8"].text    # "28/02/21" (format jour/mois/année sur 2 chiffres)
sheet["A8"].value = date(2030, 1, 5)
sheet["A8"].text    # "05/01/30"
```

Si aucun exemple n'est trouvé, ou si le patron déduit ne reproduit pas exactement le texte de
l'exemple (donc jugé non fiable), `odsslicer` revient sur une conversion Python simple plutôt
que de produire un texte incohérent. Pour les nombres "généraux" (format `float` simple, pas
pourcentage/devise), seul le séparateur décimal est repris — jamais le nombre de décimales,
qui tronquerait la précision de la nouvelle valeur.

### Ce qui n'est **pas** supporté

- Pas d'écriture de formule, pas de création de nouvelle feuille.
- Pas de véritable moteur de formatage ODF (résolution de `styles.xml`, locale du document,
  devise réelle) : l'inférence ci-dessus est une heuristique par exemple, pas une lecture du
  style de la cellule — elle peut échouer silencieusement (repli sur une conversion simple)
  sur un format qu'aucune autre cellule du document n'illustre déjà.

## Adressage des cellules

`Sheet.address(string, n_rows=1)` convertit une adresse texte en index/slice Python :

| Notation      | Résultat                              |
|---------------|----------------------------------------|
| `"A1"`        | `(0, 0)` — (ligne, colonne)             |
| `"1"`         | `0` — ligne seule                       |
| `"A"`         | `(slice(n_rows), 0)` — colonne entière  |
| `"A1:B3"`     | `(slice(0, 3), slice(0, 2))`            |
| `"A:B"`       | `(slice(n_rows), slice(0, 2))`          |
| `"1:2"`       | `slice(0, 2)`                            |

Une adresse malformée (`"1A"`, `"A:2"`, `"2:A"`, `"B:A"`...) lève un `ValueError`.

`Sheet.string_address(row, col)` fait la conversion inverse (index 0-indexé -> `"A1"`,
`"AZ12"`...) et `Sheet.string_to_col("AZ")` convertit des lettres de colonne en index — les
deux utilisent la numération bijective en base 26 habituelle des tableurs (`Z` = 25,
`AA` = 26, `AZ` = 51, `BA` = 52...).

## Limites connues

- **Écriture** : voir les limites détaillées dans [Écriture (expérimentale)](#écriture-expérimentale)
  ci-dessus — seules les cellules simples (non répétées, non fusionnées) sont modifiables.
- **Formules** : la valeur mise en cache par le tableur est lue (`office:value`), la formule
  elle-même n'est pas ré-évaluée (et l'écriture ne recalcule évidemment rien non plus).
- Les feuilles/lignes/colonnes vides au-delà de `MAX_REPEAT_ROWS` / `MAX_REPEAT_COLS`
  (voir `classes.py`) sont détectées et écartées pour éviter de matérialiser des lignes ou
  colonnes de taille `2**20`/`2**10` créées par LibreOffice pour le style par défaut d'une
  feuille — un avertissement `[WARNING]` est affiché si une incohérence de longueur de ligne
  est détectée après ce nettoyage.

## Tests

```bash
pip install pytest beautifulsoup4 lxml numpy
pytest odsslicer/tests/
```

La suite (`odsslicer/tests/test_odsslicer.py`) couvre l'adressage (`Sheet.address`,
`Sheet.string_address`/`string_to_col`), les types de cellule, les lignes/colonnes répétées et
fusionnées, les feuilles vides, `ArrayValues`, l'écriture (`Cell.value = ...`, `ODSReader.save()`
et ses garde-fous), ainsi que des tests de non-régression pour les bugs corrigés (voir plus bas).

## Historique des corrections apportées avant publication

En relisant le module pour cette publication, les bugs suivants (présents dans la version
interne) ont été corrigés dans `classes.py` :

1. **`Sheet.string_address`** produisait une adresse fausse pour la plupart des colonnes
   multi-lettres (ex. colonne 27 → `"BB1"` au lieu de `"AB1"`, colonne 51 → `"ZZ1"` au lieu de
   `"AZ1"`) à cause d'une numération en base 26 mal implémentée. Corrigé avec l'algorithme de
   numération bijective standard.
2. **`Sheet.get_col`** comparait l'index de colonne demandé à `self.n_rows` au lieu de
   `self.n_cols` pour détecter un dépassement : sur toute feuille où il y a plus de lignes que
   de colonnes, demander une colonne hors bornes levait une `IndexError` au lieu de renvoyer
   une colonne vide.
3. L'avertissement `[WARNING]` de lignes de longueurs différentes ne se déclenchait **jamais**,
   même en présence d'une vraie incohérence : `rows_len` était un itérateur `map` épuisé une
   première fois par `max()`, donc vide lors de la seconde lecture pour le calcul de
   l'avertissement.
4. **`Sheet.empty_row` / `Sheet.empty_col`**, quand on leur passait explicitement l'argument
   `slice`, renvoyaient un élément de moins que prévu (un nombre d'éléments était recalculé
   puis réutilisé à tort comme borne d'arrêt d'un `range`).
5. **`ODSReader.sheets`** était une propriété-générateur à usage unique (impossible d'en faire
   `len()` ou de la parcourir deux fois), avec en plus du code mort après le `yield` qui ne
   pouvait jamais s'exécuter. Remplacé par une liste simple, réutilisable.
6. `Cell.__floot__` (faute de frappe pour `__floor__`) a été corrigé — sans impact fonctionnel
   observé (Python retombait sur `__float__` pour `math.floor()`), mais gardait un nom trompeur.
7. **Lecture des cellules booléennes** : le format `"boolean"` cherchait la valeur dans
   `office:value` (comme les nombres) au lieu de l'attribut ODF réel `office:boolean-value`, et
   la convertissait avec `bool(s)` — qui renvoie `True` pour la chaîne non vide `"false"`. Une
   vraie cellule booléenne ODF se lisait donc toujours comme `False`. Corrigé (lecture et
   écriture désormais symétriques pour ce format).
8. **`Cell.text`/`str(cell)` renvoyaient la chaîne littérale `"None"`** au lieu du texte réel,
   dans deux cas fréquents : une cellule dont le `<text:p>` est vide (typiquement une formule
   dont le résultat mis en cache est une chaîne vide) et une cellule dont le texte est réparti
   sur plusieurs nœuds (`<text:span>` pour une mise en forme partielle, ex. "1er" avec le "er"
   en exposant). Dans les deux cas, `text:p.string` (bs4) vaut `None` dès qu'il n'y a pas
   *exactement* un seul enfant texte, et l'ancien code faisait `str(p.string)`, transformant ce
   `None` en la chaîne `"None"`. Corrigé en utilisant `p.get_text()`, qui concatène correctement
   tout le texte descendant (et renvoie `""` pour une cellule réellement vide).

Tous ces cas sont couverts par des tests de non-régression dans
`odsslicer/tests/test_odsslicer.py`.

## Existe-t-il déjà des modules PyPI équivalents ?

| Package | Lecture/Écriture | Dernière release | Statut |
|---|---|---|---|
| `odfpy` | R/W bas niveau | jan. 2020 | Quasi à l'abandon (82 issues ouvertes), mais reste la brique utilisée en interne par pandas |
| `pyexcel-ods(3)` | R/W | > 1 an | Inactif |
| `ezodf` | R/W | déc. 2015 | Abandonné depuis 10 ans |
| `pandas` (`engine="odf"`) | Lecture (délègue à odfpy) | suit pandas | Pratique mais perd formules/formats fins |
| `python-calamine` | Lecture seule (Rust), rapide | active | Le plus activement maintenu pour de la lecture pure |
| `odfdo` (fork moderne d'odfpy) | R/W complet | récente, régulière | Activement maintenu, API DOM-like |
| `pandas-ods-reader` | Lecture seule -> DataFrame | mai 2025 | Maintenu, périmètre limité |

Aucun de ces paquets n'offre l'API de type numpy (`sheet["A1"]`, slicing par adresse de
cellule) ni la même granularité sur les formats de cellule (devise/pourcentage/date/heure) et
les cellules fusionnées/répétées — c'est le principal argument pour publier ce module plutôt
que de simplement recommander `odfdo` ou `python-calamine`.

## À faire avant de publier sur GitHub

- Créer le dépôt / réserver le nom `odsslicer` sur PyPI (disponible au 2026-07-29).

`legacy.py` (implémentation historique de 2014, non importée par le package) a été supprimé.
Le dossier `odsslicer/writing tests/` (scratch de développement, doublon de
`odsslicer/tests/TEST.ods`) et les fichiers `.xml`/`.txt` générés par `export_content_xml()`
sont désormais dans `.gitignore` plutôt que trackés.

## Licence

[MIT](LICENSE) — réutilisation quasi sans contrainte, juste garder la mention copyright.

## Nom du projet

Nom retenu : **`odsslicer`** (disponible sur PyPI au 2026-07-29), pour refléter le vrai
différenciateur du module — l'indexation/slicing façon numpy par adresse de cellule — plutôt
qu'un simple "lecteur ods" générique.
