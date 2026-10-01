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
3. ~~**Performance**~~ **Fait** : banc de mesure `benchmarks/bench.py` (1k/10k/100k lignes ×
   5 colonnes), profilage cProfile des points chauds, résultats documentés dans DOCS
   (section Performance). Constats : tout est linéaire, la vraie limite est la **mémoire**
   (~4,5 Ko/cellule → ~2,3 Go de pic à 100k×5 ; au-delà, `python-calamine` pour la lecture
   pure). Deux optimisations mesurées :
   - l'inférence du texte affiché construisait ses candidats avidement — le `find_next`
     balayait tout le document à chaque écriture d'un format sans exemple (33 ms/cellule à
     10k lignes) ; générateur paresseux → **×32** (6,6 s → 0,2 s pour 200 dates), et bonus
     sur les chemins normaux (`sort` −40 %, écriture de plage −50 %) ;
   - nouvelle API `Sheet.delete_rows([...])` : un seul balayage d'ajustement des formules
     pour N lignes au lieu d'un par ligne (**×15** pour 100 lignes à 10k, davantage sur les
     gros documents), sémantique vérifiée identique aux suppressions séquentielles.
4. ~~**Scories d'API à trancher avant 1.0**~~ **Fait** (décisions : on garde le nom
   `ODSReader`) : `sheet(name)`/`delete_sheet`/`rename_sheet`/`move_sheet` lèvent maintenant
   `KeyError` pour un nom de feuille inconnu (`IndexError` reste pour les indices hors bornes) ;
   plus aucun `print` — tout passe par le logger `"odsslicer"` (DEBUG, INFO si `verbose=True`,
   WARNING pour les anomalies) ; annotations de type complètes sur toute l'API, vérifiées par
   mypy en CI (`disallow_untyped_defs`, job `typecheck`), marqueur `py.typed` embarqué. Au
   passage : correction d'un crash latent d'`export_content_xml()` avec un chemin `str`
   (normalisation de `self.file` en `Path`), et deux réusages de variables douteux nettoyés.
5. **Maturité d'écosystème** : ~~pas de `CHANGELOG.md`~~ (fait — `CHANGELOG.md` au format
   Keep a Changelog, lié depuis le README) ; bus factor de 1 ; ~~l'API n'a jamais été
   confrontée à des fichiers ODS "sauvages"~~ **Fait** : cinq fichiers réels d'open data
   (Excel 16 ×2, LibreOffice 3.5 de 2012, LibreOffice 26.2 Linux et Windows — noms de
   personnes caviardés avant inclusion, sources et licences dans `tests/wild/README.md`)
   servent de fixtures à `tests/test_wild_files.py` : ouverture, dimensions exactes, lecture
   intégrale, écriture, round-trip par odsslicer et par un vrai LibreOffice. L'investissement
   a payé immédiatement — trois vrais problèmes trouvés et corrigés :
   - Excel n'embarque pas de `settings.xml` (optionnel dans ODF) → l'ouverture plantait ;
     `styles.xml`/`meta.xml`/`settings.xml` sont maintenant optionnels, et `save()` écrit les
     parties régénérées même absentes du zip source ;
   - les « remplissages de grille » (cellule vide répétée 16 384 fois en fin de chaque ligne,
     bloc de lignes vides répété ~1 048 000 fois — Excel et LibreOffice déclarent ainsi la
     grille entière) faisaient construire des millions d'objets `Cell` : ouverture d'un
     fichier de 50 Ko en plusieurs minutes, voire blocage. `Sheet.load` normalise désormais
     chaque ligne à la largeur réelle de la feuille (seuls les suffixes *vides* sont bornés,
     aucune donnée ne bouge) → ouverture en quelques millisecondes ;
   - les lignes de largeurs inégales (fichiers Excel) faisaient planter la lecture de plage
     en `IndexError` — résolu par la même normalisation (lignes courtes complétées).
   La collection inclut aussi un export Google Sheets — révélateur en soi : Google convertit
   côté serveur via un LibreOfficeDev 6.0 headless (`meta:generator` :
   `LibreOfficeDev/6.0.5.2$Linux_X86_64`), donc de l'ODF très classique.

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

## Relecture de DOCS.md du 2026-10-01 — ce que coûterait de lever les limitations

Quatre points relevés en relisant la section « Known limitations », avec leur coût estimé.
Rien n'est engagé : noté pour décider plus tard.

### 1. Séparateurs des nombres selon la locale — toujours vrai, et asymétrique

Vérifié dans le code le 2026-10-01 : la limitation est exacte, mais seulement pour les
**nombres**. Les dates et heures lisent bien `number:language`/`number:country` du format, et
retombent sur la locale du document (`_date_names` + `_document_locale`, depuis la 0.13.1) ;
`_render_number_from_format` rend encore avec un `.`/`,` fixe (`f"{value:,.Nf}"`) et ignore la
locale du format.

À faire : étendre la table des 67 locales de `dateformats` aux séparateurs décimal et de
groupement, et rendre les nombres à travers elle. Petit travail, la table et le chemin de
résolution de locale existent déjà. Gain réel surtout pour `.text` lu par odsslicer : un vrai
tableur recalcule le texte affiché à l'ouverture.

### 2. Faire suivre les autres plages à une édition de structure — mécanique, mais un inventaire

Le remappage existe déjà et sert aux formules (`_remap_formula_references`) et aux graphiques
(`_remap_drawing_references`) : une plage de plus se branche dessus. Le travail n'est pas
l'algorithme mais l'inventaire des endroits où ODF écrit une plage, et leurs syntaxes :

- tableaux croisés : `table:source-cell-range`/`table:target-range-address` ;
- plages nommées : `table:named-range` (`table:cell-range-address`, `table:base-cell-address`)
  et `table:named-expression` (une expression entière, donc le remappage de formule) ;
- mises en forme conditionnelles : `calcext:conditional-format` (`calcext:target-range-address`)
  et les conditions des `style:map`, qui contiennent des références ;
- validations : `table:content-validation` (`table:condition`, `table:base-cell-address`) ;
- plages de base de données et filtres : `table:database-range` ;
- zones d'impression : `table:print-ranges` sur `table:table`.

Pièges : une liste de plages séparées par des espaces, les références 3D, et le fait qu'une
plage entièrement supprimée devrait devenir invalide (le remappage de suppression ne modélise
pas `#REF!`). Compter une journée avec les tests, par famille.

### 3. Texte enrichi partiel — une vraie fonctionnalité, pas un correctif

`text:span` dans un `text:p`, plus `text:a` sur une portion. Il faut un modèle de « suites »
(texte + style par portion), une API neuve (`cell.runs`), la création de styles de famille
`text` (`style:family="text"`, nouvelle famille à gérer dans le fork de style), et décider ce
que `.value`/`.text` font d'une cellule enrichie — aujourd'hui ils aplatissent. C'est le plus
gros des quatre : conception d'API avant code.

### 4. Les non-couverts — trois niveaux, et une marche commune

- **Immédiat** : protection de la feuille (`table:protected` + `table:protection-key` sur
  `table:table`) — un attribut, une propriété.
- **Contenu** : validations/listes déroulantes et filtres automatiques — une structure par
  feuille, référencée par les cellules (`table:content-validation-name`) ; même forme de travail
  que les tableaux croisés, déjà fait.
- **Regroupement de lignes/colonnes** : `table:table-row-group` imbrique les lignes ; le
  chargement les trouve déjà (recherche récursive), mais insertion, suppression et bornes de
  groupe demandent du soin.
- **La marche commune** : volets figés (`settings.xml`) et mise en page (`style:master-page`,
  `style:page-layout` dans `styles.xml`) sont hors de portée tant que `save()` recopie ces deux
  parties telles quelles. Les régénérer comme `content.xml` et `meta.xml` est le vrai préalable,
  et sert les deux d'un coup.
- **Création de graphiques** : écrire une partie `Object N/content.xml` et ses entrées de
  manifeste. La lecture est déjà là (les plages des graphiques sont lues et réécrites) ; créer
  reste un chantier à part entière.

### La contrainte à respecter si l'on régénère `styles.xml` et `settings.xml`

Soulevé le 2026-10-01 : la promesse du paquet est qu'on modifie des valeurs **sans** toucher au
style. Aujourd'hui cette promesse est absolue pour ces deux parties, parce que `save()` les
recopie octet pour octet — un relevé rapide sur 28 fichiers l'a confirmé : seuls `content.xml`
et `meta.xml` ressortent réécrits, jamais `styles.xml` ni `settings.xml`. Les régénérer
remplacerait une garantie mécanique par une confiance dans la fidélité de l'analyseur et du
sérialiseur, ce qui est un net recul.

Comment lever la limitation sans perdre la promesse :

1. **Régénération paresseuse, partie par partie.** Garder la recopie comme chemin par défaut et
   ne sérialiser une partie que si son arbre en mémoire a été modifié (un drapeau posé par les
   API qui y écrivent). Un fichier où l'on n'a changé que des valeurs ou des styles de cellule
   — ces derniers vivant dans `content.xml` — garde alors ses deux parties intactes à l'octet.
   `settings.xml` n'est même pas analysé aujourd'hui (lu en octets) : l'analyser à la demande,
   au premier accès.
2. **Invariant à mettre sous test** : une lecture suivie d'un enregistrement, sans édition, doit
   laisser `styles.xml` et `settings.xml` identiques à l'octet. Le balayage
   (`benchmarks/sweep_real_files.py`) est l'endroit pour l'affirmer sur de vrais fichiers, en
   plus d'un test unitaire.
3. **Mesure préalable à faire** (volontairement remise) : sur le corpus, comparer partie par
   partie ce qu'un aller-retour modifie déjà pour `content.xml` et `meta.xml` — attributs
   réordonnés, formes auto-fermantes, déclarations de namespace. Cela dira ce que coûte
   réellement la sérialisation bs4/lxml, donc ce qu'on accepterait pour les deux autres parties.
4. Sérialiser l'arbre analysé, jamais reconstruire : ce qu'odsslicer ne comprend pas (éléments
   inconnus, données binaires, parties d'une extension) doit traverser sans être interprété.
