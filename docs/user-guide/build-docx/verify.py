"""Checks Freejoy-User-Guide.docx against the Markdown sources. Read-only.

Run from docs/user-guide/build-docx (nothing is added to any project manifest):

    uv run --no-project --with python-docx==1.2.0 --with markdown-it-py==3.0.0 verify.py

The sources are parsed here with a different Markdown parser (markdown-it-py) than the build
uses (markdown-it), so the counts are an independent cross-check.
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import docx  # python-docx
from markdown_it import MarkdownIt

GUIDE = Path(__file__).resolve().parent.parent
DOCX = GUIDE / "Freejoy-User-Guide.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
NS = {"w": W, "r": R, "wp": WP, "a": A, "pic": PIC}
EMU_PER_CM = 360000

PARTS = {
    "visitors": [
        "01-home-and-navigation.md", "02-browse-and-search.md", "03-offer-details.md",
        "04-email-alerts.md", "05-information-pages.md", "06-keyboard-and-accessibility.md",
    ],
    "admin": [
        "admin-01-signing-in.md", "admin-02-offers.md", "admin-03-categories-and-merchants.md",
        "admin-04-users-and-2fa.md", "admin-05-subscriptions-and-analytics.md",
        "admin-06-audit-logs-seo-settings.md",
    ],
}
failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(("PASS  " if ok else "FAIL  ") + message)
    if not ok:
        failures.append(message)


def q(tag: str) -> str:
    prefix, name = tag.split(":")
    return f"{{{NS[prefix]}}}{name}"


def inline_text(token) -> str:
    return "".join(c.content for c in (token.children or []) if c.type in ("text", "code_inline")).strip()


def parse_sources():
    """Headings (level, text), image srcs, and links, in document order, from the .md files."""
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    headings, images, links = [], [], []

    def walk(name: str, shift: int, only_h: bool = False):
        tokens = md.parse((GUIDE / name).read_text(encoding="utf-8"))
        for i, t in enumerate(tokens):
            if t.type == "heading_open":
                headings.append((int(t.tag[1]) + shift, inline_text(tokens[i + 1])))
            elif t.type == "inline":
                for c in t.children or []:
                    if c.type == "image":
                        images.append(c.attrs["src"])
                    elif c.type == "link_open":
                        links.append((name, c.attrs["href"]))

    # README: preface heading + its H3s; part sections only contribute their table links.
    tokens = md.parse((GUIDE / "README.md").read_text(encoding="utf-8"))
    section = None
    for i, t in enumerate(tokens):
        if t.type == "heading_open" and t.tag == "h2":
            section = inline_text(tokens[i + 1])
            if section == "Please read this first":
                headings.append((1, section))
            elif section.startswith("For "):
                n = 1 if "visitors" in section else 2
                headings.append((1, f"Part {n}. {section}"))
        elif t.type == "heading_open" and t.tag == "h3" and section == "Please read this first":
            headings.append((2, inline_text(tokens[i + 1])))
        elif t.type == "inline" and section != "Developer documentation" and not inline_text(t).startswith("Word version:"):
            for c in t.children or []:
                if c.type == "link_open":
                    links.append(("README.md", c.attrs["href"]))
    # Order: preface, part 1 heading, part 1 chapters, part 2 heading, part 2 chapters.
    pre = [h for h in headings if not h[1].startswith("Part ")]
    part1 = [h for h in headings if h[1].startswith("Part 1.")]
    part2 = [h for h in headings if h[1].startswith("Part 2.")]
    headings.clear()
    headings.extend(pre + part1)
    for name in PARTS["visitors"]:
        walk(name, 0)
    headings.extend(part2)
    for name in PARTS["admin"]:
        walk(name, 0)
    return headings, images, links


def main() -> int:
    if not DOCX.exists():
        print(f"Missing {DOCX}; run npm run build first")
        return 1
    size = DOCX.stat().st_size
    print(f"File: {DOCX} ({size / 1048576:.2f} MB, {size} bytes)")

    with zipfile.ZipFile(DOCX) as z:
        check(z.testzip() is None, "zip container is intact")
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(z.read(name))
        check(True, "every XML part is well formed")
        document = ET.fromstring(z.read("word/document.xml"))
        settings = z.read("word/settings.xml").decode("utf8")
        header_xml = "".join(z.read(n).decode("utf8") for n in z.namelist() if re.match(r"word/header\d*\.xml", n))
        footer_xml = "".join(z.read(n).decode("utf8") for n in z.namelist() if re.match(r"word/footer\d*\.xml", n))
        media = {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith("word/media/")}
        rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    body = document.find("w:body", NS)

    # ---- headings
    docx_headings = []
    for p in body.iter(q("w:p")):
        style = p.find("w:pPr/w:pStyle", NS)
        if style is not None and style.get(q("w:val")) in ("Heading1", "Heading2", "Heading3", "Heading4"):
            level = int(style.get(q("w:val"))[-1])
            text = "".join(t.text or "" for t in p.iter(q("w:t")))
            docx_headings.append((level, text))
    exp_headings, exp_images, exp_links = parse_sources()
    by_level = Counter(level for level, _ in docx_headings)
    print(f"Headings in docx: {len(docx_headings)} {dict(sorted(by_level.items()))}")
    check(docx_headings == exp_headings, f"heading levels and text match the Markdown ({len(exp_headings)} expected, in order)")

    # ---- bookmarks and links
    bookmarks = [b.get(q("w:name")) for b in body.iter(q("w:bookmarkStart"))]
    ids_start = sorted(int(b.get(q("w:id"))) for b in body.iter(q("w:bookmarkStart")))
    ids_end = sorted(int(b.get(q("w:id"))) for b in body.iter(q("w:bookmarkEnd")))
    check(len(bookmarks) == len(set(bookmarks)), f"{len(bookmarks)} bookmarks, names unique")
    check(ids_start == ids_end and len(set(ids_start)) == len(ids_start), "bookmark start/end ids pair up")
    check(all(len(n) <= 40 for n in bookmarks), "bookmark names are within Word's 40 character limit")
    check(len(docx_headings) + 1 == len(bookmarks), "one bookmark per heading plus the Contents bookmark")
    anchors = [h.get(q("w:anchor")) for h in body.iter(q("w:hyperlink")) if h.get(q("w:anchor"))]
    external = [h for h in body.iter(q("w:hyperlink")) if h.get(q("r:id"))]
    missing = sorted({a for a in anchors if a not in set(bookmarks)})
    check(not missing, f"{len(anchors)} internal hyperlinks, every anchor resolves to a bookmark {missing}")
    toc_entries = sum(1 for level, _ in docx_headings if level <= 3)
    guide_links = [
        l for l in exp_links
        if not l[1].startswith(("http", "mailto")) and not l[1].startswith("../")
    ]
    plain_links = [l for l in exp_links if l[1].startswith("../")]
    # internal links = Markdown page links + TOC entries + 1 title-page link to the legal chapter
    expected_internal = len(guide_links) + toc_entries + 1
    check(len(anchors) == expected_internal,
          f"internal hyperlink count {len(anchors)} = {len(guide_links)} Markdown page links "
          f"+ {toc_entries} TOC entries + 1 title-page link")
    print(f"      {len(external)} external hyperlinks; {len(plain_links)} links to developer docs shown as plain text")

    # ---- fields
    instr = [t.text or "" for t in body.iter(q("w:instrText"))]
    simple = [f.get(q("w:instr")) for f in body.iter(q("w:fldSimple"))]
    toc = [i for i in instr if i.strip().startswith("TOC")]
    check(len(toc) == 1 and '\\o "1-3"' in toc[0] and "\\h" in toc[0], f"one real TOC field: {toc}")
    check("<w:updateFields" in settings, "settings.xml asks Word to update fields on open")
    check(sum(1 for f in simple if f.startswith("SEQ Figure")) == len(exp_images), "one SEQ Figure field per image")
    check("PAGE" in footer_xml and "NUMPAGES" in footer_xml, "footer has PAGE and NUMPAGES fields")
    check("STYLEREF" in header_xml, "header has a STYLEREF (current chapter) field")
    ends = [f for f in body.iter(q("w:fldChar")) if f.get(q("w:fldCharType")) == "end"]
    begins = [f for f in body.iter(q("w:fldChar")) if f.get(q("w:fldCharType")) == "begin"]
    check(len(ends) == len(begins), "field begin/end characters balance")

    # ---- images
    drawings = list(body.iter(q("w:drawing")))
    docpr = [d.find(".//wp:docPr", NS) for d in drawings]
    names = [d.get("name") for d in docpr]
    check(len(drawings) == len(exp_images), f"{len(drawings)} images in docx = {len(exp_images)} image references in Markdown")
    check(names == [Path(s).name for s in exp_images], "image order and file names match the Markdown")
    check(all((d.get("descr") or "").strip() for d in docpr), "every image has alt text")
    media_hashes = Counter(media.values())
    src_hashes = Counter(hashlib.sha256((GUIDE / s).read_bytes()).hexdigest() for s in set(exp_images))
    check(set(media_hashes) == set(src_hashes), f"embedded pictures are byte-identical to images/ ({len(src_hashes)} distinct files)")
    too_wide = []
    for d, name in zip(drawings, names):
        ext = d.find(".//wp:extent", NS)
        cm = int(ext.get("cx")) / EMU_PER_CM
        limit = 6.0 if name.endswith("-m.png") else 16.0
        if cm > limit + 0.01:
            too_wide.append((name, round(cm, 2)))
    check(not too_wide, f"no picture exceeds its width limit (16 cm desktop, 6 cm phone) {too_wide}")
    tall = [(n, round(int(d.find('.//wp:extent', NS).get('cy')) / EMU_PER_CM, 1)) for d, n in zip(drawings, names)]
    check(all(h <= 22.01 for _, h in tall), "no picture is taller than 22 cm (fits one page)")

    # ---- tables, captions, styles
    tables = list(body.iter(q("w:tbl")))
    pair_tables = [t for t in tables if t.find(".//w:drawing", NS) is not None]
    print(f"Tables: {len(tables)} ({len(pair_tables)} side-by-side image pairs, {len(tables) - len(pair_tables)} data tables)")
    captions = [p for p in body.iter(q("w:p")) if (p.find("w:pPr/w:pStyle", NS) is not None
                and p.find("w:pPr/w:pStyle", NS).get(q("w:val")) == "Caption")]
    check(len(captions) == len(exp_images), f"{len(captions)} Caption paragraphs")
    check(all("Figure " in "".join(t.text or "" for t in c.iter(q("w:t"))) for c in captions), "captions start with 'Figure '")
    check(sum(1 for p in body.iter(q("w:p")) if p.find("w:pPr/w:keepNext", NS) is not None) > len(exp_images),
          "keepNext is set on picture paragraphs and the text before them")

    # ---- python-docx load
    doc = docx.Document(str(DOCX))
    check(len(doc.inline_shapes) == len(exp_images), f"python-docx loads it: {len(doc.paragraphs)} paragraphs, "
          f"{len(doc.tables)} tables, {len(doc.inline_shapes)} inline pictures, "
          f"{len(doc.sections)} section(s), core title '{doc.core_properties.title}'")
    print("\nNot checked here: appearance in Word, real page numbers (see README).")
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nAll checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
