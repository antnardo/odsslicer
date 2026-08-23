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
- Texte affiché à l'écriture : quand aucune cellule du document n'illustre déjà le format
  (l'heuristique par apprentissage échoue), repli sur une vraie lecture du `NumberFormat`
  résolu de la cellule (décimales, groupement, symbole monétaire, composants date/heure)
  plutôt qu'une conversion brute — `_render_number_from_format`/`_render_date_time_from_format`.
  Reste approximatif sur la locale exacte (séparateurs `.`/`,` fixes, pas de lecture de
  `number:language`/`number:country`).
- Commentaires/annotations de cellule (`Cell.comment` / `Comment`, `office:annotation`) :
  texte (multi-ligne), auteur, date, visibilité — en lecture et en écriture. Au passage,
  correction d'un vrai bug latent : la lecture/écriture de `Cell.text`/`.value` n'était pas
  scopée aux enfants directs de `table:table-cell`, donc un commentaire (qui contient ses
  propres `text:p`) aurait pu être confondu avec la valeur de la cellule.
- Tri d'une plage (`Sheet.sort(source, by, ascending=True)`) : tri stable, `None` toujours en
  dernier, le style/la formule de chaque ligne suit (une formule même-ligne comme `=B2*C2`
  garde son sens après déplacement, mêmes règles de décalage relatif que `Cell.fill_formula`).
- Renommer (`ODSReader.rename_sheet`) et réordonner (`.move_sheet`) les feuilles. Le
  renommage met aussi à jour les références de formule inter-feuilles qui nomment
  explicitement l'ancien nom (`Sheet1.A6` → `NouveauNom.A6`, avec guillemets automatiques si
  besoin) ; une référence non qualifiée dans les formules de la feuille elle-même n'a pas
  besoin d'être touchée.

## Formules

- Pas de moteur de calcul : la valeur `office:value` mise en cache est lue telle quelle, jamais
  recalculée — écrire une formule n'actualise pas le résultat affiché. Gros morceau, sans doute
  hors de portée d'une lib légère — à séparer d'un futur support des plages nommées (voir
  ci-dessous), qui lui est tractable indépendamment.
- Les plages nommées (`table:named-range`) ne sont ni lues, ni créables, ni traduites par la
  syntaxe "friendly" des formules — il faut les référencer via la syntaxe ODF brute (`[...]`).
- Les références 3D (une plage sur plusieurs feuilles, `Sheet1:Sheet3.A1`) ne sont pas non plus
  traduites.

## Gaps identifiés (rien d'entamé) — probablement le plus utile

- **Liens hypertexte dans une cellule** (`<text:a xlink:href="...">` autour du texte) : pas lus,
  pas écrits — `Cell.text` les aplatit dans le texte brut.

## Gaps identifiés — utiles mais plus de niche

- Validation de données / listes déroulantes (`table:content-validations`).
- Filtres automatiques / plages de base de données (`table:database-ranges`).
- Volets figés / vue scindée (config-items dans `settings.xml`).
- Protection au niveau de la feuille entière (`table:protected` sur `table:table` — distinct de
  `style:cell-protect`, déjà supporté par cellule).
- Regroupement de lignes/colonnes (plan, `table:table-row-group`).
- Texte enrichi partiel dans une cellule (un mot en gras au milieu d'une phrase) — `Cell.text`
  aplatit tout aujourd'hui ; écrire ce genre de mise en forme mixte serait un chantier à part.

## Probablement hors de portée durablement

- Graphiques et images embarquées (`office:chart`, `draw:frame`/`draw:image`) — gros morceau,
  peu probable qu'une lib de lecture/écriture de données s'y attaque.
- Mise en page/impression (`style:master-page`, `style:page-layout`, en-têtes/pieds de page).
- Tableaux croisés dynamiques (`table:data-pilot-tables`).
- Vraie prise en compte de la locale du document dans le rendu du texte affiché
  (`number:language`/`number:country` sur les `NumberFormat`) — actuellement approximé avec un
  séparateur `.`/`,` fixe.
