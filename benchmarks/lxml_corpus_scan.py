"""Two questions about real .ods files that decide how lxml must be set up,
answered on the same sample as `sweep_real_files.py`.

1. **Prefixes.** Does any file bind an ODF namespace to another prefix than
   the one the specification uses (`tab:` for `table:`), or use it as the
   default namespace? BeautifulSoup matches `"table:table-cell"` against the
   prefix as written, so such a file reads as having no sheet at all.
2. **Prefixes inside values.** A formula names its language with a prefix,
   `of:=SUM(...)`, which the document's own declarations resolve: odsslicer
   writes `of:=` whatever the document binds. How many documents leave `of`
   unbound, and which prefixes do their formulas use?
3. **Strictness.** BeautifulSoup drives lxml with `recover=True`, which
   repairs malformed XML without a word. Does every part odsslicer parses
   pass a strict lxml parser, and when recovery is needed, does it change
   what the tree holds?

**Privacy.** As in the sweep: nothing here prints a cell's content. The
report carries counts, the namespace prefixes a file declares, the
application that wrote it (`meta:generator`) and, for an anomaly, the path.

    python benchmarks/lxml_corpus_scan.py --limit 500
    python benchmarks/lxml_corpus_scan.py --corpus tests/wild
"""

import argparse
import io
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep_real_files import MAX_BYTES_DEFAULT, sample, spotlight_files, usable

# The prefixes the ODF specification writes, for the namespaces odsslicer reads.
CANONICAL = {
    "urn:oasis:names:tc:opendocument:xmlns:office:1.0": "office",
    "urn:oasis:names:tc:opendocument:xmlns:table:1.0": "table",
    "urn:oasis:names:tc:opendocument:xmlns:text:1.0": "text",
    "urn:oasis:names:tc:opendocument:xmlns:style:1.0": "style",
    "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0": "number",
    "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0": "fo",
    "urn:oasis:names:tc:opendocument:xmlns:meta:1.0": "meta",
    "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0": "draw",
    "urn:oasis:names:tc:opendocument:xmlns:of:1.2": "of",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://www.w3.org/1999/xlink": "xlink",
}
PARTS = ("content.xml", "styles.xml", "meta.xml")
OPENFORMULA = "urn:oasis:names:tc:opendocument:xmlns:of:1.2"
FORMULA = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}formula"
LANGUAGE = re.compile(r"([A-Za-z][\w.-]*):")


def declarations(markup: bytes) -> dict[str | None, str]:
    """Every namespace declaration in `markup`, prefix to URI - the root's
    and any declared further down."""
    found: dict[str | None, str] = {}
    for _, (prefix, uri) in etree.iterparse(io.BytesIO(markup), events=("start-ns",), recover=True):
        found.setdefault(prefix or None, uri)
    return found


def shape(root: etree._Element) -> list[tuple[str, tuple[tuple[str, str], ...], str, str]]:
    """The tree as names, attributes and text, prefixes resolved away."""
    return [
        (str(el.tag), tuple(sorted(el.attrib.items())), el.text or "", el.tail or "") for el in root.iter()
    ]


def scan(path: Path, counts: Counter[str], anomalies: list[str]) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            members = set(zf.namelist())
            parts = {name: zf.read(name) for name in PARTS if name in members}
    except (zipfile.BadZipFile, OSError, KeyError):
        counts["unreadable zip"] += 1
        return
    if "content.xml" not in parts:
        counts["no content.xml"] += 1
        return
    counts["files"] += 1
    generator = "?"
    if "meta.xml" in parts:
        meta = etree.fromstring(parts["meta.xml"], etree.XMLParser(recover=True))
        if meta is not None:
            found = meta.find(".//{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}generator")
            if found is not None and found.text:
                generator = found.text.split("/")[0].split("$")[0]
    counts[f"generator {generator}"] += 1
    for name, markup in parts.items():
        counts["parts"] += 1
        # 1. prefixes
        for prefix, uri in declarations(markup).items():
            expected = CANONICAL.get(uri)
            if expected is not None and prefix != expected:
                counts[f"{name}: {uri.rsplit(':', 2)[-2]} bound to {prefix!r}"] += 1
                anomalies.append(f"{path} {name}: {expected} -> {prefix!r} ({generator})")
        # 3. strictness
        try:
            etree.fromstring(markup, etree.XMLParser(huge_tree=True))
        except etree.XMLSyntaxError as exc:
            counts[f"{name}: rejected by a strict parser"] += 1
            recovered = etree.fromstring(markup, etree.XMLParser(recover=True, huge_tree=True))
            kept = "present" if recovered is not None else "absent"
            anomalies.append(
                f"{path} {name}: strict parse fails ({exc.msg[:60]}), recovered root {kept} ({generator})"
            )
            continue
        strict = etree.fromstring(markup, etree.XMLParser(huge_tree=True))
        if name == "content.xml":
            # 2. the prefix of each formula's language - the prefix only
            languages = Counter(
                (m.group(1) if (m := LANGUAGE.match(f)) else "none")
                for el in strict.iter()
                if (f := el.get(FORMULA)) is not None
            )
            bound = OPENFORMULA in declarations(markup).values()
            if languages:
                counts["content.xml: files with formulas"] += 1
                for language in languages:
                    counts[f"content.xml: formulas in {language!r}"] += 1
            if not bound:
                counts["content.xml: of unbound"] += 1
                if languages:
                    anomalies.append(f"{path}: formulas {dict(languages)} with of unbound ({generator})")
        recovered = etree.fromstring(markup, etree.XMLParser(recover=True, huge_tree=True))
        if shape(strict) != shape(recovered):
            counts[f"{name}: recover changes a well-formed tree"] += 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--corpus", help="scan this directory instead of the home directory")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--max-size", type=float, default=MAX_BYTES_DEFAULT / 1e6, help="MB")
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--paths", action="store_true", help="list the files behind each anomaly")
    args = parser.parse_args()

    max_bytes = int(args.max_size * 1e6)
    if args.corpus:
        candidates = sorted(p for p in Path(args.corpus).rglob("*.ods") if usable(p, max_bytes))
        files = sample(candidates, args.limit, max_bytes, args.seed)
    else:
        files = sample(spotlight_files(), args.limit, max_bytes, args.seed)
    counts: Counter[str] = Counter()
    anomalies: list[str] = []
    for n, path in enumerate(files, 1):
        scan(path, counts, anomalies)
        print(f"\r{n}/{len(files)} files", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    for key, value in sorted(counts.items()):
        print(f"{value:>6}  {key}")
    print(f"{len(anomalies):>6}  anomalies")
    if args.paths:
        for line in anomalies:
            print(f"        {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
