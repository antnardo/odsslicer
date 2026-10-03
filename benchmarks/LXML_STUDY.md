# Dropping BeautifulSoup for lxml alone: feasibility study

Prepared on 2026-10-03 for the decision recorded in `ROADMAP.md` ("Vers 1.0 — se passer de
BeautifulSoup, lxml seul"), on top of v0.14.3. It is a study and a prototype, not the
migration: one small module is ported (`properties.py`, with the `meta.xml` part it reads), the
rest of the package is untouched. The plan that follows from it is in `ROADMAP.md`, in French
like the rest of that file.

Everything here can be measured again:

| script | answers |
| --- | --- |
| `benchmarks/lxml_inventory.py` | where the code calls bs4, by the operation each call needs |
| `benchmarks/lxml_corpus_scan.py` | what real files declare and whether a strict parser takes them |
| `benchmarks/lxml_profile_open.py` | how much of opening a sheet is bs4's, and what lxml would take |
| `tests/test_xmltree.py` | each pitfall, failing the naive way, then held by the access layer |

## The verdict in five lines

- **Nothing in the code lacks a reasonable lxml equivalent.** Two operations need a helper of
  about twenty lines each, because lxml has no counterpart: `unwrap()`, and a lazy search in
  document order (`previous_elements`/`next_elements`). Both are written and tested against bs4.
- **bs4 accounts for 94 % of opening and loading a sheet** at 100,000 rows, over 90 % at every
  size: the parse, the navigation, and the garbage collector its millions of Python objects keep
  busy (a third of the opening time at 100,000 rows). odsslicer's own work is the remaining 6 %.
- **That share is not the gain.** lxml parses 23 times faster, but walks the tree only twice as
  fast: each element reached gets a Python proxy. Projected on lxml, opening and loading a
  100,000-row sheet goes from 12.2 s to 2.1 s (5.8×) and the peak memory from 1.9 GB to 0.9 GB.
- **The coupling follows the package parts, not the modules.** A module moves with the part it
  reads. `content.xml` and `styles.xml` are read by the same code, so they have to switch
  together, which is where the risk is (see the plan).
- **Two premises of the decision need correcting**: lxml does not keep escapes as written (it is
  faithful in canonical form, as bs4 has been since 0.14.2), and the 28 s opening time could not
  be reproduced (12 to 15 s here; see the measurements).

## 1. Inventory of the call sites, by operation

Counted by `lxml_inventory.py`, which runs mypy with a plugin recording every call and attribute
access whose receiver is typed as a bs4 class, so that `list.append` and `dict.get` are not
counted. A second pass adds the members only bs4 has wherever mypy saw `Any` (an element taken
from a `find_all()` result, `Cell.attrs`). A site is a (line, member) pair.

On v0.14.3: **519 call sites and 132 type mentions** (`Tag`, `BeautifulSoup` in annotations and
casts) in 7,235 lines. 0.14.3 added 13 of them, in `_paragraph_text` and `_set_paragraph_text`.
By module: `sheet` 167, `cell` 121, `styles` 99, `reader` 73, `properties` 30, `xmlutils` 28,
`constants` 1. After the port on this branch: 488 and 124.

| operation | sites | modules (sites) | lxml | kind |
| --- | --- | --- | --- | --- |
| attributes: read, write, delete, test | 238 | cell 67, sheet 65, styles 65, reader 32, properties 6, xmlutils 3 | `el.get(qn(...))`, `el.set`, `el.attrib` | mechanical, with two decisions |
| search by name among children or descendants | 82 | cell 20, sheet 20, reader 16, styles 15, properties 8, xmlutils 3 | `iterchildren(qn)`, `iterdescendants(qn)`, `find(qn)` | mechanical, each site to classify |
| insert: at the end, before, after, at an index | 62 | sheet 25, reader 11, styles 10, cell 8, properties 4, xmlutils 4 | `append`, `addprevious`, `insert_after()`, `insert` | mechanical with the helper |
| text of an element and its descendants | 27 | properties 9, styles 8, cell 7, xmlutils 2, reader 1 | `text()`, `set_text()`, `paragraph_text()`, `set_paragraph_text()` | mechanical with the helpers |
| remove, detach, unwrap, replace | 27 | sheet 10, cell 9, properties 3, reader 3, xmlutils 2 | `remove()`, `unwrap()`, `parent.replace` and its tail | mechanical with the helpers |
| parent, siblings, children | 27 | sheet 22, reader 2, xmlutils 2, cell 1 | `getparent()`, `itersiblings()`, `iterchildren(etree.Element)` | mechanical |
| qualified name of an element | 26 | sheet 16, reader 4, xmlutils 4, cell 1, styles 1 | `el.tag == qn(...)`, `etree.QName(el).namespace` | mechanical, and fixes a bug |
| clone | 11 | sheet 9, cell 1, xmlutils 1 | `clone()` | mechanical with the helper |
| parse, build, serialise | 10 | xmlutils 5, reader 4, constants 1 | `parse()`, `serialize()`, `new()` | mechanical, one decision |
| search in document order, or upwards | 7 | cell 7 | `preceding()`, `following()`, `iterancestors()` | helper written |
| text nodes built by hand (`NavigableString`) | 2 | xmlutils 2 | `.text`, `.tail` | mechanical |
| type mentions | 132 | sheet 55, reader 25, cell 24, xmlutils 15, properties 8, styles 5 | `etree._Element` | mechanical |

The 82 name searches break down as 47 over descendants, 22 over children (`recursive=False`),
6 of any child element (`find_all(True, recursive=False)`), 10 over several names at once and
9 filtered on an attribute, 4 of them on an attribute alone (`find_all(attrs={"style:name": True})`).

### What is mechanical

Most of it, once the helpers exist: an attribute read is `el.get(qn("table:style-name"))`, a
search over children is `children(el, "text:p")`, a removal is `remove(el)`. The prototype
shows the shape on `properties.py`: 30 sites in 189 lines, and the 22 existing tests passed
unchanged.

### What needs a decision

- **Children or descendants, site by site.** bs4's `find_all` searches descendants unless told
  otherwise, and 47 sites rely on that default. Most mean children (a cell's paragraphs, a
  style's properties); some do mean descendants (rows inside a `table:table-row-group`). The
  translation has to say which: this default once had a note's paragraphs read as the cell's
  value. Reviewing the 47 is the slowest part of the mechanical work, and the one that finds
  bugs.
- **`Cell.attrs` and `Sheet.attrs` are public.** They are the element's own attribute dict, with
  prefixed keys, and DOCS.md shows `sheet["A1"].attrs.get("table:style-name")`. lxml's
  `el.attrib` is keyed `{URI}local`. Three options: drop them, expose `el.attrib` as it is, or
  keep a read-only view with prefixed keys (`prefixed()` exists for that). The same question for
  `CellStyle.cell_properties` and `text_properties`, documented as raw attribute dicts: I would
  keep their keys prefixed with the specification's prefix, which does not depend on the file.
- **The hot path wants constants.** `qn()` is a dict lookup behind a call; in `Cell.__init__`
  and `Sheet.load`, which run once per cell, precomputed names (`_VALUE_TYPE = qn(...)`) are
  worth it. A choice of style rather than a risk.
- **Strict parsing.** See pitfall 4.

## 2. The pitfalls, each with a failing case

Every one of them is a test in `tests/test_xmltree.py`, which runs the naive translation first,
then the layer, and in most cases compares the result with what bs4 does on the same edit, in
canonical form (C14N).

### Pitfall 1: the tail

In lxml the text after an element belongs to that element (`el.tail`), not to its parent. Four
consequences, each shown failing:

| bs4 | naive lxml | what happens | layer |
| --- | --- | --- | --- |
| `span.decompose()` in `Total<text:span>:</text:span> 42 €` | `p.remove(span)` | reads `Total`: the text after the span left with it | `remove()` gives the tail to the previous sibling or the parent |
| `copy.deepcopy(tag)` | the same | the copy carries the tail: inserted next to the original, ` 42 €` is said twice | `clone()` drops it |
| `ref.insert_after(new)` | `ref.addnext(new)` | lands after the text that followed `ref`, not right after `ref` | `insert_after()` moves that text to the new element |
| `a.unwrap()` on `Voir <text:a>ici</text:a> pour le détail` | take `a.text`, `p.remove(a)` | reads `Voir ici`: lxml has no `unwrap`, and the obvious stand-in drops the rest | `unwrap()` |

How much is at stake depends on where the code removes elements. Of the 27 removal sites, 25
work on element-only content (cells, rows, styles, `office:meta`), where a tail is indentation at
most: losing it changes nothing a reader sees. Two work inside a paragraph, both in `cell.py`'s
`hyperlink`:

- `hyperlink = None` unwraps the link. A link on part of a cell's text is something LibreOffice
  writes, and the naive port would cut the text after the link: that case is now a test of the
  current behaviour (`test_removing_a_link_on_part_of_the_text_keeps_the_text_around_it`), for
  the port of `cell.py` to keep passing.
- `hyperlink = url` moves the paragraph's content into a new link (0.14.3,
  `a.append(child.extract())`). In lxml the text before the first child is no child, it is
  `p.text`: moving the children leaves it outside the link (tested). Here the tails moving
  along with their elements is what is wanted.

Writing is where the tail matters most: `set_paragraph_text()`, which writes
`a<text:s text:c="2"/>b` since 0.14.3, has to put `b` in the tail of `<text:s/>`.

### Pitfall 2: qualified names, and whose prefixes

bs4 matches `"table:table-cell"` against the prefix as the file writes it. XML lets a file bind
any prefix: `tests/test_xmltree.py` builds one from `TEST.ods` with the table namespace bound to
`tab:` (a rebinding that changes nothing else, checked in `{URI}local` form).

- **LibreOffice** reads it exactly as the original: the same CSV export (`requires_soffice` test).
- **odsslicer today** opens it without a word, with **no sheet at all** (`sheets_names == []`).
  Pinned by a strict `xfail`, to be lifted when `content.xml` moves to lxml.
- **The layer** finds the four sheets.

Does such a file exist? None among 492 real files sampled as the sweep does (293 LibreOffice, 116
LibreOfficeDev, 58 OpenOffice, 5 Microsoft Office, gnumeric, ODFPY, Collabora...): every one
uses the specification's prefixes. The bug is real, but no producer in the corpus triggers it.

Whether the namespace map should be built from the document's own declarations is a question
worth answering precisely:

- **For names, no.** lxml names an element by URI. The URIs are fixed by the specification, and
  the prefixes the code writes (`"table:table-cell"`) are its own vocabulary: `NAMESPACES` maps
  them to URIs once and for all, and that is what makes a `tab:` file readable. A map built from
  the document would *break* it: a document binding `tab:` has no `table` key to look up. What
  must not be hard-coded is the assumption that the document uses the same prefixes, which is
  what bs4 makes today.
- **For prefixes inside values, yes.** A formula names its language with a prefix, `of:=SUM(...)`,
  which only the document's declarations resolve: no XML parser looks inside an attribute value.
  odsslicer writes `of:=` whatever the document binds. The corpus scan found 289 files with
  formulas (260 using `of:`, 29 OpenOffice's `oooc:`), and **5 with formulas in `of:` while
  `of` is not declared at all**. All 5 were written by LibreOffice, under a folder of backups
  "before injection": a tool rewrote them. I did not open them further. `prefix_of()` reads the
  binding; deciding what to write when there is none is for the port of `formulas.py`.

### Pitfall 3: creating an element

bs4 cannot build `<table:table-row/>` without the namespace's URI at hand, hence
`xmlutils._blank_template`, which copies an existing element of the document and strips it, and
`_new_qualified_tag` as a fallback when there is none to copy. With lxml the question goes away:
`new("table:table-row")` creates the element with the specification's prefix declared on
itself, and **once inserted under an element where the document binds that URI, lxml drops the
declaration and takes the document's prefix**. Under `tab:`, it comes out as `<tab:table-row/>`,
with no stray `xmlns`. Where nothing binds it, it keeps its own declaration, which is valid. Both
cases are tested. `_blank_template`, `_new_qualified_tag`, `EMPTY_CELL_BS` and `_TAG_FACTORY`
become `new()`: 57 call sites on v0.14.3, all mechanical.

### Pitfall 4: strictness

bs4 drives lxml with `recover=True`. On the corpus, a strict parser rejected 6 parts out of
1,475, all from the two **encrypted** files of LibreOffice's test suite. Recovery never had to
repair a real XML file, and on well-formed parts it changes nothing. What recovery does to an
encrypted file is worse than an error: it opens as a document with **no sheet**, and `save()`
writes a 39-byte `content.xml` (the XML declaration) over the 941 encrypted bytes, or over the
source file itself when no path is given. `parse()` is strict: such a file raises
`XMLSyntaxError`. Better still, detect the encryption in the manifest and say so: proposed
separately, since it is a bug of today's releases.

Three smaller pitfalls are tested in the same file: `el.iter(name)` includes `el` itself where
bs4's `find_all` does not; `el.text` stops at the first child where `get_text()` reads
everything; and iterating an element's children yields comments, whose `tag` is not a string.

### Fidelity, measured again

The roadmap says lxml keeps attribute order, escapes and whitespace. On the eight fixtures,
`serialize(parse(part))` for every XML part comes back **identical in canonical form (C14N)**,
which is the claim that matters. Attribute order and whitespace are kept as written. The bytes
are not: `&apos;` and `&quot;` come back as characters (up to 123 per part), `<x></x>` becomes
`<x/>`, and namespace declarations move before the attributes (the manifest only). The XML
declaration comes from the original bytes: lxml cannot tell `standalone="no"` from no
`standalone` at all, writes single quotes, and Excel follows it with CRLF. So the byte-for-byte
copy of an untouched part stays necessary, and regenerating `styles.xml` is "safe" in the
same sense as with bs4 since 0.14.2, no more.

## 3. The prototype

`src/odsslicer/xmltree.py`, 382 lines with their reasoning, is not a facade. Code keeps
calling lxml elements directly (`el.get`, `el.set`, `getparent()`), and the module holds only
what lxml does differently enough to cost a bug: `qn`/`prefixed`/`prefix_of`, `parse`/`serialize`,
`children`/`descendants`/`preceding`/`following`, `text`/`set_text`/`paragraph_text`/`set_paragraph_text`,
`new`, `remove`/`clone`/`insert_after`/`unwrap`. Its 84 tests include parity with bs4 for every
edit, and each guard was broken once to see a test fail.

**Ported:** `properties.py` and the lines of `ODSReader` that parse and serialise `meta.xml`.
`reader.meta_data` is an lxml `ElementTree`. The cost: 30 call sites rewritten, 3 lines of
`reader.py`, no test changed, two added (a `meta.xml` with other prefixes is read, and written back
with its own prefixes; the partial-link guard above). The module got simpler: no cast, no
template copied to create an element.

What the port teaches about the cost per module is less the line count than the coupling. Moving
`properties.py` meant moving `meta.xml`, and nothing else reads it. For `cell.py`, `sheet.py` and
`styles.py`, the trees are shared: a `Cell` holds an element of `content.xml`, its `CellStyle`
one of `content.xml` or `styles.xml` depending on where the style lives, and `_find_in_styles`
searches both. They cannot switch one at a time.

## 4. Measurements

Apple M4, 16 GB, Python 3.14, one fresh process per size, on a machine with a background load
(load average 4 to 7). The synthetic workbooks are `bench.py`'s: N rows × 5 columns.

### The number that decides

`lxml_profile_open.py` wraps every bs4 method odsslicer calls in a clock and times the garbage
collector through `gc.callbacks`. Opening (`ODSReader(path)`) then loading the sheet
(`reader.sheet(name)`, which builds the grid), on v0.14.3:

| | 1,000 rows | 10,000 rows | 100,000 rows |
| --- | --- | --- | --- |
| open | 0.06 s | 0.63 s | 7.96 s |
| of which under bs4 | 92 % | 89 % | 70 % |
| of which the collector, under bs4 | 6 % | 11 % | 30 % |
| load | 0.04 s | 0.42 s | 4.45 s |
| of which under bs4 | 71 % | 74 % | 69 % |
| of which the collector, under bs4 | 11 % | 9 % | 16 % |
| **open + load** | **0.11 s** | **1.05 s** | **12.41 s** |
| **bs4 and its collector** | **91 %** | **93 %** | **94 %** |
| odsslicer's own code | 9 % | 7 % | 6 % |

So the answer to "how much of opening is bs4's" is 94 % at 100,000 rows: well past the 80 %
the decision set as the threshold. odsslicer's own work (the grid, the repeats, the value
conversions, building a `Cell` per cell) is 0.7 s of 12.4 s.

The garbage collector is the surprise: a third of the opening time at 100,000 rows, because a bs4
tree is millions of Python objects that every full collection walks. An lxml tree is C
structures: the collector does not see it.

A sampling profiler was tried first and set aside. It put bs4's share of loading at 30 to 46 %,
where the wrapped clocks and a plain measurement of `Cell()` against the bs4 calls it makes agree
on about 80 to 90 %: 2.14 µs of a cell's 2.42 µs go to `find_all("text:p", recursive=False)` and
`get_text()`.

### What lxml would take

| | 1,000 rows | 10,000 rows | 100,000 rows |
| --- | --- | --- | --- |
| `content.xml` | 0.7 MB | 7.4 MB | 75 MB |
| parse, bs4 as odsslicer does | 0.06 s | 0.63 s | 7.83 s |
| parse, lxml alone | < 0.01 s | 0.04 s | 0.34 s (23×) |
| walk the grid, bs4 | 0.03 s | 0.30 s | 2.92 s |
| walk the grid, lxml | 0.02 s | 0.15 s | 1.49 s (2.0×) |
| memory the parse adds, bs4 | 46 MB | 188 MB | 1,531 MB |
| memory the parse adds, lxml | 9 MB | 47 MB | 426 MB |
| **open + load, today** (fresh process) | **0.10 s** | **1.02 s** | **12.19 s** |
| **open + load, projected on lxml** | **0.02 s** | **0.21 s** | **2.09 s (5.8×)** |
| **peak RSS, today** | **72 MB** | **241 MB** | **1,925 MB** |
| **peak RSS, projected on lxml** | **61 MB** | **136 MB** | **888 MB (−54 %)** |

The parse is 23 times faster, but the navigation `Sheet.load` does (rows, cells, repeats,
attributes, paragraphs) only twice as fast, because every element reached gets a Python proxy.
That is why bs4's 94 % share turns into a 5.8× gain rather than a 15× one. The projection
(`projected_open_and_load`) parses the three parts with lxml and runs `Sheet.load`'s passes,
building an object of `Cell`'s size per cell with the same value conversion, collector on, in a
fresh process. It is a projection, not the port: it leaves out what `Sheet.load` does for fillers
and trailing empty rows, which these workbooks do not have.

The "~28 s to open 100,000 rows" of the decision could not be reproduced: opening and loading
take 12 to 15 s here, 14 s in DOCS.md (11 s + 5.3 s, Python 3.12). The closest figure is the
25 s of DOCS.md's reader comparison: odsslicer 0.11 reading a 100,000 × 5 float matrix in full.

### `bench.py`, before and after the port

`bench.py`, one fresh process per size, on `master` (v0.14.3) and on this branch, with the
DOCS.md table (Python 3.12, an earlier version, another day) for reference:

| operation | DOCS.md, 100k | v0.14.3: 1k / 10k / 100k | branch: 1k / 10k / 100k |
| --- | --- | --- | --- |
| generate + save | 42 s | 0.31 s / 3.8 s / 242 s | 0.31 s / 3.6 s / 175 s |
| open | 11 s | 62 ms / 0.65 s / 8.9 s | 63 ms / 0.64 s / 9.0 s |
| first sheet access (`load`) | 5.3 s | 42 ms / 0.49 s / 4.7 s | 43 ms / 0.41 s / 4.7 s |
| full read (`to_numpy`) | 75 ms | < 1 ms / 81 ms / 0.40 s | < 1 ms / 35 ms / 0.38 s |
| write a 1,000-cell range | 14 ms | 12 / 51 / 373 ms | 13 / 43 / 374 ms |
| `sort` 1,000 rows | 51 ms | 42 / 206 / 427 ms | 40 / 77 / 443 ms |
| `delete_row` | 0.9 s | 7 / 90 / 708 ms | 7 / 71 / 760 ms |
| `delete_rows` (10) | 1.3 s | 7 / 72 / 666 ms | 6 / 66 / 679 ms |
| `copy` 1,000×2 | 5.3 s | 121 / 544 / 5,358 ms | 114 / 523 / 5,452 ms |
| `save` | 4.1 s | 53 / 369 / 3,604 ms | 49 / 360 / 3,501 ms |
| peak RSS | 2.0 GB | 78 / 288 / 2,230 MB | 78 / 287 / 2,268 MB |

The branch and `master` agree within the noise of a loaded machine, as they should: the ported
path is `meta.xml`, a few hundred bytes whatever the size of the sheet. The projection above is
where the rewrite's gain shows. Two things this table shows besides:

- **"generate + save" is superlinear**: ×12 from 1,000 to 10,000 rows, then ×50 to 100,000,
  and 175 to 242 s where DOCS.md says 42 s. It is bulk writing, unrelated to this study, and the
  same on `master`: proposed separately.
- `to_numpy` and `sort` vary by a factor of two at 10,000 rows between runs: the machine's
  background load, not the code.

## 5. What is left to decide

Listed with the plan in `ROADMAP.md`.
