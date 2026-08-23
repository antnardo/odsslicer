# Roadmap

## Fait

- Formats numériques from scratch (`NumberFormat.create`), mises en forme conditionnelles en
  écriture (`.add_condition`), copie de style cellule à cellule (`cell.style = other.style`).
- Suppression de lignes/colonnes (`Sheet.delete_row`/`.delete_column`) et de feuilles
  (`ODSReader.delete_sheet`).
- Copier-coller de cellules/plages (`Sheet.copy`).

## Formules

- Pas de moteur de calcul : la valeur `office:value` mise en cache est lue telle quelle, jamais
  recalculée — écrire une formule n'actualise pas le résultat affiché.
- Les plages nommées et les références 3D (plusieurs feuilles) ne sont pas traduites par la
  syntaxe "friendly" — il faut passer par la syntaxe ODF brute (`[...]`).

## Texte affiché à l'écriture

- L'inférence du format d'affichage (`Cell._infer_number_display` et consorts) reste une
  heuristique par apprentissage sur d'autres cellules du document, pas une vraie lecture du
  moteur de formatage ODF/locale — peut se rabattre silencieusement sur une conversion brute
  si aucune cellule n'illustre déjà ce format.

## Propriétés du fichier

- `ODSReader.meta` ne garde aujourd'hui que les octets bruts de `meta.xml` (jamais parsés) : pas
  d'accès structuré aux propriétés type "Fichier > Propriétés" de LibreOffice — titre, auteur,
  mots-clés, commentaires (`dc:title`/`dc:creator`/`meta:keyword`/`dc:description`...) — ni aux
  propriétés personnalisées (`meta:user-defined`), ni en lecture ni en écriture.

## Autres

- Ajuster les références de formule lors d'un `delete_row`/`delete_column` (aujourd'hui elles
  ne bougent pas, comme documenté dans le README — cohérent avec l'absence de moteur de calcul,
  mais peut surprendre).
- Gros classeurs au-delà de `MAX_REPEAT_ROWS`/`MAX_REPEAT_COLS` : les lignes/colonnes répétées
  surnuméraires sont détectées et jetées plutôt que matérialisées.
