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
- Liens hypertexte dans une cellule (`Cell.hyperlink`, `<text:a xlink:href="...">`) — lien sur
  la cellule entière uniquement (pas sur une portion du texte, voir "texte enrichi partiel"
  ci-dessous). Écrire une nouvelle `.value` efface le lien, comme dans un vrai tableur.
- Tableaux croisés dynamiques (`Sheet.create_pivot_table`, `table:data-pilot-table`) :
  définition ODF uniquement (source, champs ligne/colonne/données, fonction d'agrégation),
  même philosophie que les formules — on décrit, le tableur calcule. Différence vérifiée
  empiriquement avec LibreOffice : contrairement à une formule, un TCD n'est **pas** recalculé
  automatiquement à l'ouverture — la définition est reconnue et modifiable depuis l'interface,
  mais la zone cible reste vide jusqu'à un `Données > Pivot > Actualiser` explicite. Pas de
  `data-pilot-level`/tri/sous-totaux/champ "page" (filtre) : à ajouter si besoin, la structure
  est en place.
- Recalcul via LibreOffice (`recalculate(path)` / `save(path, recalculate=True)`) : délègue
  à un LibreOffice local en headless le calcul de toutes les formules (y compris celles dont la
  valeur en cache est obsolète) et le rafraîchissement des tableaux croisés (matérialisation
  de la grille de résultat). Profil utilisateur jetable dans un répertoire temporaire (le
  profil de l'utilisateur n'est jamais touché), script exécuté par le Python embarqué de
  LibreOffice via le scripting framework (pas de `python-uno` système). Commande configurable
  en tête de module (`LIBREOFFICE_COMMAND`), avec détection des emplacements d'installation
  usuels. Vérifié : formule obsolète 6.4 → 103.0, pivot vide → grille complète, en ~2 s.
  Note d'investigation : la voie "macro Basic dans le profil jetable" (`macro:///…`) n'a
  jamais démarré malgré des fichiers `.xlc/.xlb` calqués sur un vrai profil — la voie Python
  du scripting framework marche du premier coup et n'a besoin d'aucun enregistrement.

## Formules

- Pas de moteur de calcul *interne* : la valeur `office:value` mise en cache est lue telle
  quelle. Couvert en pratique par `recalculate()` ci-dessus (délégation à LibreOffice) ; un
  vrai moteur en pur Python reste hors de portée d'une lib légère.
- Les plages nommées (`table:named-range`) ne sont ni lues, ni créables, ni traduites par la
  syntaxe "friendly" des formules — il faut les référencer via la syntaxe ODF brute (`[...]`).
- Les références 3D (une plage sur plusieurs feuilles, `Sheet1:Sheet3.A1`) ne sont pas non plus
  traduites.

## Vers 1.0 — consolidation (bilan post-0.9, par ordre de priorité)

Le constat général au moment de la 0.9 : les fonctionnalités voulues sont là et fiables pour
l'usage qui a motivé le module ; ce qui le sépare d'un 1.0 serein est de la consolidation, pas
des features.

1. ~~**Restructuration de `classes.py`**~~ **Fait** : les ~3 850 lignes sont découpées en 10
   modules thématiques (`addresses`, `constants`, `xmlutils`, `formulas`, `styles`, `cell`,
   `sheet`, `properties`, `libreoffice`, `reader` — le plus gros fait ~1 000 lignes), code
   déplacé verbatim (zéro changement de comportement), `classes.py` conservé en shim de
   compatibilité (`from odsslicer.classes import Sheet` marche toujours), pyflakes propre.
   Reste de ce point : le nettoyage des lignes répétées dans `Sheet.load()` (commentaire
   `works but nasty` de 2021) a été déplacé tel quel — toujours la partie la plus fragile du
   code, à réécrire un jour avec des tests dédiés.
2. ~~**Suite LibreOffice en CI**~~ **Fait** : job `test-libreoffice` sur ubuntu-latest
   (LibreOffice du PPA officiel + `libreoffice-script-provider-python`), qui exécute la suite
   de cohérence puis toute la suite à chaque push. La stabilisation a rapporté gros — deux
   vrais problèmes de portabilité découverts et corrigés :
   - le Python embarqué de LibreOffice (builds Ubuntu) découvre son préfixe en cherchant
     `python3` dans le `PATH` — un interpréteur étranger en tête (venv actif, toolcache CI)
     lui faisait charger une stdlib incompatible et crasher pyuno en `std::bad_alloc` avant
     même notre code. `recalculate()` blinde désormais l'environnement du sous-processus
     (purge `PYTHONPATH`/`PYTHONHOME`/`LD_LIBRARY_PATH` + PATH filtré des interpréteurs
     étrangers) — correctif utile à tout utilisateur Ubuntu sous venv, pas juste à la CI ;
   - l'export Flat ODF (fods) de LibreOffice 24.2 perd les annotations (bizarrerie du filtre,
     le round-trip .ods les préserve) — le test des commentaires passe maintenant par un
     round-trip .ods relu par odsslicer, plus robuste et plus probant.
3. **Performance : zéro donnée aujourd'hui.** Chaque cellule est un objet Python matérialisé à
   l'ouverture ; `delete_row` rescanne toutes les formules du document à chaque appel (en
   supprimer 100 = 100 balayages) ; `sort` réécrit cellule par cellule via les setters XML.
   Invisible sur des petits fichiers, limite inconnue sur un classeur de 100 000 lignes. À
   faire : un test de charge simple, documenter les limites constatées, optimiser si besoin.
4. **Scories d'API à trancher avant 1.0** (dernier moment indolore) : `ODSReader` est un faux
   nom (c'est un lecteur-écrivain-créateur — un alias `ODS = ODSReader` ?) ; `sheet(name)`
   lève `IndexError` là où `KeyError` serait naturel ; `verbose` et le `[WARNING]` de `load()`
   passent par `print` au lieu de `logging` ; pas de marqueur `py.typed` ni d'annotations
   complètes.
5. **Maturité d'écosystème** : pas de `CHANGELOG.md` dans le repo (seulement les release
   notes GitHub) ; bus factor de 1 ; l'API n'a jamais été confrontée à des fichiers ODS
   "sauvages" produits par Excel, un export Google Sheets ou de vieux OpenOffice — en
   collectionner quelques-uns comme fixtures serait un bon investissement.

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
- Vraie prise en compte de la locale du document dans le rendu du texte affiché
  (`number:language`/`number:country` sur les `NumberFormat`) — actuellement approximé avec un
  séparateur `.`/`,` fixe.
