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

### Pour démarrer (état au 2026-10-18)

**Où en est-on : la 1.0 est prête à publier.** Les six étapes du plan sont faites, sur
`claude/lxml-study`, la branche de la 1.0, et l'audit avant la 1.0 (section du même nom, plus
bas) aussi, par parties : **A, l'API publique**, **B1, les styles sans état double**, et **B2–B5,
le découpage de ce qui était trop long**, sont dans `claude/lxml-study` ; **les trois ruptures
que B2–B5 proposaient et la partie C, les conventions**, sont faites sur `claude/audit-c`, à
relire et fusionner. Il ne reste que des décisions, ci-dessous, et la migration des projets de
l'auteur.

**Ce qui reste à décider pour publier** :

- **La fusion et le tag** : les parties de l'audit dans `claude/lxml-study`, puis la branche
  rebasée sur `master` (depuis v0.16.1, un seul commit, de test, déjà dans la branche) et
  fusionnée ; le tag `v1.0.0` et la release GitHub, que `publish.yml` envoie sur PyPI. La version vient du tag (setuptools-scm),
  rien à éditer ; seul le titre « [Unreleased] » du CHANGELOG devient « [1.0.0] — date ».
- **Le classificateur** `Development Status :: 4 - Beta` : passer à `5 - Production/Stable`
  avec la 1.0, ou non.
- **Python 3.14** : toutes les mesures de l'étape 6 sont faites sous 3.14, que ni la matrice de
  la CI ni les classificateurs ne nomment (3.10 à 3.13).
- **Les dépendants** : plus aucun code de `pylocalmodules` n'utilise une échappatoire de la 0.x
  (`attrs`, une recherche bs4, `EMPTY_CELL_BS`), sauf trois scripts de reproduction archivés
  dans `promotion/fiches/bancs/odsslicer/corrections-2026-09-27/` ; mais l'audit, partie A, casse
  compta, devoir_utils, copiedouble et sncf-ods (la liste est dans « Audit avant la 1.0 »). Les
  migrer, puis, avant de monter `~/Envs/main` en 1.0, relancer leurs suites contre elle
  (`uv run --frozen --with 'odsslicer==1.0.0'`).
- **Les projets de l'auteur, après la publication** : compta, copiedouble, devoir_utils et
  sncf-ods, chacun à migrer de son côté, puis à verrouiller sur la 1.0.0 — les ruptures qui
  touchent chacun sont dans « Audit avant la 1.0 », partie A, et sncf-ods appelle aussi trois
  fois `NumberFormat.create` (`src/sncf_ods/classeur.py`, `_format`), à écrire
  `NumberFormat.create(lecteur, NumberFormatSpec(...))`. Aucun n'assigne `sheet.name` ni ne
  passe les champs d'un tableau croisé par position.

**Ce qui vient après la 1.0** : les autres pistes de cette feuille de route, aucune n'attendant
plus la bascule — les plages nommées et les références 3D dans la syntaxe des formules
(« Formules ») ; les séparateurs des nombres selon la locale, faire suivre les autres plages
(tableaux croisés, plages nommées, mises en forme conditionnelles) aux éditions de structure,
le texte enrichi partiel, puis les non-couverts (validation, filtres, volets figés, protection,
plans), avec la contrainte qu'impose leur régénération (« Relecture de DOCS.md du
2026-10-01 », et « La contrainte à respecter si l'on régénère `styles.xml` et `settings.xml` ») ; la réécriture de `Sheet.load` avec des tests dédiés
(« Vers 1.0 — consolidation », point 1), qui a gagné à l'étape 6 un filet de plus, et à l'audit
(B2) des étapes nommées, `loading._GridLoader`. Et la mémoire,
devenue la seule limite : un `Cell` par position (environ 1,7 Ko par cellule, ouverture et
chargement compris).

**Lire, dans l'ordre** (pour reprendre la migration elle-même) : cette section ; « Plan
d'engagement » plus bas, les étapes 5 et 6 surtout ; DOCS.md, section 15 ; puis `xmltree.py` et
`tests/test_xmltree.py`, qui sont la spécification exécutable de chaque piège.
`benchmarks/LXML_STUDY.md` (en anglais) décrit le code d'avant la bascule.

**Où vit le travail — décidé le 2026-10-04.** Les étapes 0, 1, 5 et 6 changent le type d'une
échappatoire et attendent la 1.0 : elles vivent sur `claude/lxml-study`, la branche de la 1.0,
rebasée sur `master` après chaque 0.x.

**Le formatage** est vérifié par la CI : formater chaque commit avant de le pousser (`ruff format
src tests benchmarks`, ruff 0.16.x épinglé), sinon le job `lint` échoue.

**À ne pas oublier en route** :

- **Jamais d'insertion par indice en lxml** : `insert(i)` et `index()` y parcourent la liste. Tout
  `sheet.py` place désormais à côté d'un élément connu (`_insert_chain`, `insert_before`) ; des
  tests l'enregistrent (la fixture `positional`, dans `tests/conftest.py`, et ses tests dans
  `test_growth.py`, `test_structure.py`, `test_unrolling.py`, `test_columns.py`), quatre
  mutations le vérifient.
- **Un élément sans enfant est faux en lxml** : jamais `a or b` entre deux éléments, toujours
  `is None` (`xmltree.child` le rappelle). Trois sites l'avaient (`NumberFormat`,
  `_find_in_styles`, `_new_text_paragraph`), les deux premiers gardés par une mutation ; la suite
  tourne sans `FutureWarning` (`pytest -W error::FutureWarning`), que lxml émet à chaque test de
  vérité d'un élément.
- **Un élément retiré reste lisible** en lxml, là où bs4 le vidait : `_drawings` écarte ceux qui
  ne remontent plus à la racine de `content.xml` (`_root_of`).

**Vérifier chaque étape** — dans un venv propre au worktree, jamais dans `~/Envs/main` :
`pytest` (suite LibreOffice comprise, et `-W error::FutureWarning`), `mypy` (avec l'extra
`typecheck`), `ruff check` et `ruff format --check` sur `src tests benchmarks`,
`benchmarks/mutate_layers.py` et `benchmarks/mutate_flags.py` (chaque garde-fou cassé doit faire
échouer un test), puis `benchmarks/sweep_real_files.py --baseline-src <src d'un worktree détaché
sur la branche de départ> --limit 250 --write-limit 150` (7 minutes pour 250 fichiers au calme,
bien plus sous charge ; 500 dépassent 30 minutes : le lancer en arrière-plan avec un délai long).
Le balayage n'édite que des valeurs : ce qu'une édition de structure réécrit (les graphiques)
se compare partie par partie avec `benchmarks/edit_parts.py --baseline-src ... --timeout 300`,
sur le corpus public de LibreOffice (`promotion/corpus/libreoffice-core`, 614 fichiers : une heure
sous charge ; `too-many-cols-rows.ods` y retient chaque version plus d'une demi-heure, d'où le
délai) et sur les vrais fichiers qui ont des objets.
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

0. ~~**Couche d'accès et `meta.xml`**~~ — fait : la couche sur `master`, le portage de
   `meta.xml` sur `claude/lxml-study`, où `meta_data` devient un arbre lxml ; il attend la 1.0.
1. ~~**`settings.xml` et le contenu des graphiques.**~~ **Fait le 2026-10-17** (branche
   `claude/lxml-step1`, partie de `claude/lxml-study` : le portage, les deux scripts de
   vérification, la documentation). Rupture : `settings_data`, gardée pour la 1.0.

   **Ce qui passe par lxml** : `settings_data`, un `etree._ElementTree` analysé au premier accès
   et sérialisé par `xmltree.serialize`, sous la déclaration XML lue, seulement une fois touché ;
   le contenu propre de chaque graphique (`Object N/content.xml`), lu par `_charts`, ses plages
   réécrites par `_remap_drawing_references` et `_rename_drawing_references` par URI
   (`_CHART_RANGE_ATTRS`, précalculés par `qn`). `_parts` garde l'arbre et les octets lus, pour
   la déclaration. **Reste en bs4** : `<draw:object>` et `table:end-cell-address`, qui sont dans
   `content.xml`, et `styles.xml` — l'étape 5. `_touched_tag` reconnaît les deux bibliothèques.
   `MalformedDocumentError` existe (sous-classe de `ValueError`, exportée) : `meta.xml` lève à
   l'ouverture, `settings.xml` au premier accès, un graphique à la première édition de
   structure, **avant** qu'elle change quoi que ce soit (`ODSReader._read_objects`, en tête de
   `insert_rows`, `insert_columns`, `delete_rows`, `delete_column`, `rename_sheet`).

   **Ce que ça a coûté** : une session ; `reader.py` et `sheet.py` +163 −46 lignes ; 13 tests
   ajoutés (17 cas), les 4 qui lisaient `settings_data` en bs4 réécrits ; deux scripts,
   `benchmarks/edit_parts.py` (une édition de structure faite par deux versions, comparée
   partie par partie : mêmes octets, même forme canonique, ou une différence) et
   `benchmarks/mutate_flags.py` (16 garde-fous de `save()` cassés un à un, chacun attrapé).
   `mutate_layers.py` attrape toujours ses 39 mutations.

   **Mesuré**, contre `claude/lxml-study` : aucune fixture n'a de graphique ; sur les 8,
   `settings.xml` touché revient en même forme canonique (6, plus 2 créés pour Excel), le reste à
   l'octet. Corpus public de LibreOffice, 86 fichiers à objets : 507 éditions, 775 graphiques
   réécrits, tous en même forme canonique (octets autres : `&apos;`, `<x/>`, déclaration du
   fichier là où bs4 écrivait `encoding="utf-8"`), zéro différence. Vrais fichiers de l'auteur à
   objets, 147 : 872 éditions, 2 549 graphiques réécrits, zéro différence. Balayage de 250 vrais
   fichiers (écriture sur 150) : zéro partout — lecture, grille, écriture-relecture, parties
   recopiées, erreurs nouvelles.

   **Ce qu'elle apprend pour l'étape 5** :

   - **`getroottree()` ne dit pas l'arbre d'un élément détaché** : un élément retiré de son arbre
     reste dans le même document lxml, et `getroottree().getroot()` rend encore la racine de ce
     document. `_touched_tag` remonte donc par `getparent()`, comme `parent` en bs4 (piège épinglé
     dans `tests/test_xmltree.py`). À la bascule, sa branche bs4 disparaît.
   - **Le cache de `_drawings` est fragile.** Rempli à la première édition de structure, après
     elle ; lire les graphiques avant l'édition par lui l'aurait rempli d'éléments que l'édition
     jette, d'où un `TypeError` (attrapé sur `ser_labels.ods`, d'où `_read_objects` lit les
     objets dans la liste du paquet). Il cachait **deux défauts présents depuis la 0.14**, corrigés à
     part sur `master` le 2026-10-17 (`62ea419`, contre LibreOffice) : (a) supprimer une ligne qui porte un dessin ancré au-delà
     des colonnes de la grille supprime le dessin avec elle (`_rescue_drawings` ne parcourt que
     les colonnes de la grille ; `ser_labels.ods` de LibreOffice : un graphique en F2, la grille
     en A:E), contrairement à l'issue #24 ; (b) deux éditions de structure de suite, dont la
     seconde jette un dessin mis en cache par la première, lèvent `TypeError`
     (`insert_columns(5)` puis `delete_rows([1])` sur le même fichier). Corrigés : la
     suppression de lignes parcourt aussi les cellules au-delà de la grille
     (`_cell_starting_at`), et `_drawings` écarte ce que bs4 a vidé. À la bascule, lxml ne vide
     rien : un élément retiré reste lisible, et ce tri devient inutile.
   - **`serialize` veut les octets lus** pour la déclaration : `self.content` et `self.styles`
     serviront à l'étape 5, comme `self.settings` ici.
   - **Préfixes** : bs4 ne reconnaissait même pas comme graphique une partie qui lie `office:`
     autrement, et laissait ses plages telles quelles ; par URI, elle suit. `content.xml` aura le
     même gain (le `xfail` du fichier `tab:`).
   - **L'analyse stricte d'une partie lue à la demande** décide où l'erreur sort ; pour
     `content.xml` et `styles.xml`, c'est l'ouverture, sans question.

   **Laissé à décider** : un objet intégré qui n'est pas un graphique mais est mal formé (une
   formule) refuse aussi l'édition de structure, comme une partie orpheline (qu'aucun
   `<draw:object>` ne nomme), puisque `_read_objects` lit tout `*/content.xml` ; et un graphique
   est réécrit à chaque édition de structure même si aucune de ses plages ne bouge, comme en
   0.16.0 — ne le toucher que si une valeur change le laisserait recopié à l'octet.
2. ~~**Préparation sur bs4, sans changer de bibliothèque.**~~ **Fait le 2026-10-17** (branche
   `claude/lxml-step2`, un commit par module : `xmlutils`, `styles`, `cell`, `sheet`, `reader`,
   puis `discard`).
   `src/odsslicer/xmlbs.py` donne un corps bs4 à 17 opérations sous le nom et la sémantique de
   leur homologue de `xmltree` : `new`, `text`, `set_text`, `paragraph_text`,
   `set_paragraph_text`, `remove`, `discard`, `clone`, `insert_after`, `insert_before`, `unwrap`,
   `wrap_contents`, `children`, `local_name`, `name_of`, `namespace_of`, `rename`. À la bascule,
   chaque module change sa ligne `from .xmlbs import ...` en `from .xmltree import ...`, et
   `xmlbs.py` disparaît. `xmltree` y a gagné les sept qui lui manquaient (`insert_before`,
   `discard`, `wrap_contents`, les trois noms, `rename`) ; il n'y a pas de `replace` : son seul
   site, `_split_repetitions`, reste tel quel (voir l'étape 5).

   Ce que ça a coûté : une session, ~310 lignes pour `xmlbs`, 80 de plus pour `xmltree`, et
   aucune ligne de comportement. Passés : 31 créations (`cell` 6, `sheet` 6, `styles` 10,
   `reader` 6, `xmlutils` 2, `constants` 1) et ~95 sites de l'inventaire (`lxml_inventory.py` :
   texte 16, clonage 6, insertions avant ou après 18, suppressions 21, noms 28, construction 5),
   plus les appels aux fonctions de paragraphe et à `_children_named`. L'inventaire passe de 520
   à 454 points d'appel, dont une quarantaine dans `xmlbs` même. Trois choix, chacun pour que
   rien ne change :

   - **`new(name, near=...)`** : bs4 ne sait pas si le document lie l'espace de noms d'un élément
     neuf ; lxml le décide à l'insertion. `near`, un élément du document, reprend la règle de
     `_blank_template` : l'élément déclare son espace de noms (`xmlns:text="..."`) quand le
     document n'a encore aucun élément de ce nom, rien sinon — octet pour octet ce qui s'écrivait
     (testé contre l'ancienne copie sur les huit fixtures). `xmltree.new` accepte `near` et
     l'ignore. À la bascule, la déclaration redondante de ces cas-là disparaît : différence
     d'octets, pas de forme canonique.
   - **`remove` et `discard`** : `extract()` pour un élément qu'on replace ailleurs,
     `decompose()` pour un élément qu'on jette, comme avant — bs4 libère ainsi le sous-arbre sans
     attendre le ramasse-miettes, et un `Cell` d'une feuille supprimée le voit vidé, comme avant.
     En lxml les deux sont `remove`.
   - **`children` rend une liste, des deux côtés** : en générateur, la version bs4 ralentissait
     `Sheet.load` de 11 % (9,7 → 10,8 ms sur `NotesReference.ods`), le chemin chaud testant le
     résultat une fois par cellule.

   Mesuré à la fin, contre la 0.15.1, en alternance : `NotesReference.ods` ouvert et ses trois
   feuilles chargées, médiane 48,8 ms contre 48,9 ; `Sheet.load` seul, 9,7 ms des deux côtés ;
   `bench.py` à 10 000 lignes, ouverture 614 contre 624 ms, chargement 311 contre 307, le reste
   dans le bruit, sauf la génération (2,1 s contre 2,7 s) et la copie d'un bloc (378 contre
   445 ms), plus rapides : `new` construit l'élément au lieu de copier puis vider un modèle. Le pic
   mémoire varie de 277 à 290 Mo d'une exécution à l'autre, des deux côtés. Balayage contre la
   0.15.1 : 250 fichiers, 617 feuilles, 147 écrits et relus, zéro partout (lecture, grille,
   écriture-relecture, parties recopiées, erreurs nouvelles).

   Les noms se comparent par nom local (`local_name(child) == "table-row"`), comme avant ; le
   seul passage à l'URI est `_drawings_in` (`namespace_of`), où le préfixe du fichier décidait.
   `EMPTY_CELL_BS` est `new("table:table-cell")`, un élément détaché plutôt qu'un document à lui
   seul : il se lit pareil, seul `cell.cell` d'une position hors de la feuille change de type.

   Le filet : `tests/test_xmlbs.py` (163 cas, chacun passé par les deux couches et comparé à ce
   que faisait le code bs4), `benchmarks/mutate_layers.py`, qui casse un à un les 39 garde-fous
   des deux couches — chacun fait échouer un test —, et `benchmarks/time_open.py`, qui chronomètre
   l'ouverture de vrais fichiers contre une autre extraction, en alternance.

   Reste hors de `xmlbs`, volontairement : `properties.py` (porté sur la branche 1.0), les
   `append` (même nom, même sens dans les deux bibliothèques), les insertions par indice de
   `sheet.py`, les recherches (étapes 3 et 5) et l'analyse et la sérialisation (`encode`,
   `prettify`, étape 5).
3. ~~**Enfants ou descendants, site par site.**~~ **Fait** (2026-10-17, branche
   `claude/lxml-step3`). Chaque recherche dit maintenant ce qu'elle veut : `recursive=False` (ou
   `children`) là où ce sont des enfants, un commentaire là où les descendants sont voulus.

   **Mesuré d'abord** : `benchmarks/children_scan.py` compare, avec lxml seul, « les enfants » et
   « tout ce qui est dessous » pour chaque structure en jeu. Sur **500 vrais fichiers, les deux
   s'accordent partout** ; sur un fichier fabriqué qui porte chaque cas, le script les voit tous.
   Les corrections ne changent donc rien sur le corpus, et le balayage contre la 0.15.1 est à zéro.
   Sur le fichier fabriqué, LibreOffice tranche :

   - *Corrigés, contre LibreOffice* : un tableau logé dans le cadre d'une cellule (une zone de
     texte) comptait pour une feuille, et ses cellules pour des colonnes de la ligne ; LibreOffice
     ne montre ni l'un ni l'autre. Les feuilles sont désormais les `table:table` enfants de
     `office:spreadsheet` (`ODSReader._spreadsheet`), les lignes s'atteignent par les conteneurs de
     lignes sans entrer dans une cellule (`sheet._sheet_rows`, que `_rows_past_grid` partage), les
     cellules sont les enfants de leur ligne (`sheet._row_cells`, sept sites).
   - *La note en liste à puces* : LibreOffice en montre les éléments comme son texte, donc sa
     lecture garde les descendants. Réécrire son texte laissait la liste vide derrière, et lui
     donner un auteur le plaçait dans la liste : les deux visent maintenant les enfants qui portent
     le texte, paragraphes et listes (`cell._NOTE_TEXT`).
   - *Enfants, sans changement* : les propriétés d'un style (`styles.py`, 11 sites,
     `_ensure_style_child`, `_fork_style`), l'auteur et la date d'une note, les styles
     automatiques et le corps du document (enfants de la racine, `ODSReader._content_root`), les
     tableaux croisés du classeur.
   - *Descendants, voulus et commentés* : les styles de `styles.xml` (`_find_in_styles`, le
     style par défaut), les dessins et leurs plages (`_drawings`, `_charts`,
     `_remap_drawing_references`, `_rename_drawing_references`), les noms de styles du document
     (`_new_style_name`), un groupe de définitions de colonnes qui n'en contient qu'un autre
     (`delete_column`). À l'étape 5, ce sont des `descendants`.

4. ~~**Décisions d'API, écrites dans DOCS.md avant le code**~~ **Fait** (2026-10-17, décisions de
   l'auteur), écrites dans DOCS.md, section 15 « What changes in 1.0 » :
   - **`Cell.attrs` et `Sheet.attrs` disparaissent à la 1.0.** Leur seul usage réel (4 lignes de
     devoir_utils, toujours `cell.attrs.get("table:style-name")`) a sa propriété dès maintenant :
     `Cell.style_name`. Dépréciés dans le CHANGELOG, sans avertissement à l'exécution : le code
     interne lit `Cell.attrs` à chaque pas, et l'en sortir est le travail de l'étape 5. Les
     usages de devoir_utils sont à migrer une fois `style_name` publié (sa session, à prévenir).
   - **Les clés de `cell_properties` et `text_properties` gardent le préfixe de la
     spécification** (`"fo:background-color"`), quel que soit celui du fichier.
   - **Analyse stricte** : un XML mal formé lève `MalformedDocumentError` (sous-classe de
     `ValueError`, comme `EncryptedDocumentError`).
   - **`of:` non déclaré** : écrire avec le préfixe que le document lie à OpenFormula
     (`prefix_of()`), sinon déclarer `of` sur la racine.
   - **lxml ≥ 5.0**, bs4 retiré des dépendances.
   - Contrats de l'étape 2 : `new(name, near=...)` a perdu `near` à l'étape 5 (validé par l'auteur le
     2026-10-18 : il ne servait qu'à `xmlbs`), `children`
     rend une liste dans les deux couches.
5. ~~**La bascule : `content.xml` et `styles.xml` sur lxml, ensemble — 1.0.**~~ **Faite le
   2026-10-17** (branche `claude/lxml-step5`, partie de `claude/lxml-study`, à fusionner). Chaque
   module importe de `xmltree` ; `content.xml` et `styles.xml` passent par `xmltree.parse`
   (strict) et `xmltree.serialize` ; `xmlbs.py`, `_parse_xml`, `_root_local_name`,
   `_ODF_NAMESPACES`, la branche bs4 de `_touched_tag` et le tri bs4 de `_drawings` ont disparu.
   L'inventaire (`lxml_inventory.py`) ne trouve plus aucun point d'appel bs4 sous `src`, et un
   test vérifie dans un interpréteur neuf que le paquet n'importe jamais bs4.

   **Les décisions de DOCS.md §15, toutes appliquées** : `Cell.attrs` et `Sheet.attrs` retirés ;
   échappatoires en arbres et éléments lxml ; clés de `cell_properties`/`text_properties` au
   préfixe de la spécification (`xmltree.attributes`), celui du fichier pour un espace de noms que
   la spécification ne nomme pas (`officeooo:rsid`), comme avant ; `MalformedDocumentError` à
   l'ouverture pour `content.xml` et `styles.xml` ; le fichier `tab:` se lit (le `xfail` strict est
   devenu trois tests qui passent, lecture des valeurs et écriture comprises) ; une formule prend
   le préfixe que le document lie à OpenFormula (`ODSReader._formula_prefix`), sinon `of` est
   déclaré sur la racine (`_declare_on_root` : lxml ne sait pas ajouter une déclaration à un
   élément existant, la racine est refaite et le contenu déplacé dessous, les objets `Sheet` et
   `Cell` gardant leurs éléments ; vérifié contre LibreOffice, qui calcule la formule).

   **Ce qui n'était pas prévu** : `new(name, near=...)` perd `near`, qui ne servait qu'à `xmlbs` ;
   `new` déclare aussi les espaces de noms de ses attributs (sinon `xlink:href` posé sur un
   élément détaché devient `ns0:href`) ; `xmltree` gagne `child`, `elements`, `attributes` et
   `unset` (les stubs de lxml refusent `attrib.pop(k, None)`) ; `parse` lève la limite de taille
   de libxml2 (`huge_tree`), sans quoi le `tdf136551.ods` de LibreOffice, bien formé, avec un
   attribut de 10,9 Mo, était refusé comme mal formé  ; `EMPTY_CELL_BS`, exporté par
   `odsslicer.classes`, gardé à l'étape 5 puis renommé `EMPTY_CELL` (2026-10-18).

   **Ce que ça a coûté** : une session ; `src` +928 −1 116 lignes (7 961 au lieu de 8 149),
   `tests` +892 −812 (la moitié dans `test_odsslicer.py`, `test_xmlbs.py` versé dans
   `test_xmltree.py`). Tests : 1 298 (1 380 et un `xfail` avant : les 163 cas de
   `test_xmlbs.py` passaient par les deux couches, seul le côté lxml reste) ; ajoutés : le
   préfixe d'OpenFormula (5), les clés de propriétés (2), les nouvelles fonctions de `xmltree`
   (8), le fichier `tab:` lu et écrit (2), `content.xml`/`styles.xml` mal formés (2), un graphique supprimé avec ses cellules qui
   ne suit plus les éditions, l'absence de bs4, les déclarations superflues, le saut de ligne
   dans un attribut, la limite de libxml2. Les tests qui comptaient les `Tag.index` de bs4
   enregistrent maintenant les `index`/`insert` de lxml par une sous-classe d'élément
   (`positional`). Mutations : `mutate_layers.py` en a 30 (les 21 de `xmlbs` et celle de
   `xmltree.discard` sont partis avec eux ; 13 nouvelles : les fonctions ajoutées, les `or`, les
   dessins retirés, les quatre placements par indice), `mutate_flags.py` 15 (celle de la branche
   bs4 de `_touched_tag` partie, une pour `styles.xml` touché ajoutée) : toutes attrapées.

   **Mesuré**, contre `claude/lxml-study`, même machine (Apple M4, charge 2 à 4) :

   - *Balayage* de 250 vrais fichiers (608 feuilles, 148 écrits et relus) : zéro partout —
     lecture, grille, écriture-relecture, parties recopiées, erreurs nouvelles (2 illisibles
     des deux côtés, des zip tronqués).
   - *Fidélité* (`edit_parts.py`) : fixtures, 48 éditions, `content.xml` en même forme canonique,
     tout le reste à l'octet. Corpus de LibreOffice, 614 fichiers, 3 360 éditions : `content.xml`
     en même forme canonique sur 3 338, le reste à l'octet ; 24 erreurs identiques des deux
     côtés ; un fichier hors délai (`too-many-cols-rows.ods`, des deux côtés) ; 22 différences,
     toutes du côté de bs4 — `tdf76310.ods`, `tdf42481.ods`, `tdf100793.ods`, `tdf123479.ods`
     ont un saut de ligne ou un retour chariot écrit `&#xA;`/`&#x0d;` dans un attribut (ou un
     retour chariot dans un texte), que bs4 réécrivait brut, et qu'un analyseur relit en espace :
     la version lxml redonne l'original en forme canonique, bs4 non (`tdf76310` est le rapport de
     LibreOffice sur les formules à sauts de ligne) ; 6 sur `tdf136551.ods`, refusé avant le
     correctif `huge_tree`, à zéro après. Vrais fichiers de l'auteur à objets, 84 : 503 éditions,
     `content.xml` en même forme canonique sur 473, 30 différences sur 5 fichiers, toutes ce même
     saut de ligne dans `office:string-value` que bs4 perdait — la version lxml égale l'original.
     Corrigé, donc, et annoncé dans le CHANGELOG.
   - *Vitesse*, `lxml_profile_open.py --only time`, ouvrir puis charger dans un processus neuf,
     meilleur de trois : 100 000 lignes, **7,35 s → 2,52 s** (×2,9 ; la projection, sur cette
     machine, donne 1,88 à 1,96 s), pic mémoire **1 379 → 835 Mo** (projection 790) ;
     10 000 lignes, 0,60 → 0,25 s (projection 0,18) ; 1 000, 0,06 → 0,03 s. Les chiffres de
     l'étude (12,2 s → 2,1 s, 1,9 → 0,9 Go) ont été pris sur des classeurs plus gros
     (`content.xml` de 75 Mo contre 51 aujourd'hui) et sous une autre charge. L'ouverture seule
     tombe de 5,82 à 0,21 s ; **le chargement monte de 1,45 à 2,30 s** : bs4 avait tout bâti en
     objets Python à l'ouverture, lxml crée un mandataire par élément atteint, et
     `Cell.__init__` (120 000 appels à 10 000 lignes, deux par cellule : `load` construit un
     `Cell` pour décider du contenu, puis celui de la grille) en fait 60 % — l'étape 6.
     `bench.py` (100 000 lignes) : générer et enregistrer 21,3 → 9,1 s, ouvrir 7,5 → 0,2 s,
     charger 1,65 → 2,32 s, `to_numpy` 334 → 71 ms, écrire 1 000 cellules 302 → 35 ms, trier
     395 → 73 ms, copier 1 000 × 2 3,4 → 1,1 s, enregistrer 3,3 → 0,33 s, supprimer des lignes
     inchangé (~0,7 s, la passe sur les formules), pic 2 440 → 1 396 Mo. `NotesReference.ods`
     (`time_open.py`, 3 feuilles chargées, médiane de 9) : 46,8 → 17,0 ms.
   - *Placements*, `bench_insertions.py` à 20 000 lignes, contre bs4 : écrire 20 000 valeurs sous
     les données 1,05 → 0,43 s, insérer 20 000 lignes 0,15 → 0,06 s, insérer 10 colonnes 1,18 →
     0,51 s, écrire dans une série de 20 000 lignes répétées 0,20 → 0,08 s. Les mêmes, placés par
     indice en lxml (les quatre mutations appliquées à une copie) : 8,0 s, 3,3 s, 0,5 s, 8,0 s —
     le piège que l'étude annonçait, évité.

   **Ce qu'elle apprend pour l'étape 6** :

   - **Le chargement est le chemin chaud**, et le seul à avoir reculé : `Cell.__init__` lit cinq
     à huit attributs par `get` (constantes `qn` déjà précalculées) et ses paragraphes ; `load`
     construit deux `Cell` par cellule (`_holds_content(Cell(...))`, puis la grille), ce qu'un
     test du contenu sur l'élément seul éviterait ; `local_name` (`etree.QName`) reste sur
     `_sheet_rows`, `_column_definitions`, `_merge_masters`, `materialize_cell`, à remplacer par
     des comparaisons de `tag` à des constantes (un changement de comportement pour un nom local
     d'un autre espace de noms, à trancher).
   - **DOCS.md, section 12**, est marquée périmée : à remesurer (`bench.py`), avec la comparaison
     aux autres lecteurs (`compare_readers.py`).
   - `xmltree.preceding` construit une liste par sous-arbre de frère ; paresseux sur les frères,
     pas dans chaque sous-arbre — sans effet mesuré ici (`_format_template_candidates` s'arrête au
     premier voisin), à revoir si un profil le montre.
   - `_declare_on_root` refait la racine et déplace tout le document : une passe, payée une fois,
     par un document qui ne lie aucun préfixe à OpenFormula — 5 fichiers sur 492 du corpus.

   **Laissé à décider** : ~~publier la 1.0 après l'étape 6 ou avant, `Sheet.load` étant plus lent
   que la 0.16 pris seul~~ (sans objet depuis l'étape 6, où il ne l'est plus) ;
   et le nom `EMPTY_CELL_BS`, gardé parce qu'exporté par `odsslicer.classes` — tranché le 2026-10-18 :
   renommé `EMPTY_CELL` à la 1.0, `near` retiré pour de bon.

6. ~~**Le chemin chaud et la mesure.**~~ **Fait le 2026-10-18** (branche `claude/lxml-step6`,
   partie de `claude/lxml-study`, à fusionner) : plus aucune opération n'est plus lente qu'en
   0.16.1, chargement seul compris.

   **Le chemin chaud**, profilé (cProfile sur `Sheet.load`, puis chronométré en processus neufs,
   en alternance avec la 0.16.1) ; chargement d'une feuille de 10 000 lignes × 5 :

   - *Les noms par URI* (`43b0340`) : `local_name` (un `etree.QName` par appel) remplacé par des
     comparaisons de `tag` à des constantes `{URI}local` dans `sheet.py` et `Cell.is_covered`
     ; 0,233 → 0,226 s. Décision appliquée : un élément d'un autre espace de noms au même nom
     local n'est plus pris pour une ligne, un groupe, une colonne ou une cellule couverte — ce
     que fait LibreOffice (vérifié par conversion en CSV : il saute un `<x:table-row>`, la 0.16.1
     le lisait comme une ligne). Aucun tel élément dans les 2 112 vrais fichiers de l'auteur ni
     les 611 du corpus de LibreOffice (`benchmarks/namespace_scan.py`). Les formats de nombre,
     laissés au nom local à cette étape, sont passés par URI à l'audit (partie A, point 6).
   - *Un `Cell` par position* (`6a0e1dc`) : `load` en construisait 120 013 pour 50 005 cellules
     (les gardes de largeur, de lignes vides et de colonnes vides, puis la grille).
     `_holds_content` lit désormais l'élément (type de valeur, `office:value`, formule, paragraphe
     ou note), un test le tient égal à `Cell.is_empty` sur 23 formes de cellule ; chaque ligne
     est listée et mesurée une fois (`measure`, que `_normalize_row_width` reçoit) ;
     `_row_cells` filtre par lxml (`iterchildren(*tags)`) ; `Cell.__init__` lit une cellule sans
     enfant ou d'un seul paragraphe sans lister ses enfants (1 324 → 818 ns par cellule).
     0,226 → 0,094 s (0.16.1 : 0,111 s). Mesuré pas à pas : un seul `Cell` 0,237 → 0,136 s,
     paragraphes 0,136 → 0,112, mesure unique 0,112 → 0,096 ; incorporer `_compute_is_empty` n'a
     rien rapporté (laissé tel quel).
   - *La copie des cellules répétées* (`165007f`) : les positions qu'un élément répété couvre
     sont des copies de sa première lecture (`Cell._at`, un `dict` copié), sauf une valeur de
     temps, que le format de sa colonne peut lire en durée. Sans effet sur les classeurs
     synthétiques ; `NotesReference.ods`, trois feuilles chargées : 9,0 → 6,8 ms (0.16.1 :
     7,9), 8 667 de ses 10 626 positions copiées. Un test relit chaque position des fixtures et
     des fichiers sauvages à neuf et compare.
   - *Le tri* (`b99f335`) : seul `sort` à 1 000 lignes restait plus lent (39 ms contre 36) —
     `_style_name` remonte au style par défaut de la colonne en parcourant ses définitions,
     deux fois par cellule, et ce parcours crée des mandataires lxml là où bs4 avait ses objets
     tout faits. Le style par défaut de chaque colonne est lu une fois par tri : 29 ms.
   - Écarté : couper le ramasse-miettes pendant `load` (17 % du chargement à 100 000 lignes),
     un effet de bord global qu'une bibliothèque n'a pas à imposer.

   Chaque garde-fou a sa mutation (`mutate_layers.py` : 11 nouvelles, 41 au total ;
   `mutate_flags.py` 15), toutes attrapées.

   **Mesuré**, Apple M4, Python 3.14, lxml 6.1, charge 2,7 à 4,3, `bench.py` en processus neuf
   par taille, médiane de trois, en alternance avec la 0.16.1 (le tableau est dans DOCS.md,
   section 12, et le détail aux trois tailles dans le compte rendu de la session) : à 100 000
   lignes, ouvrir 8,0 → 0,21 s, charger 1,72 → 1,06 s, ouvrir puis charger en processus neuf
   7,6 → 1,27 s (la projection de l'étude donnait 1,9), pic 1,38 → 0,85 Go ; enregistrer 3,4 →
   0,33 s ; supprimer des lignes 5 à 10 % plus vite seulement (la passe sur les formules). Rien
   n'est plus lent qu'en 0.16.1 à aucune taille ; à 1 000 lignes, les petites opérations
   (`to_numpy`, lire et écrire une cellule, supprimer une ligne) sont égales ou plus rapides au
   chronomètre fin. `NotesReference.ods` (`time_open.py`, médiane de 9) : 46,1 → 9,4 ms.
   `bench_insertions.py` à 20 000 lignes : 2,3 à 2,4 fois plus rapide que la 0.16.1 partout.
   `compare_readers.py` : odsslicer lit désormais deux fois plus vite qu'odfdo 3.27 (1,4 s
   contre 2,6 s à 100 000 lignes, 12,3 s en 0.16.1), python-calamine 0.9 restant onze fois plus
   rapide.

   *Balayage* de 250 vrais fichiers contre `claude/lxml-study` (557 feuilles, 146 écrits et
   relus) : zéro partout — lecture, grille, écriture-relecture, parties recopiées, erreurs
   nouvelles (4 illisibles des deux côtés). *Fidélité* (`edit_parts.py`) : fixtures, 48
   éditions, 542 parties identiques à l'octet, zéro différence ; corpus de LibreOffice, 614
   fichiers, 3 366 éditions : 59 777 parties identiques à l'octet (les deux versions écrivent
   désormais avec lxml), 24 erreurs identiques des deux côtés, `too-many-cols-rows.ods` hors
   délai (300 s), zéro différence.

   **La documentation** : CHANGELOG « Unreleased » réécrit en notes de la 1.0 (retiré, changé,
   corrigé, vitesse ; le correctif des sauts de ligne dans un attribut, rangé par erreur sous la
   0.16.1, y est revenu : il n'a jamais été publié sur bs4) ; DOCS.md section 12 remesurée,
   comparaison aux autres lecteurs comprise, section 15 avec « Upgrading from 0.x » (chaque
   rupture, l'ancienne ligne et la nouvelle, vérifiées en les exécutant) ; README et CHANGELOG ne
   parlent plus de la règle des versions 0.x.

Pourquoi cet ordre : du plus isolé au plus couplé, et pour que chaque différence de balayage ait
une seule cause possible. Les étapes 1 et 2 ne changent rien à ce qu'on lit ou écrit, et le
balayage le prouve ; la 3 change peut-être quelque chose, et seulement ce qu'elle vise ; la 5
change de bibliothèque sans changer de logique, puisque toute la logique délicate a déjà été
déplacée et testée à l'étape 2. La bascule elle-même ne peut pas être découpée : tant que
`content.xml` et `styles.xml` partagent leur code, il n'y a pas d'état intermédiaire où l'un est
en lxml et l'autre en bs4.

Ruptures d'API de la 1.0 (faites à l'étape 5, dans le CHANGELOG et DOCS.md §15) : `reader.data`,
`styles_data`, `meta_data`, `settings_data`, `reader.tables`, `sheet.table`, `cell.cell` sont des
éléments ou arbres lxml ; `Cell.attrs` et `Sheet.attrs` sont retirés ; `export_content_xml(pretty=True)`
sort l'indentation de lxml. Changements de comportement : un fichier mal formé lève au lieu de
s'ouvrir réparé en silence ; un fichier aux préfixes non standard se lit ; une formule prend le
préfixe d'OpenFormula du document ; un saut de ligne écrit en référence dans un attribut survit à
l'enregistrement ; un élément d'un autre espace de noms au nom local d'une ligne ou d'une colonne
n'en est plus une (étape 6).

### Audit avant la 1.0

Avant de figer l'API par la 1.0 (une rupture attend ensuite la 2.0), l'auteur en a relu chaque
recoin, et validé point par point ce qui casse. Le travail se fait par parties, chacune dans sa
branche, partie de `claude/lxml-study`.

**Partie A — l'API publique : faite le 2026-10-18** (`claude/audit-a`, un commit par point, puis
la documentation ; à relire et fusionner). Chaque rupture a sa ligne dans le CHANGELOG et son avant/après dans
DOCS.md, section 15 (« Upgrading from 0.x ») :

1. *Une seule entrée publique* : `odsslicer.classes`, la couche de compatibilité de l'époque du
   module unique, est retiré ; `odsslicer` réexporte toute l'API (`Sheet`, `Cell`, `ArrayValues`,
   `string_address`, `string_to_col` en plus), et plus rien d'interne (`EMPTY_CELL`, `TAG_CELL`,
   `RE_STRING_CELL`, `MAX_REPEAT_*`). Docstring du paquet réécrite en anglais.
2. *Les 12 accesseurs doublons de `Sheet` retirés* (`get_row`, `get_rows`, `get_cell`,
   `get_cells`, `get_row_slice`, `get_col`, `get_cols`, `empty_row`, `empty_col`, `address`,
   `string_address`, `string_to_col`) : `sheet[...]` et les fonctions d'`addresses` font la même
   chose. L'analyseur d'adresses est `addresses._parse_address`, qui dit ce qui ne va pas — et
   corrige au passage `"A0"` (lu comme la colonne A de la dernière ligne), `"A:B1"` (lu `"A:B"`),
   `"1:B2"` (lu `"1:2"`), `"A1:B"` (`TypeError`).
3. *Les booléens nommés obligatoires* (`ODSReader(..., *, verbose)`, `new`, `save`,
   `export_content_xml`, `sort`, `NumberFormat.create`, `recalculate`, `Sheet(...)`, et les
   privés de `cell.py`) ; ruff `FBT` ajouté aux règles, `FBT003` ignoré dans les tests seuls
   (`find_all(True)` de bs4, `np.bool_(True)`).
4. *Lancer LibreOffice, en public* : `find_libreoffice()`, `libreoffice_command(profile)`,
   `libreoffice_environment(environ=None)`, dont `recalculate` se sert lui-même ; DOCS.md
   section 7, « Running LibreOffice yourself ». devoir_utils (`ponderer_notes_reference.py`,
   `tests/test_notes_ods.py`) appelle encore les anciens privés : à migrer de son côté.
5. *Les constantes mortes retirées* (`MAX_ROWS_PER_SHEET`, `MAX_COLS_PER_SHEET`,
   `MAX_CELLS_PER_SHEET`, `MAX_CHARS_PER_CELL`, `MAX_SHEETS`), les commentaires de
   `constants.py` réécrits.
6. *`<loext:text>` par URI* : les parties d'un format de nombre sont comparées par URI
   (`styles._FORMAT_PARTS`), `number:text` et `loext:text` explicitement ; le texte affiché
   inféré à l'écriture ne change pas, un élément d'un espace de noms inconnu n'est plus une
   partie.

Vérifié à chaque commit : pytest (suite LibreOffice comprise, `-W error::FutureWarning`), mypy,
ruff, `mutate_layers.py` et `mutate_flags.py` entièrement attrapées. Balayage de 250 vrais
fichiers contre `claude/lxml-study`, à la fin (557 feuilles, 146 écrits et relus) : zéro partout
— lecture, grille, écriture-relecture, parties recopiées, erreurs nouvelles (4 illisibles des
deux côtés).

**Ce qui casse chez l'auteur**, à migrer avant de monter `~/Envs/main` en 1.0 : compta
(`tests/conftest.py`, `feuille.get_cell(i, j)`), devoir_utils (`notes.string_address(...)` dans
`notes_ods/` et `scripts/`, les privés de LibreOffice), copiedouble (`get_cell`,
`string_address`, `sheet.address` dans `overall_export.py`, `capacities.py`, `roster.py`),
sncf-ods (`Sheet.string_address`).

**Partie B1 — les styles sans état double : faite le 2026-10-18** (`claude/audit-b1`, à relire
et fusionner). `CellStyle` recopiait chaque propriété résolue dans un attribut à sa création, et
chaque setter écrivait le XML puis sa copie : deux vues d'un même style divergeaient dès que
l'une écrivait (`a = cell.style; b = cell.style; b.bold = True` laissait `a.bold` à `False`).
Corrigé en simplifiant :

1. *Une propriété, une déclaration* : `_StyleProperty` (élément de propriétés, attribut,
   lecture, écriture), lue à chaque accès en remontant la chaîne d'héritage, paresseusement
   (elle s'arrête au style le plus proche qui la pose) ; écrite par `_write_attrs`, sur le style
   propre de la cellule comme avant. Vingt propriétés de `CellStyle` le sont ainsi ; les quatre
   côtés de bordure sont des `_BorderSide`, résolus et écrits en bloc (`_borders`,
   `_write_border_sides`) ; `underline` (trois attributs), `superscript`/`subscript`,
   `number_format`, `cell_properties`/`text_properties` sont des propriétés qui lisent le XML.
   Plus aucun état dans `__init__` : une vue de cellule lit sur la cellule le nom de son style
   (elle suit donc un fork fait par une autre vue) et sa valeur (un format conditionnel se
   résout contre la valeur actuelle).
2. *`RowStyle`, `ColumnStyle`, `TableStyle`* recopiaient de même : ils partagent
   `_SheetPartStyle`, qui lit le style que pointe maintenant leur ligne, colonne ou feuille
   (`Sheet._row_style_tag`, `_column_style_tag`, `_table_style_tag`).
3. *Docstrings* sur chaque propriété (`help(CellStyle)`), et `tests/test_styles.py` : le défaut,
   puis pour chaque propriété « une vue écrit, une autre lit » (42 tests, tous en échec sur
   l'ancien code).

Vitesse : une vue lisant trois propriétés (`bold`, `background_color`, `number_format`) sur
10 000 cellules coûte environ deux fois plus (0,50 → 1,04 s sur un classeur synthétique,
1,7 → 3,0 s sur NotesReference.ods) : la chaîne se parcourt à chaque propriété, et la recherche
d'un style par son nom est linéaire ; une vue par propriété (`cell.style.bold`, l'usage courant)
coûte moins (1,49 → 1,06 s ; 5,1 → 3,0 s). La recherche (`_find_in_styles`) est passée d'un
`all()` sur un générateur à une boucle, un tiers de moins. L'ouverture ne change pas
(`time_open.py`, 9,3 ms des deux côtés). Un index des styles par nom rendrait la lecture bien
plus rapide, mais ce serait un cache à tenir à jour : écarté.

Vérifié : pytest (1 429 tests, suite LibreOffice comprise), mypy, ruff, `mutate_layers.py` et
`mutate_flags.py` entièrement attrapées ; balayage de 250 vrais fichiers contre
`claude/lxml-study` à zéro partout (4 illisibles des deux côtés), `edit_parts.py` sans écart sur
les fixtures, et toutes les propriétés de style lues sur 88 503 cellules de 69 fichiers
identiques entre les deux versions.

**Parties B2–B5 — découper ce qui était trop gros : faites le 2026-10-18** (`claude/audit-b2`, à
relire et fusionner). `Sheet` (1 865 lignes, sept responsabilités), `Cell` (1 343 lignes avec
`ArrayValues` et `Comment`) et `ODSReader` (858 lignes, quatre responsabilités) sont découpés en
modules internes d'une responsabilité chacun, sans rien changer de l'API publique
(`odsslicer.__all__`, méthodes, propriétés, signatures, docstrings). La forme est la même
partout : une classe d'aide qui reçoit la `Sheet` (ou le paquet) et fait une chose, créée à
chaque appel puisqu'elle ne garde aucun état ; des fonctions de module pour ce qui ne touche que
des éléments. Aucun mixin : aucun souci n'a besoin de tout l'état de `Sheet`, seulement de sa
grille (`rows`, `n_rows`, `n_cols`) et de son élément (`table`). Chaque module dit dans sa
docstring ce qu'il fait et pourquoi il est à part, déclare `__all__` (vide quand rien n'y est
public, comme `fileutils`), et les imports sont absolus (ruff `TID252`).

La carte des modules, du bas vers le haut (un module n'importe, à l'exécution, que des modules
au-dessus de lui dans cette liste ; `tests/test_imports.py` vérifie qu'il n'y a pas de cycle et
que `__init__.py` ne contient que des imports) :

| module | lignes | responsabilité | importe (à l'exécution) |
| --- | --- | --- | --- |
| `xmltree`, `addresses`, `datetimes`, `dateformats`, `fileutils` | — | inchangés | — |
| `numberformat` | 410 | les formats de nombre : `NumberFormat`, et `_NumberFormatSpec` d'où `create` construit l'élément | `dateformats`, `xmltree` |
| `values` | 165 | une valeur entre Python et ODF : `_plain_value`, `_Encoded` (type, attribut, texte, valeur relue, sorte de format de date) | `datetimes`, `xmltree` |
| `selection` | 152 | `ArrayValues` | — |
| `comment` | 124 | `Comment` | `xmltree` |
| `package` | 284 | le zip : parties en octets et en arbres, `touch`/`touch_tag`, `write` ; les deux erreurs publiques | `fileutils`, `xmltree`, `xmlutils` |
| `styles` | 635 | les vues de style (`CellStyle`, `RowStyle`…), sans les formats de nombre ni le rendu | `numberformat`, `xmltree`, `xmlutils` |
| `gridxml` | 390 | le XML d'une feuille sans `Cell` : répétitions, lignes, cellules, ce qui est au-delà de la grille | `constants`, `xmltree` |
| `references` | 78 | formules et dessins de tout le document qui suivent une édition ou un renommage | `formulas` |
| `pivots` | 153 | la définition d'un tableau croisé, ses champs en `_PivotFields` | `addresses`, `formulas`, `xmltree` |
| `catalog` | 356 | trouver un style dans les deux parties, en créer (forks de cellule, de ligne, de colonne, de feuille ; formats de date) | `package`, `numberformat`… |
| `drawings` | 162 | graphiques et formes, et les adresses qu'ils tiennent (une boucle pour décaler et renommer, il y en avait deux copies) | `package`, `formulas` |
| `columns` | 155 | les définitions de colonnes, par colonne logique | `gridxml` |
| `unrolling` | 106 | donner à une position de la grille un élément à elle (`_Unroller`), les suites de lignes répétées | `gridxml` |
| `display` | 571 | le texte affiché d'une valeur écrite : par l'exemple (`_DisplayText`) ou par le format (`_render_*_from_format`) | `columns`, `numberformat`, `datenames`… |
| `copying` | 153 | copier et trier, partageant `_CellContent` | `columns`, `formulas` |
| `merges` | 168 | les cellules fusionnées (`_Merges`) | `gridxml`, `unrolling` |
| `cell` | 692 | `Cell` : lecture, écriture en place, style, fusion, note, lien | `display`, `values`, `styles`… |
| `loading` | 341 | construire la grille (`_GridLoader`) : où elle finit, sa largeur, les `Cell`, le rognage | `cell`, `gridxml`, `unrolling` |
| `growth` | 141 | agrandir la grille, et ce que le fichier tient au-delà | `cell`, `gridxml`, `unrolling` |
| `structure` | 274 | insérer et supprimer lignes et colonnes, en étapes nommées | `growth`, `merges`, `references`… |
| `sheet` | 520 | `Sheet` : l'API et ses docstrings, l'indexation, `materialize_cell`, ce que cellules et vues demandent à leur feuille | toutes les aides de feuille |
| `reader` | 393 | `ODSReader` : les feuilles, `save`, les propriétés ; compose `_Package`, `_StyleCatalog`, `_Drawings` | `package`, `catalog`, `drawings`, `sheet`… |

Ce qui a changé en passant, en simplifiant plutôt qu'en ajoutant :

1. *Deux états doubles de `Sheet`* : `stylename` était une copie prise au chargement, qu'une
   écriture de `sheet.style` laissait périmée, et `size` une troisième copie de `n_rows` et
   `n_cols` que chaque édition devait remettre ; ce sont des propriétés. `Sheet._is_merge_master`
   doublait `Cell.is_merge_master` : retiré.
2. *Une seule façon de forker un style* : `Cell` et `Sheet` avaient chacun la leur ; le catalogue
   les tient (`fork_cell_style`, `fork_sheet_part_style`, `date_formatted_style`).
3. *Une valeur qu'une cellule ne peut pas tenir lève avant qu'on la touche* : elle levait une fois
   les attributs de valeur retirés.
4. *Le rendu par le format* (`_render_number_from_format`, `_render_date_time_from_format`) passe
   de `styles` à `display`, avec l'inférence par l'exemple : les deux répondent à la même
   question, qu'aucune vue de style ne pose.

Fonctions raccourcies (B5), complexité ruff avant → après (`C901`, `PLR0912`, `PLR0915`,
`PLR0913` ; 28 relevés avant, 2 après) : `Sheet.load` 28 → `_GridLoader` en sept étapes ;
`NumberFormat.create` 19 et 9 paramètres → `_NumberFormatSpec` (vérifiée à la création) et
`_number_format_element`, une étape par famille (la signature publique garde ses 9 paramètres) ;
`_render_date_time_from_format` 18 → `_DateTimeLayout`, `_shown_fields`, `_ComponentRenderer`, et
`_elapsed_fields` rend un `_ElapsedFields` figé au lieu d'un `SimpleNamespace` ; `Sheet.sort`
15, `delete_rows` 15, `insert_rows` 14, `delete_column` 11, `merge` 11 → des méthodes d'étape ;
`Cell.value` 12 (54 instructions) → `_encoded` et `_DisplayText.of` ; `Cell.__init__` 12 → 9 (une
table des attributs de valeur par type) ; `_plain_value` 12 → sa part numpy à part ;
`xmltree.set_paragraph_text` 12 → `_encoded_whitespace`, `_write_after` ; les 6 paramètres de
`_normalize_row_width` → un `_RowMeasure` figé, ceux de `styles._flag` → 5 (`_line` pour le
barré). Restaient les signatures publiques de `NumberFormat.create` (9) et
`Sheet.create_pivot_table` (6) : changées ensuite, avec `Sheet.name`, sur `claude/audit-c`
(ci-dessous).

Vitesse, contre `claude/lxml-study`, en alternant les versions : `bench.py` à 10 000 et
100 000 lignes, deux tours, sans écart mesurable (chargement de la feuille 93–97 ms contre
97–98 ms, et 1 037–1 043 ms contre 1 038–1 067 ms ; écriture de 1 000 cellules, suppression,
copie, enregistrement à l'identique) ; `time_open.py` sur NotesReference.ods 9,2 ms des deux
côtés. Une écriture prépare deux objets de plus (`_Encoded`, `_DisplayText`), 0,5 µs sur 6 :
`_plain_value` reconnaît désormais `str`, `int` et `float` par leur type avant les vérifications
numpy et `numbers`, qui coûtaient davantage (400 → 80 ns), et 1 000 flottants s'écrivent comme
avant (7,5–7,7 ms des deux côtés sur un classeur de 10 000 lignes) ; le tri de 1 000 lignes reste
un peu plus lent (29,5–30,7 ms contre 29,0–29,7 ms, un `_CellContent` par cellule), 200 dates
un peu plus rapides (111–114 contre 115–118 ms).

Vérifié à chaque commit : pytest (1 431 tests, suite LibreOffice comprise), mypy, ruff ;
`mutate_layers.py` (41) et `mutate_flags.py` (14 : les deux mutations des boucles de décalage et
de renommage des graphiques n'en font plus qu'une, la boucle étant unique) entièrement attrapées,
aucune périmée. Comportement : balayage de 250 vrais fichiers contre `claude/lxml-study`
(écriture sur 150) à zéro partout — lecture, grille, écriture-relecture, parties recopiées,
erreurs nouvelles (4 illisibles des deux côtés) ; `edit_parts.py` sans écart sur les 8
fixtures (48 éditions) et sur les 614 fichiers du corpus de LibreOffice (3 366 éditions, 59 777
parties identiques à l'octet, 24 erreurs identiques des deux côtés, un fichier au-delà de
`--timeout 120`).

**Les trois ruptures que B2–B5 proposaient, et la partie C — les conventions : faites le
2026-10-18** (`claude/audit-c`, un commit par point, à relire et fusionner ; ne casse rien
d'autre).

Les ruptures, validées par l'auteur, chacune dans le CHANGELOG et dans DOCS.md, section 15
(« Upgrading from 0.x ») :

1. *`NumberFormat.create(reader, spec)`* : `NumberFormatSpec`, la spécification interne
   devenue publique et exportée, remplace les neuf paramètres ; figée, vérifiée à sa création,
   hachable, `components` gardé en tuple mais accepté en liste. L'appel de la 0.x lève
   `TypeError`, qui, sur la famille seule, dit d'écrire `NumberFormatSpec(...)`.
2. *`Sheet.create_pivot_table(source, target, *, rows, columns, values, name)`* : les champs
   et le nom, nommés obligatoires.
3. *`Sheet.name`* lit `table:name` sur l'élément de la feuille : c'était une copie, que
   `rename_sheet` tenait à jour ; `sheet.name = ...` lève `AttributeError` en renvoyant vers
   `rename_sheet` (le setter est typé `NoReturn`, que mypy refuse aussi).

La partie C, en conventions de CLAUDE.md :

1. *`__all__` partout* : `constants` (`FORMATS` seul), `properties` (`DocumentProperties`),
   et vide dans `datetimes`, `dateformats`, `datenames`, `formulas`, `xmlutils` ; chaque module
   à `__all__` vide le dit dans sa docstring. `tests/test_imports.py` vérifie que tout module
   en déclare un, sans nom privé, et que le paquet exporte les noms publics de chacun (sauf
   `xmltree`).
2. *Docstrings de module* réécrites pour dire le pourquoi : `constants`, `formulas` (des
   expressions régulières sur les références plutôt qu'un analyseur d'OpenFormula),
   `xmlutils`, `addresses`, `styles` (des vues sans état), `properties`, `libreoffice`
   (l'exemple de `LIBREOFFICE_COMMAND`, sorti d'un commentaire qu'ERA001 prenait pour du code).
3. *Un fichier de test par module* : `tests/test_odsslicer.py` (841 tests, 7 100 lignes) est
   réparti en 25 fichiers, `test_<module>.py`, par un script qui déplace chaque test tel quel et
   met une aide partagée dans `conftest.py` ; les 1 474 identifiants collectés avant et après
   sont les mêmes, au chemin près. Restent à part, transverses : `test_libreoffice_consistency.py`,
   `test_wild_files.py`, `test_imports.py`. N'ont pas de fichier : `constants`, `fileutils`,
   `xmlutils` et `package` (dont `test_reader.py` teste l'écriture par `save`).
4. *Noms et paramètres* : 156 tests renommés `test_<scénario>_<résultat>` (la famille
   `test_save_round_trip_after_*` devient `..._reads_back_after_a_save`), dont 83 en passant
   dans l'une des onze classes `Test<Unité>` qui rangent 96 tests (`TestParseAddress`,
   `TestCreate`, `TestRenameSheet`…), la liste complète dans le message du commit ; cinq
   boucles de cas deviennent des paramètres (1 474 tests deviennent 1 655). Une boucle qui construit des données, ou qui vérifie chaque cellule
   d'un seul résultat, reste. Les en-têtes et la docstring en français passent en anglais.
5. *Menus défauts* : l'argument inutilisé de `formulas._insertion_remap` (`_edge`, et pourquoi),
   celui du lecteur du type absent de `FORMATS`, les deux `NotImplementedError` sans message de
   `styles._SheetPartStyle`. ERA001 ne trouve plus rien dans `src` ; ses quatre relevés dans les
   tests sont des en-têtes.

Les scripts de mutation nomment les fichiers où leurs tests sont passés, et ne comptent plus
pour attrapée qu'une mutation qui fait échouer un test (code 1) : un fichier de tests disparu
les aurait toutes données pour attrapées.

Vérifié à chaque commit : pytest (suite LibreOffice comprise, `-W error::FutureWarning`, relancée
seule quand une autre session lançait LibreOffice), mypy, ruff, `mutate_layers.py` (41) et
`mutate_flags.py` (14) entièrement attrapées. Comportement : balayage de 250 vrais fichiers
contre `claude/lxml-study` (557 feuilles, 146 écrits et relus), à la fin, à zéro partout —
lecture, grille, écriture-relecture, parties recopiées, erreurs nouvelles (4 illisibles des deux
côtés).

### À décider

- ~~**Fusionner le prototype quand ?**~~ Tranché le 2026-10-04 : la couche et ses tests sur
  `master`, le portage de `properties.py` sur `claude/lxml-study` jusqu'à la 1.0 (voir « Pour
  démarrer »). Rebasée sur la v0.15.0, la branche balayait à zéro partout (250 fichiers, écriture
  sur 150), le fichier chiffré levant la même erreur des deux côtés.
- ~~**`Cell.attrs` et `Sheet.attrs`, clés des propriétés, analyse stricte, `of:` non déclaré,
  plancher de lxml**~~ Tranché le 2026-10-17 par l'auteur : voir l'étape 4 du plan, et DOCS.md,
  section 15. `attrs` disparaît (remplacé par `Cell.style_name`), les clés gardent le préfixe de
  la spécification, un XML mal formé lève `MalformedDocumentError`, `of:` prend le préfixe du
  document ou se déclare, lxml ≥ 5.0.

