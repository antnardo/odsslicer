# Roadmap

## Fait

- Formats numériques from scratch (`NumberFormat.create`), mises en forme conditionnelles en
  écriture (`.add_condition`), copie de style cellule à cellule (`cell.style = other.style`).
- Suppression de lignes/colonnes (`Sheet.delete_row`/`.delete_column`) et de feuilles
  (`ODSReader.delete_sheet`).
- Copier-coller de cellules/plages (`Sheet.copy`).
- Propriétés du fichier (`ODSReader.properties` / `DocumentProperties`) : titre, sujet,
  description, auteur, mots-clés et propriétés personnalisées typées (`meta:user-defined`),
  en lecture et en écriture — `meta.xml` est maintenant régénéré à la sauvegarde comme
  `content.xml`.
- Ajustement des références de formule lors d'un `delete_row`/`delete_column`, y compris
  inter-feuilles (`Sheet1.A6`) — une référence pointant exactement sur la ligne/colonne
  supprimée reste inchangée (pas d'équivalent `#REF!`, cohérent avec l'absence de moteur de
  calcul).

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

## Autres

- Gros classeurs au-delà de `MAX_REPEAT_ROWS`/`MAX_REPEAT_COLS` : les lignes/colonnes répétées
  surnuméraires sont détectées et jetées plutôt que matérialisées.
