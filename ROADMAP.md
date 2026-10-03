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
  `style:page-layout` dans `styles.xml`) demandaient que `save()` sache réécrire ces deux
  parties. La mécanique est là (régénération paresseuse, voir ci-dessous) ; ce qui reste pour
  les deux fonctionnalités, c'est le modèle et l'API — et la réserve de la mesure : la
  sérialisation abîme un format de nombre rembourré d'espaces, donc une partie qu'on réécrit ne
  revient pas intacte.
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

#### Ce que la mesure a dit, et ce qui est fait (2026-10-01)

Les points 1, 2 et 4 sont faits : `save()` recopie toute partie que rien n'a touchée, y compris
le `content.xml` d'un graphique, `settings.xml` s'analyse au premier accès
(`ODSReader.settings_data`), et l'invariant est sous test sur les huit fixtures du dépôt comme
dans le balayage.

Le point 3 a été mesuré sur ces huit fixtures (les six de `tests/wild/`, `TEST.ods`,
`WHOLEROW.ods`), et il **retient la main** : la sérialisation est fidèle au sens, pas à l'octet,
et pour `styles.xml` pas même au sens.

- Un aller-retour sans édition ne réécrit que `content.xml` et `meta.xml` : 8 fichiers sur 8,
  ce qui confirme le relevé des 28.
- Ce que la sérialisation change, sans rien perdre : la déclaration XML
  (`encoding="UTF-8" standalone="yes"` → `encoding="utf-8"`, et les CRLF d'Excel passent en LF),
  l'ordre des attributs (de 34 % à 53 % des balises ouvrantes selon le fichier — 1 427 sur 3 110
  pour le plus gros `content.xml`), `&apos;` et `&quot;` rendus en caractères (jusqu'à 123 dans
  un fichier), un élément vide replié en `<x/>` (0 → 4 dans un `meta.xml`). Le poids bouge de
  −615 à +0 octets. Les 35 déclarations de namespace restent toutes, aucune balise ni attribut
  ne disparaît : la forme canonique C14N des deux parties est identique avant et après.
- Ce que la sérialisation **perdait** : BeautifulSoup normalise tout nœud de texte entièrement
  blanc à un seul caractère (`'   '` → `' '`, `'\t\n '` → `'\n'`) — c'est `endData()` dans bs4,
  pas lxml, qui le fait seul est fidèle. Sur `styles.xml`, cela touchait 34
  `<number:text>   </number:text>` répartis sur 3 fichiers sur 8 (8, 13 et 13) : des formats
  comptables qui perdaient leur rembourrage, donc un alignement de colonne changé dans le
  tableur. La forme C14N de ces trois `styles.xml` n'était **pas** identique après un
  aller-retour.
- Le même défaut atteignait `content.xml`, sérialisé à chaque `save()`, dans toutes les
  versions publiées (reproduit sur la 0.14.1) : un `<number:text>   </number:text>` injecté dans
  ses styles automatiques ressortait à un espace. Aucune fixture n'en porte, parce que
  LibreOffice écrit un style de nombre là où vit le style de cellule qui l'utilise : dans
  `styles.xml` pour un style nommé, dans `content.xml` pour un format appliqué directement à
  une cellule — le cas d'un format comptable choisi dans la barre d'outils.

#### Le blanc est conservé (2026-10-01)

Le levier est bien `preserve_whitespace_tags`, mais ni l'une ni l'autre des deux voies
envisagées : bs4 compare ce jeu au **nom local** de chaque balise ouverte (`text`, pas
`number:text`), et ne réduit un nœud blanc que si **aucune** balise du jeu n'est ouverte. Il
suffit donc d'y mettre l'élément racine du document — `document-content`, `document-styles`…,
lu sur la première balise ouvrante, à coût constant (`_parse_xml`) — pour que tout ce qu'il
contient soit conservé tel quel, sans nommer une seule balise ODF (règle 4 respectée) et sans
passe sur les octets. Passer tous les noms de balises du document reviendrait au même, avec une
passe de plus ; en nommer quelques-unes aurait perdu en silence celles oubliées.

- Après correction, les 8 fixtures reviennent identiques en forme C14N pour chaque partie
  sérialisée — `content.xml`, `meta.xml`, et `styles.xml` quand on force sa sérialisation —, les
  34 nœuds rembourrés compris. Les seules différences restantes sont la déclaration XML, l'ordre
  des attributs, `&apos;`/`&quot;` rendus en caractères et `<x></x>` replié en `<x/>`, sans perte.
- Coût : la vérification se fait à chaque balise ouverte et fermée, soit +1,1 % à 1 000 lignes,
  +1,9 % à 10 000 et +2,4 % à 100 000 sur l'ouverture (fichiers de `benchmarks/bench.py`, processus
  frais entrelacés, médiane ; voir la section 12 de `DOCS.md`), rien en mémoire.
- Un effet de bord, qui est aussi une correction : une suite d'espaces seule dans un nœud de
  texte (`<text:p>   </text:p>`, les espaces avant un `<text:span>`, un span qui ne contient
  qu'eux) se lit désormais telle quelle, là où elle se lisait réduite à un espace. LibreOffice
  l'affiche telle quelle (vérifié par conversion d'un fichier portant chaque cas), et 2 des 250
  vrais fichiers balayés en portent : LibreOffice code les espaces répétés en `<text:s/>`, mais
  d'autres producteurs les écrivent en clair.
- Corrigé en 0.14.3 ([#27](https://github.com/antnardo/odsslicer/issues/27)) : `cell.text`
  ignorait `<text:s text:c="N"/>`, `<text:tab/>` et `<text:line-break/>`, que LibreOffice écrit
  pour deux espaces ou plus, une tabulation, un saut de ligne manuel. Lus et écrits désormais
  comme LibreOffice.

La relecture de la régénération paresseuse a aussi trouvé un chemin d'écriture sans drapeau :
`add_condition` sur un format de nombre de `styles.xml` (celui d'un style de cellule nommé, le
cas le plus courant dans un document LibreOffice) écrivait dans un arbre que `save()` recopiait,
donc perdait la condition en silence — déjà vrai sur `master`, où `styles.xml` était toujours
recopié. L'écriture pose maintenant le drapeau (`_touched_tag`), et chaque chemin pouvant
atteindre une partie recopiée a un test qui échoue si on retire son drapeau.

Conséquence pour la mise en page et les volets figés : plus rien ne retient la main sur la
mécanique. Ce qui reste est le modèle et l'API de chacune des deux fonctionnalités.

## Vers 1.0 — se passer de BeautifulSoup, lxml seul (décidé le 2026-10-01)

Décision prise : on y va, et la rupture d'API est acceptée. Les attributs qui exposent des
objets bs4 (`reader.data`, `styles_data`, `meta_data`, `settings_data`, `cell.cell`) sont des
propriétés obscures, peu utilisées, et le paquet n'a pas d'utilisateurs à ménager. À faire à la
**1.0**, annoncée comme telle, pas au fil d'une 0.14.x.

### Ce que ça rapporte, mesuré

Sur un `content.xml` réel de 185 Ko (`tests/wild/libreoffice26_linux_streets.ods`) :

| | temps d'analyse | pic mémoire |
| --- | --- | --- |
| BeautifulSoup (`"xml"`, donc lxml dessous) | 0,10 s | 35,2 Mo |
| lxml seul | 0,01 s | 23,9 Mo |

Environ ×10 sur l'analyse, et un écart mémoire qui s'ouvre avec la taille : c'est la limite que
DOCS.md documente déjà (~4,5 Ko par cellule, ~2 Go de pic à 100 000 × 5) — elle est largement le
surcoût des objets bs4, un élément lxml étant une structure C de quelques dizaines d'octets.

Et surtout, **la fidélité devient acquise par construction** : lxml préserve l'ordre des
attributs, les échappements et les blancs (vérifié). Plus de perte de rembourrage des
`<number:text>`, plus d'arbitrage autour de `preserve_whitespace_tags`, et régénérer
`styles.xml` redevient sûr — donc mise en page et volets figés deviennent atteignables.

### Ce que ça coûte

Réécriture du cœur, pas un échange de bibliothèque : **~490 points d'appel** dans 7 015 lignes de
`src` — `.attrs` 181, annotations `Tag` 121, `find`/`find_all` 83, plus `decompose`,
`insert_before`/`insert_after`, `get_text`, `.string`, `deepcopy`. Les sélecteurs changent de
nature : bs4 accepte le nom préfixé (`"table:table-cell"`), lxml veut `{URI}local` avec une carte
de namespaces — plus correct au sens de XML, mais chaque appel est touché.

### Le filet qui rend l'opération raisonnable

Les ~1 030 tests, la suite de cohérence LibreOffice, et le balayage de 500 fichiers réels
(`benchmarks/sweep_real_files.py` : invariant grille ↔ XML, écriture-relecture). C'est exactement
ce qu'exige une réécriture de cœur, et c'est déjà en place.

### Ordre à tenir

1. ~~D'abord le correctif des blancs~~ — fait, publié en 0.14.2 avec la régénération
   paresseuse (voir « Le blanc est conservé » ci-dessus). La migration part de là : lxml seul
   doit garder au moins cette fidélité, et les tests qui l'épinglent la vérifient.
2. **Puis l'étude de faisabilité** : inventaire des points d'appel par famille, prototype de la
   couche d'accès (nommage `{URI}local`, carte de namespaces, clonage, insertion), mesure sur
   1 000 / 10 000 / 100 000 lignes contre la section 12 de DOCS.md.
3. **Puis la migration**, en gardant l'API publique haute (`ODSReader`, `Sheet`, `Cell`,
   `CellStyle`…) inchangée. Seules les échappatoires changent de type : des éléments lxml, dit
   dans le CHANGELOG et dans DOCS.md. Ne pas reconstruire une façade façon bs4 — ce serait
   réécrire bs4.
