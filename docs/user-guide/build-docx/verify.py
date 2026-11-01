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
DOCX = Path(sys.argv[1]) if len(sys.argv) > 1 else GUIDE / "Freejoy-User-Guide.docx"
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
CHECKS = 0


def check(ok: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
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
        malformed = []
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                try:
                    ET.fromstring(z.read(name))
                except ET.ParseError as error:
                    malformed.append(f"{name}: {error}")
        check(not malformed, f"every XML part is well formed {malformed}")
        if malformed:
            print(f"{len(failures)} check(s) failed")
            return 1
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

    # ---- images: figures (one per Markdown reference) and the pictures (slices) that make them up
    drawings = list(body.iter(q("w:drawing")))
    parent = {child: p for p in body.iter() for child in p}
    in_pair = set()
    for tbl in body.iter(q("w:tbl")):
        for d in tbl.iter(q("w:drawing")):
            in_pair.add(d)
    pictures = []
    for d in drawings:
        pr = d.find(".//wp:docPr", NS)
        rect = d.find(".//a:srcRect", NS)
        pictures.append({
            "d": d, "name": pr.get("name"), "descr": (pr.get("descr") or "").strip(),
            "cm": int(d.find(".//wp:extent", NS).get("cx")) / EMU_PER_CM,
            "hcm": int(d.find(".//wp:extent", NS).get("cy")) / EMU_PER_CM,
            "top": int((rect.get("t") if rect is not None else None) or 0),
            "bottom": int((rect.get("b") if rect is not None else None) or 0),
            "embed": d.find(".//a:blip", NS).get(q("r:embed")),
        })
    figures, problems, i = [], [], 0
    part_re = re.compile(r"^(.*) \(part (\d+) of (\d+)\)$")
    while i < len(pictures):
        first = pictures[i]
        m = part_re.match(first["descr"])
        if not m:
            figures.append([first])
            i += 1
            continue
        count = int(m.group(3))
        group = pictures[i:i + count]
        for k, pic in enumerate(group, start=1):
            mk = part_re.match(pic["descr"])
            ok = (mk and mk.group(1) == m.group(1) and int(mk.group(2)) == k and int(mk.group(3)) == count
                  and pic["name"] == first["name"] and pic["embed"] == first["embed"]
                  and abs(pic["top"] - round(100000 * (k - 1) / count)) <= 1
                  and abs(pic["bottom"] - round(100000 * (count - k) / count)) <= 1)
            if not ok:
                problems.append(f"{first['name']} slice {k}/{count}")
        figures.append(group)
        i += count
    sliced = [g for g in figures if len(g) > 1]
    print(f"Figures: {len(figures)} ({len(pictures)} pictures, {len(sliced)} figures cut into slices of the same media file)")
    check(len(figures) == len(exp_images), f"{len(figures)} figures in docx = {len(exp_images)} image references in Markdown")
    check(not problems, f"slices are numbered, contiguous crops of one embedded file {problems}")
    check([g[0]["name"] for g in figures] == [Path(s_).name for s_ in exp_images], "figure order and file names match the Markdown")
    check(all(p["descr"] for p in pictures), "every picture and slice has alt text")
    media_hashes = Counter(media.values())
    src_hashes = Counter(hashlib.sha256((GUIDE / s_).read_bytes()).hexdigest() for s_ in set(exp_images))
    check(set(media_hashes) == set(src_hashes), f"embedded pictures are byte-identical to images/ ({len(src_hashes)} distinct files)")
    too_wide, too_narrow, too_tall, unkept = [], [], [], []
    for pic in pictures:
        phone = pic["name"].endswith("-m.png")
        if pic["cm"] > (6.0 if phone else 16.0) + 0.01:
            too_wide.append((pic["name"], round(pic["cm"], 2)))
        minimum = 5.0 if phone else (None if pic["d"] in in_pair else 8.0)
        if minimum is not None and pic["cm"] < minimum - 0.01:
            too_narrow.append((pic["name"], round(pic["cm"], 2)))
        if pic["hcm"] > 22.01:
            too_tall.append((pic["name"], round(pic["hcm"], 1)))
        para = parent[parent[pic["d"]]]
        if para.find("w:pPr/w:keepNext", NS) is None:
            unkept.append(pic["name"])
    check(not too_wide, f"no picture exceeds its width limit (16 cm desktop, 6 cm phone) {too_wide}")
    check(not too_narrow, f"every phone picture is >= 5 cm wide, every desktop picture outside a pair >= 8 cm {too_narrow}")
    check(not too_tall, f"no picture or slice is taller than 22 cm (fits one page) {too_tall}")
    check(not unkept, f"every picture and slice paragraph is keepNext (stays with its caption) {unkept}")
    widths = sorted(p["cm"] for p in pictures if p["name"].endswith("-m.png"))
    print(f"      narrowest phone picture {widths[0]:.2f} cm; narrowest desktop picture outside a pair "
          f"{min(p['cm'] for p in pictures if not p['name'].endswith('-m.png') and p['d'] not in in_pair):.2f} cm")

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
    try:
        doc = docx.Document(str(DOCX))
    except Exception as error:  # any load failure is a failed check, not a crash
        check(False, f"python-docx can load the file: {error}")
    else:
        check(len(doc.inline_shapes) == len(pictures), f"python-docx loads it: {len(doc.paragraphs)} paragraphs, "
              f"{len(doc.tables)} tables, {len(doc.inline_shapes)} inline pictures, "
              f"{len(doc.sections)} section(s), core title '{doc.core_properties.title}'")
    print("\nNot checked here: appearance in Word, real page numbers (see README).")
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    if CHECKS == 0:
        print("\nNo checks ran")
        return 1
    print(f"\nAll {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
