# Roadmap

## Styles

- Créer un format numérique **from scratch** (`number:number-style`, `number:currency-style`,
  `number:date-style`...) — on ne peut aujourd'hui qu'assigner un `NumberFormat` déjà présent
  dans le document (`cell.style.number_format = autre_format`).
- Écrire les mises en forme **conditionnelles** (`.conditions` / `<style:map>`) — lecture seule
  pour l'instant.
- Copier/dupliquer un style complet d'une cellule vers une autre en une fois (pas de raccourci
  type `cell.style = other.style`).

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

- Suppression de lignes/colonnes/feuilles (on ne fait aujourd'hui que grandir/ajouter, jamais
  retirer).
- Copier-coller de cellules/plages (dupliquer valeur + style + formule d'une cellule vers une
  autre).
- Gros classeurs au-delà de `MAX_REPEAT_ROWS`/`MAX_REPEAT_COLS` : les lignes/colonnes répétées
  surnuméraires sont détectées et jetées plutôt que matérialisées.
