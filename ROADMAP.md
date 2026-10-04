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

### Pour démarrer (état au 2026-10-04)

**Où en est-on.** La 0.15.0 est publiée : blancs conservés (0.14.2), `<text:s/>` lu et écrit
(0.14.3), fichiers chiffrés refusés et éditions de feuille linéaires, bs4 ≥ 4.13 (0.15.0). Depuis,
sur `master` : la couche d'accès lxml, `src/odsslicer/xmltree.py` et ses 84 tests
(`tests/test_xmltree.py`), que rien n'utilise encore ; le code formaté par ruff, vérifié par la CI.
L'étude est sur la branche `claude/lxml-study`, poussée sur GitHub, rebasée sur ce `master` :
`properties.py` porté avec `meta.xml` — l'étape 0 du plan ci-dessous —, l'étude
(`benchmarks/LXML_STUDY.md`) et ses scripts.

**Lire, dans l'ordre** : cette section ; « Ce que l'étude a établi », « Plan d'engagement » et
« À décider » plus bas ; puis `benchmarks/LXML_STUDY.md` (en anglais), dont la section 2, les
pièges, avant d'écrire la moindre ligne ; enfin `xmltree.py` et ses tests, qui sont la spécification
exécutable de chaque piège.

**Où vit le travail — décidé le 2026-10-04.** Les étapes 2 à 4 ne cassent rien : elles se font
sur `master` et sortent en 0.15.x, l'étape 2 écrivant ses fonctions bs4 sous les noms de
`xmltree` et contre ses tests de parité. Les étapes 0, 1, 5 et 6 changent le type d'une
échappatoire et attendent la 1.0 : elles vivent sur `claude/lxml-study`, la branche de la 1.0,
rebasée sur `master` après chaque 0.15.x.

Les décisions qui restent attendent leur étape : `Cell.attrs`/`Sheet.attrs` et les clés de propriétés à l'étape
4 ; `of:` non déclaré au portage de `formulas.py` (étape 5) ; analyse stricte (recommandée, par
l'étude comme par la session qui l'a relue) et plancher de lxml à la 1.0.

**Le formatage est fait** (2026-10-04, `f33f352`, sans changer un seul arbre syntaxique) et la
CI le vérifie : formater chaque commit avant de le pousser (`ruff format src tests benchmarks`,
ruff 0.16.x épinglé), sinon le job `lint` échoue. `.git-blame-ignore-revs` fait sauter ce commit
à `git blame`.

**Par où commencer** : l'étape 2 sur `master`, module `xmlutils` d'abord (création d'éléments :
57 sites, `_blank_template`, `_new_qualified_tag`, `EMPTY_CELL_BS`, `_TAG_FACTORY` → `new`). C'est
publiable, le balayage la vérifie, et c'est là que se règlent une fois pour toutes les pièges du
`tail`. L'étape 1 (`settings.xml`, graphiques) se fait sur la branche 1.0, en parallèle ou après.

**À ne pas oublier en route** :

- **Jamais d'insertion par indice en lxml** : `insert(i)` y parcourt la liste. Les sites hérités
  des correctifs de la 0.15.0 sont tous dans `sheet.py` — `_position_after` et ses trois
  appelants (`_take_back_rows`, `insert_rows`, `insert_columns`), et `_split_repetitions`
  (`replace_with(*copies)`) — et deviennent à l'étape 5 des `insert_after` successifs.
- **Enfants ou descendants** : 47 recherches comptent sur le défaut récursif de bs4 ; c'est
  l'étape 3, et c'est là que se cachent les bogues (la note lue comme valeur).
- **Le `tail`** : deux sites travaillent dans du texte mixte, tous deux dans `hyperlink`
  (`cell.py`), et `set_paragraph_text` doit mettre le texte suivant `<text:s/>` dans son `tail`.

**Vérifier chaque étape** — dans un venv propre au worktree, jamais dans `~/Envs/main` :
`pytest` (suite LibreOffice comprise), `mypy` (avec l'extra `typecheck` : bs4 4.13 a ses
propres types, mais sans `types-beautifulsoup4` mypy relève 53 erreurs — le garder jusqu'au départ
de bs4), `ruff check` et `ruff format --check` sur `src tests benchmarks`, puis
`benchmarks/sweep_real_files.py --baseline v0.15.0 --limit 250 --write-limit 150` (7 minutes
pour 250 fichiers ; 500 dépassent 30 minutes : le lancer en arrière-plan avec un délai long).
Toute différence de lecture se tranche contre LibreOffice (`soffice --headless --convert-to
csv`), jamais contre odsslicer, et le balayage n'imprime jamais le contenu d'une cellule. Deux
sessions qui lancent LibreOffice en même temps font échouer ses tests par intermittence :
relancer seul avant de conclure.

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

> Corrigé par l'étude du 2026-10-03 : lxml préserve l'ordre des attributs et les blancs, **pas
> les échappements**. `&apos;` et `&quot;` ressortent en caractères, `<x></x>` en `<x/>`, comme
> avec bs4 ; seule la forme canonique (C14N) revient identique, sur les 8 fixtures et toutes leurs
> parties. Régénérer `styles.xml` est donc sûr au sens où il l'est déjà avec bs4 depuis la 0.14.2,
> pas davantage, et la recopie à l'octet des parties non touchées reste nécessaire. Le gain de
> fidélité réel est ailleurs : plus de `preserve_whitespace_tags`, et l'ordre des attributs gardé.

### Ce que ça coûte

Réécriture du cœur, pas un échange de bibliothèque : **~490 points d'appel** (estimation du
2026-10-01 ; l'inventaire de l'étude en compte 519, voir plus bas) dans 7 015 lignes de
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
2. ~~**Puis l'étude de faisabilité**~~ — faite le 2026-10-03, sur la 0.14.3 : voir ci-dessous et
   `benchmarks/LXML_STUDY.md` (inventaire, pièges, mesures, en anglais), branche
   `claude/lxml-study`.
3. **Puis la migration**, en gardant l'API publique haute (`ODSReader`, `Sheet`, `Cell`,
   `CellStyle`…) inchangée. Seules les échappatoires changent de type : des éléments lxml, dit
   dans le CHANGELOG et dans DOCS.md. Ne pas reconstruire une façade façon bs4 — ce serait
   réécrire bs4.

### Ce que l'étude a établi (2026-10-03)

Le détail, chiffres et cas qui échouent, est dans `benchmarks/LXML_STUDY.md` ; les scripts qui les
mesurent sont à côté (`lxml_inventory.py`, `lxml_corpus_scan.py`, `lxml_profile_open.py`).

- **Aucune opération sans équivalent lxml raisonnable.** Deux demandent un utilitaire d'une
  vingtaine de lignes, faute de contrepartie dans lxml : `unwrap()` et la recherche paresseuse en
  ordre de document (`previous_elements`/`next_elements`, qui sert à l'inférence du texte
  affiché). Les deux sont écrits et testés contre bs4. La décision tient.
- **L'inventaire** : 519 points d'appel et 132 mentions de type sur la 0.14.3 (la 0.14.3 en a
  ajouté 13). Attributs 238, recherches par nom 82, insertions 62, texte 27, suppressions 27,
  parent et frères 27, noms qualifiés 26, clonage 11, analyse et construction 10, ordre de
  document 7. Par module : `sheet` 167, `cell` 121, `styles` 99, `reader` 73, `properties` 30,
  `xmlutils` 28.
- **Les pièges, chacun montré en échec** dans `tests/test_xmltree.py` : le `tail` (en lxml, le
  texte qui suit un élément lui appartient — `remove`, `deepcopy`, `addnext` le perdent, le
  dupliquent ou le dépassent ; deux sites seulement travaillent dans du texte mixte, tous deux
  dans `hyperlink`), les préfixes (un fichier qui lie `tab:` au namespace des tableaux : identique
  pour LibreOffice, **zéro feuille** pour odsslicer aujourd'hui ; aucun des 492 fichiers du corpus
  ne le fait), la création d'éléments (`_blank_template` disparaît : un élément créé détaché prend
  le préfixe du document à l'insertion), l'analyse stricte.
- **Les préfixes : la carte ne doit pas venir du document pour les noms.** Les URI sont fixées par
  la spécification et les préfixes du code sont son propre vocabulaire ; une carte tirée du
  document casserait justement le fichier `tab:`, qui n'a pas de clé `table`. Le document compte
  pour les préfixes **dans les valeurs** : `of:=` d'une formule. Le corpus a 5 fichiers dont les
  formules sont en `of:` sans que `of` soit déclaré.
- **Un défaut trouvé en route** : un fichier chiffré s'ouvrait sans erreur avec zéro feuille, et
  `save()` écrivait un `content.xml` vide par-dessus (c'est le `recover=True` de bs4). Corrigé à
  part et publié en 0.15.0 : le manifeste est lu avant toute partie, et un
  paquet chiffré lève `EncryptedDocumentError`.
- **Le chiffre qui décide** : à 100 000 lignes, ouvrir puis charger la feuille, c'est 94 % de bs4
  — l'analyse, la navigation, et le ramasse-miettes que ses millions d'objets Python occupent (un
  tiers de l'ouverture). Le travail propre d'odsslicer pèse 6 %. Mais lxml ne parcourt l'arbre que
  deux fois plus vite que bs4 (un proxy Python par élément atteint) : la projection donne **×5,8**
  (12,2 s → 2,1 s) et **un pic mémoire divisé par deux** (1,9 Go → 0,9 Go), pas ×15. Les ~28 s
  d'ouverture de la décision ne se retrouvent pas : 12 à 15 s ici, comme dans DOCS.md.
- **Un coût de bs4 caché dans les éditions** : `insert_after`, `insert_before`, `extract` et
  `decompose` cherchent la position de l'élément en parcourant tous ses frères. C'est la
  régression quadratique de `grow_to` trouvée à part (246 s → 32 s pour générer 100 000 lignes,
  corrigée en 0.15.0), et `insert_rows` avait la même. En lxml, `addnext` et
  `addprevious` sont en temps constant (8 000 lignes ajoutées l'une après l'autre : 3 ms contre
  377 ms) ; `parent.insert(i, …)` et `parent.index(…)` restent linéaires, donc le portage
  s'interdit l'indice et se place toujours à côté d'un élément connu. Les trois correctifs publiés
  en 0.15.0 (`grow_to`, `insert_rows`/`insert_columns`, `_split_repetitions`, avec bs4 relevé
  à 4.13) cherchent l'indice une fois puis insèrent par
  `parent.insert(position + k, …)` : juste sur bs4, quadratique traduit tel quel en lxml, où
  `insert(i)` parcourt la liste jusqu'à `i` (20 000 copies 50 000 lignes plus bas : 2,6 s par
  indice, 9 ms par `addnext` en chaîne). À l'étape 5, ces sites deviennent des `insert_after`
  successifs.
- **Le prototype** : `xmltree.py` (pas une façade : le code appelle lxml directement, le module ne
  porte que ce qui diffère assez pour coûter un bogue), et `properties.py` porté avec les lignes
  de `reader.py` qui lisent et écrivent `meta.xml`. Coût : 30 points d'appel, 3 lignes de
  `reader.py`, aucun test modifié.
- **Le couplage suit les parties du paquet, pas les modules.** Porter `properties.py` voulait dire
  porter `meta.xml`, que rien d'autre ne lit. `content.xml` et `styles.xml` sont lus par le même
  code — un `Cell` tient un élément de l'un, son `CellStyle` un élément de l'un ou de l'autre,
  `_find_in_styles` cherche dans les deux — : ils basculent ensemble, ou pas du tout.

### Plan d'engagement — six étapes après l'étude

Chaque étape se termine suite verte (pytest, mypy, ruff, suite LibreOffice) et balayage à zéro
contre la dernière version publiée, sauf différence annoncée. Les étapes 2 à 4 ne cassent rien et
peuvent sortir en 0.15.x ; 0 et 1 changent le type d'une échappatoire (`meta_data`,
`settings_data`) et attendent la 1.0, que publie l'étape 5.

0. ~~**Couche d'accès et `meta.xml`**~~ — fait sur la branche de l'étude. Attention : `meta_data`
   y devient un arbre lxml, donc la branche ne doit pas partir dans une 0.14.x telle quelle (voir
   « À décider »).
1. **`settings.xml` et le contenu des graphiques.** Deux parties que seul `reader.py` lit
   (`settings_data`, `_charts`, la réécriture de leurs plages) : petit, et le premier usage de la
   couche sur un chemin de `save()` qui recopie ou régénère. `_touched_tag` apprend à remonter à
   la racine d'un arbre lxml (`getroottree()`). Rupture : `settings_data`, à garder pour la 1.0.
2. **Préparation sur bs4, sans changer de bibliothèque.** Faire passer par des fonctions à corps
   bs4, aux noms et à la sémantique de `xmltree`, tout ce qui diffère entre les deux : création
   d'éléments (`_blank_template`, `_new_qualified_tag`, `EMPTY_CELL_BS`, `_TAG_FACTORY` → `new`,
   57 sites), texte (`text`, `set_text`, les deux fonctions de paragraphe), suppression, clonage,
   insertion avant ou après, remplacement, désenrobage (59 sites), comparaison de nom (`.name`,
   `.prefix` → 26 sites). Module par module : `xmlutils`, `styles`, `cell`, `sheet`, `reader`.
   Comportement inchangé, balayage à zéro : c'est ce qui le garantit. Les pièges du `tail` se
   règlent alors une fois, dans des fonctions testées des deux côtés, au lieu d'une fois par site
   dans la bascule.
3. **Enfants ou descendants, site par site.** Les 47 recherches qui comptent sur le défaut
   récursif de `find_all` disent ce qu'elles veulent (`recursive=False` là où ce sont des
   enfants). Étape à part parce qu'elle peut changer un comportement — elle corrige des bogues du
   type de la note lue comme valeur — et que chaque différence au balayage doit pouvoir lui être
   imputée.
4. **Décisions d'API, écrites dans DOCS.md avant le code** : ce que deviennent `Cell.attrs`,
   `Sheet.attrs`, les clés de `cell_properties`/`text_properties` (voir « À décider »), et le
   message d'erreur d'un fichier mal formé (un fichier chiffré a déjà le sien).
5. **La bascule : `content.xml` et `styles.xml` sur lxml, ensemble — 1.0.** Les fonctions de
   l'étape 2 prennent les corps de `xmltree` ; reste le mécanique : 238 accès aux attributs
   (`el.get(qn(...))`, constantes précalculées sur le chemin chaud), 82 recherches, 27
   navigations, 132 annotations. bs4 quitte les dépendances ; le `xfail` du fichier `tab:` saute.
   **C'est l'étape la plus risquée** : les éditions de structure de `sheet.py` (découpe des
   répétitions, `_unrepeat_*`, `_take_back_rows`, 22 navigations vers le parent ou les frères,
   dans des groupes de lignes), l'alias `Cell.attrs` écrit à travers lui-même, `_touched_tag`, et
   la vitesse de `Sheet.load`, à remesurer contre la projection. Balayage attendu à zéro, hors
   fichiers aux préfixes non standard (qui se lisent) ; le corpus n'en a pas.
6. **Le chemin chaud et la mesure.** `Cell.__init__` et `Sheet.load` en appels lxml natifs, banc
   complet contre la section 12 de DOCS.md, CHANGELOG et DOCS de la 1.0 (les échappatoires
   devenues lxml, nommées une à une).

Pourquoi cet ordre : du plus isolé au plus couplé, et pour que chaque différence de balayage ait
une seule cause possible. Les étapes 1 et 2 ne changent rien à ce qu'on lit ou écrit, et le
balayage le prouve ; la 3 change peut-être quelque chose, et seulement ce qu'elle vise ; la 5
change de bibliothèque sans changer de logique, puisque toute la logique délicate a déjà été
déplacée et testée à l'étape 2. La bascule elle-même ne peut pas être découpée : tant que
`content.xml` et `styles.xml` partagent leur code, il n'y a pas d'état intermédiaire où l'un est
en lxml et l'autre en bs4.

Ruptures d'API à annoncer à la 1.0 : `reader.data`, `styles_data`, `meta_data`, `settings_data`,
`reader.tables`, `sheet.table`, `cell.cell` deviennent des éléments ou arbres lxml ; `Cell.attrs`
et `Sheet.attrs` selon la décision ci-dessous ; `export_content_xml(pretty=True)` sort
l'indentation de lxml. Changements de comportement : un fichier mal formé lève au lieu de s'ouvrir
réparé en silence ; un fichier aux préfixes non standard se lit.

### À décider

- **Fusionner le prototype quand ?** `meta_data` y devient lxml : sur `master` avant la 1.0, ce
  serait une rupture dans une 0.x. Rebasée le 2026-10-04 sur la v0.15.0 (refus des
  fichiers chiffrés, éditions de feuille linéaires), la branche balaye à zéro partout contre lui
  (250 fichiers, écriture sur 150) : le fichier chiffré lève la même erreur des deux côtés,
  avant que l'analyse stricte n'intervienne. Soit la branche attend l'étape 5, soit on la
  fusionne sans le portage de `properties.py` (la couche et ses tests seuls ne cassent rien).
- **`Cell.attrs` et `Sheet.attrs`** : les retirer, exposer `el.attrib` (clés `{URI}local`), ou
  garder une vue en lecture aux clés préfixées (`prefixed()` existe). DOCS.md en montre l'usage.
  Même question pour les clés de `cell_properties` et `text_properties` ; je garderais le préfixe
  de la spécification, qui ne dépend pas du fichier.
- **Analyse stricte** : recommandée. Le corpus ne contient aucun XML que `recover=True` aurait
  dû réparer, et le chiffrement est désormais détecté par le manifeste, avant l'analyse : la
  stricte ne garde plus que les parties tronquées ou mal formées, que bs4 complète sans rien
  dire.
- **`of:` non déclaré** : à l'écriture d'une formule, déclarer `of` sur la racine s'il manque, ou
  écrire avec le préfixe que le document lie à OpenFormula (`prefix_of()`) — 5 fichiers réels sont
  concernés.
- **Plancher de lxml** : `lxml>=4.6` aujourd'hui. Les roues des Python récents imposent de fait
  une 5.x ; le relever à la 1.0 ne coûte rien.

