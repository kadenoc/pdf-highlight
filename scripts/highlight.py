#!/usr/bin/env -S uv run --quiet --with pymupdf python
"""Apply a line-id selection to a PDF as real highlight annotations.

Reads the sidecar written by extract.py, so ids map back to exact rectangles —
no text re-matching, no fuzzy search.

Usage:
  highlight.py --work WORKDIR --spec sel.txt [--out BOOK-highlighted.pdf]
               [--plan highlights.md] [--dry-run] [--budget 0.30]

Spec (plain text, one selection per line; '#' comments ignored):
    12:3-9       core
    12:14.2-14.9 key    definition of a martingale
    13:1-4.6     ctx
Ranges are PAGE:LINE[.WORD][-LINE[.WORD]]; word indices trim the start of the
first line and the end of the last line. JSON is also accepted:
{"select": [{"r": "12:3-9", "t": "core", "n": "..."}, "13:1-4"]}
"""
import argparse, json, os, re, sys
from collections import Counter, defaultdict

import pymupdf

# Two colours only: green marks what a term means, yellow marks everything else
# worth reading. Opacity carries how important it is to read.
ROLES = {
    "def":   ((0.55, 0.92, 0.55), "definition / notation",
              "what a term or symbol means"),
    "claim": ((1.00, 0.92, 0.30), "the reading",
              "everything else worth reading"),
}
# prefix on the role: "!" essential, none normal, "~" supporting
OPACITY = {"!": 0.62, "": 0.40, "~": 0.20}
WEIGHTS = {"!": "essential", "": "worth reading", "~": "supporting"}
ALIAS = {
    "definition": "def", "notation": "def", "k": "def", "key": "def",
    # everything that is not a definition is simply "the reading"
    "result": "claim", "core": "claim", "c": "claim", "must": "claim",
    "cond": "claim", "caveat": "claim", "condition": "claim",
    "how": "claim", "method": "claim", "step": "claim", "gen": "claim",
    "ctx": "~claim", "context": "~claim", "skim": "~claim", "x": "~claim",
}

# PAGE:LINE[.WORD][-LINE[.WORD]]
RANGE_RE = re.compile(r"^(\d+):(\d+)(?:\.(\d+))?(?:-(\d+)(?:\.(\d+))?)?$")
BIG = 10**6


def parse_spec(path):
    raw = open(path).read()
    items = []
    if raw.lstrip()[:1] in "{[":
        data = json.loads(raw)
        seq = data["select"] if isinstance(data, dict) else data
        for it in seq:
            if isinstance(it, str):
                items.append((it, "core", ""))
            else:
                items.append((it["r"], it.get("t", "core"), it.get("n", "")))
        return items
    for ln in raw.splitlines():
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        parts = ln.split(None, 2)
        items.append((parts[0], parts[1] if len(parts) > 1 else "core",
                      parts[2] if len(parts) > 2 else ""))
    return items


def expand(items, lines, errors, warnings):
    """-> {page: {line_idx: [span, ...]}}; a span is one highlighted stretch of
    that line. A line can carry several, so two selections that touch the same
    line no longer clobber each other."""
    sel = defaultdict(lambda: defaultdict(list))
    for r, t, n in items:
        tok = t.lower().strip()
        weight = tok[0] if tok[:1] in "!~" else ""
        tok = ALIAS.get(tok.lstrip("!~"), tok.lstrip("!~"))
        if tok[:1] in "!~":              # an alias may itself carry a weight
            weight, tok = tok[0] if not weight else weight, tok.lstrip("!~")
        t = tok
        if t not in ROLES:
            errors.append(f"unknown role {t!r} in {r!r} "
                          f"(use def/claim; prefix ! essential or ~ supporting)")
            continue
        m = RANGE_RE.match(r)
        if not m:
            errors.append(f"bad range {r!r} (want PAGE:LINE[.WORD][-LINE[.WORD]])")
            continue
        page, lo = int(m.group(1)), int(m.group(2))
        w0 = int(m.group(3)) if m.group(3) else None
        hi = int(m.group(4)) if m.group(4) else lo
        w1 = int(m.group(5)) if m.group(5) else None
        recs = lines.get(str(page))
        if recs is None:
            errors.append(f"page {page} was not extracted (see meta.json pages)")
            continue
        if hi < lo:
            errors.append(f"reversed range {r!r}")
            continue
        for i in range(lo, hi + 1):
            if i >= len(recs):
                errors.append(f"{page}:{i} out of range (page has {len(recs)} lines)")
                continue
            if hi > lo and recs[i].get("margin"):
                continue          # a range crossing a side-note skips over it
            span = {"role": t, "weak": weight, "note": n,
                    "w0": w0 if i == lo else None,
                    "w1": w1 if i == hi else None}
            here = sel[page][i]
            a0, a1 = span["w0"] or 0, span["w1"] if span["w1"] is not None else BIG
            for other in here:
                b0 = other["w0"] or 0
                b1 = other["w1"] if other["w1"] is not None else BIG
                if a0 <= b1 and b0 <= a1:
                    warnings.append(f"{page}:{i} selected twice by overlapping ranges")
                    break
            here.append(span)
    return sel


def span_text(rec, span):
    if span["w0"] is None and span["w1"] is None:
        return rec["t"]
    toks = rec["t"].split()
    return " ".join(toks[(span["w0"] or 0):
                         (span["w1"] + 1 if span["w1"] is not None else None)])


def line_words(page, rect, cache):
    """Words of one text line, left to right, excluding margin notes that sit on
    the same visual row."""
    if page.number not in cache:
        cache[page.number] = page.get_text("words")
    x0, y0, x1, y1 = rect
    h = y1 - y0
    out = [w for w in cache[page.number]
           if min(y1, w[3]) - max(y0, w[1]) > 0.5 * min(h, w[3] - w[1])
           and w[0] >= x0 - 2 and w[2] <= x1 + 2]
    out.sort(key=lambda w: w[0])
    return out


STOP = {"the","and","that","this","with","from","which","have","been","are","was",
        "for","not","but","can","its","our","their","when","then","than","into",
        "such","also","only","some","each","they","them","these","those","where",
        "what","how","all","any","one","two","use","used","using","see","section"}
# Openers that point at a premise. "there"/"here" are excluded: "There are three
# concepts…" introduces rather than refers back.
CONNECTIVE = {"therefore","thus","hence","consequently","moreover","however",
              "this","these","those","it","they"}


def paragraphs(sel, lines):
    """Merge consecutive lines of one selection into whole spans, the way the
    digest renders them. -> [(page, first_idx, text, role, weight, note)]"""
    out = []
    for p in sorted(sel):
        recs = lines[str(p)]
        for run in runs(sel[p], recs):
            group, key, first = [], None, None
            def flush():
                if group:
                    txt = ""
                    for piece in group:
                        if not txt:
                            txt = piece
                        elif txt.endswith("-") and piece[:1].islower():
                            txt = txt[:-1] + piece
                        else:
                            txt += " " + piece
                    out.append((p, first, txt, key[0], key[1], key[2]))
                    group.clear()
            for i in run:
                rec = recs[i]
                if rec.get("fig") and not rec.get("show"):
                    continue
                for sp in sel[p][i]:
                    t = span_text(rec, sp)
                    if not t:
                        continue
                    k = (sp["role"], sp["weak"], sp["note"])
                    if k != key:
                        flush(); key = k; first = i
                    group.append(t)
            flush()
    return out


def audit(sel, lines, meta):
    """Checks derived from blind-reader reviews of real output: the defects that
    actually cost a reader understanding."""
    paras = paragraphs(sel, lines)
    frag, tiny, orphan, dup, uncovered = [], [], [], [], []

    for p, i, txt, role, w, note in paras:
        words = txt.split()
        alpha = sum(c.isalpha() or c.isspace() for c in txt) / max(len(txt), 1)
        if len(words) < 5 or alpha < 0.5:
            tiny.append((p, i, txt[:60]))
            continue
        if not re.search(r"[.!?:;\)\]]\s*$", txt) or (txt[:1].islower() and not txt[:1].isdigit()):
            frag.append((p, i, txt[:40] + " … " + txt[-40:]))
        head = re.sub(r"[^a-z]", "", words[0].lower())
        if head in CONNECTIVE and (i - 1) not in sel[p]:
            orphan.append((p, i, " ".join(words[:8])))

    # near-duplicate propositions, compared within a chapter
    tops = [e["page"] for e in meta["toc"] if e["level"] <= 2] or [1]
    def chapter(pg):
        c = 0
        for t in tops:
            if pg >= t:
                c = t
        return c
    buckets = defaultdict(list)
    for p, i, txt, role, w, note in paras:
        ws = {x for x in re.findall(r"[a-z]{4,}", txt.lower()) if x not in STOP}
        if len(ws) >= 6:
            buckets[chapter(p)].append((p, i, txt, ws))
    for items in buckets.values():
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                wa, wb = items[a][3], items[b][3]
                j = len(wa & wb) / len(wa | wb)
                if j > 0.6:
                    dup.append((items[a][0], items[a][1], items[b][0], items[b][1],
                                items[a][2][:50]))
    # terms the author flagged in the margin, with no highlight naming them nearby
    hl_by_page = defaultdict(str)
    for p, i, txt, role, w, note in paras:
        hl_by_page[p] += " " + txt.lower()
    for p_str, recs in lines.items():
        p = int(p_str)
        for r in recs:
            if not r.get("margin"):
                continue
            term = r["t"].strip().lower()
            if not (2 < len(term) < 40) or not re.fullmatch(r"[a-z][a-z \-/']+", term):
                continue
            near = hl_by_page[p] + hl_by_page.get(p + 1, "") + hl_by_page.get(p - 1, "")
            if term not in near:
                uncovered.append((p, r["t"].strip()))

    def show(name, rows, fmt, why):
        if not rows:
            return
        print(f"  {name}: {len(rows)}   ({why})")
        for row in rows[:3]:
            print(f"      p{row[0]}  {fmt(row)}")

    print("\naudit:")
    show("fragments", frag, lambda r: r[2],
         "starts or ends mid-sentence - extend to the sentence edge or drop")
    show("scraps", tiny, lambda r: r[2],
         "under 5 words or mostly symbols - no standalone meaning")
    show("orphan connectives", orphan, lambda r: r[2],
         "opens on a back-reference whose premise is not highlighted")
    show("repeats", dup, lambda r: f"~ p{r[2]}:{r[3]}  {r[4]}",
         "same proposition highlighted twice - keep the sharper one")
    show("unclaimed margin terms", uncovered, lambda r: r[1],
         "author flagged the term; no highlight nearby names it")
    if not any((frag, tiny, orphan, dup, uncovered)):
        print("  clean")


def body_chars_of(lines, p):
    recs = lines.get(str(p), [])
    return sum(len(r["t"]) for r in recs
               if not r.get("chrome") and not r.get("fig") and not r.get("margin"))


def add_legend(doc, meta, lines, page_chars, sel, cov, sel_chars):
    """Prepend a reading map: what each colour means and how much of each
    chapter is highlighted."""
    # Front matter is often a different trim size than the body; match the body.
    sizes = Counter((round(pg.rect.width), round(pg.rect.height)) for pg in doc)
    w, h = sizes.most_common(1)[0][0]
    ref = pymupdf.Rect(0, 0, w, h)
    page = doc.new_page(0, width=w, height=h)
    M = 62
    y = 92
    page.insert_text((M, y), meta["title"][:70], fontsize=19, fontname="hebo")
    y += 22
    page.insert_text((M, y), "Reading map", fontsize=13, fontname="helv",
                     color=(0.35, 0.35, 0.35))
    y += 30
    hours_all, hours_hl = 0, 0
    if meta["total_body_chars"]:
        hours_all = meta["total_body_chars"] / 1100 / 60      # ~1100 chars/min
        hours_hl = sel_chars / 1100 / 60
    page.insert_text((M, y), f"{100*cov:.0f}% of the body text is highlighted.",
                     fontsize=10.5, fontname="helv")
    y += 15
    page.insert_text((M, y), f"About {hours_hl:.0f}h of reading instead of {hours_all:.0f}h, "
                             f"at a steady prose pace - maths runs slower.",
                     fontsize=10.5, fontname="helv", color=(0.35, 0.35, 0.35))
    y += 34

    page.insert_text((M, y), "Green is a definition, yellow is the reading",
                     fontsize=11, fontname="hebo")
    y += 18
    for role, (rgb, name, blurb) in ROLES.items():
        page.draw_rect(pymupdf.Rect(M, y - 8.5, M + 26, y + 3),
                       color=None, fill=rgb, fill_opacity=OPACITY[""])
        page.insert_text((M + 36, y), name, fontsize=10, fontname="hebo")
        page.insert_text((M + 168, y), blurb, fontsize=10, fontname="helv",
                         color=(0.3, 0.3, 0.3))
        y += 19
    y += 12

    page.insert_text((M, y), "Depth of colour is how important it is to read",
                     fontsize=11, fontname="hebo")
    y += 18
    for w in ("!", "", "~"):
        page.draw_rect(pymupdf.Rect(M, y - 8.5, M + 26, y + 3),
                       color=None, fill=ROLES["claim"][0], fill_opacity=OPACITY[w])
        page.insert_text((M + 36, y), WEIGHTS[w], fontsize=10, fontname="hebo")
        y += 19
    y += 2
    page.insert_text((M + 36, y), "Notes on a highlight appear in your reader's annotation sidebar.",
                     fontsize=9.5, fontname="helv", color=(0.45, 0.45, 0.45))
    y += 34

    tops = [e for e in meta["toc"] if e["level"] <= 2]
    if tops:
        page.insert_text((M, y), "Where the reading is", fontsize=11, fontname="hebo")
        y += 17
        bounds = []
        for i, e in enumerate(tops):
            end = tops[i + 1]["page"] - 1 if i + 1 < len(tops) else meta["page_count"]
            # Exercises / Further Reading belong to the chapter above them.
            if re.match(r"^(exercises|further reading)$", e["title"].strip(), re.I) and bounds:
                bounds[-1] = (bounds[-1][0], bounds[-1][1], end)
                continue
            bounds.append((e["title"], e["page"], end))
        for title, a0, b0 in bounds:
            tot = sum(page_chars.get(p, 0) for p in range(a0, b0 + 1))
            if tot < 400:
                continue
            got = sum(sel.get(p, 0) for p in range(a0, b0 + 1))
            pct = 100 * got / tot
            if y > ref.height - 70:
                page.insert_text((M, y), "…", fontsize=10, fontname="helv")
                break
            page.insert_text((M, y), title[:52], fontsize=9.5, fontname="helv")
            page.insert_text((M + 300, y), f"p{a0}-{b0}", fontsize=9.5, fontname="helv",
                             color=(0.45, 0.45, 0.45))
            bw = 90 * min(pct, 100) / 100
            page.draw_rect(pymupdf.Rect(M + 370, y - 7, M + 460, y + 1),
                           color=None, fill=(0.91, 0.91, 0.91))
            page.draw_rect(pymupdf.Rect(M + 370, y - 7, M + 370 + bw, y + 1),
                           color=None, fill=(0.45, 0.45, 0.45))
            page.insert_text((M + 468, y), f"{pct:.0f}%", fontsize=9.5, fontname="helv")
            y += 15
    page.insert_text((M, ref.height - 46),
                     "Generated by pdf-highlight. This page was added in front, so PDF page "
                     "numbers run one ahead of the original.",
                     fontsize=8, fontname="helv", color=(0.55, 0.55, 0.55))


def runs(indices, recs=None):
    def bridged(a, b):
        if recs is None:
            return False
        return all(recs[k].get("margin") or recs[k].get("chrome")
                   for k in range(a + 1, b))
    out, cur = [], []
    for i in sorted(indices):
        if cur and (i == cur[-1] + 1 or bridged(cur[-1], i)):
            cur.append(i)
        else:
            if cur:
                out.append(cur)
            cur = [i]
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", help="output PDF (default: <input>-highlighted.pdf)")
    ap.add_argument("--plan", help="also write a highlights-only markdown digest")
    ap.add_argument("--dry-run", action="store_true", help="validate + report coverage only")
    ap.add_argument("--budget", type=float, default=0.30, help="warn above this char coverage")
    ap.add_argument("--no-legend", action="store_true",
                    help="do not prepend the legend / reading-map page")
    a = ap.parse_args()

    meta = json.load(open(os.path.join(a.work, "meta.json")))
    lines = json.load(open(os.path.join(a.work, "lines.json")))["lines"]

    errors, warnings = [], []
    sel = expand(parse_spec(a.spec), lines, errors, warnings)
    if errors:
        print(f"SPEC ERRORS ({len(errors)}):", file=sys.stderr)
        for e in errors[:40]:
            print("  " + e, file=sys.stderr)
        if len(errors) > 40:
            print(f"  ... {len(errors)-40} more", file=sys.stderr)
        sys.exit(2)

    # Character counts follow the trimmed spans, not whole lines.
    def page_chars(p, idxs=None):
        tot = 0
        for i, spans in sel[p].items():
            if idxs is not None and i not in idxs:
                continue
            rec = lines[str(p)][i]
            if rec.get("fig"):
                continue
            for s in spans:
                tot += len(span_text(rec, s))
        return tot

    sel_chars = sum(page_chars(p) for p in sel)
    sel_lines = sum(len(d) for d in sel.values())
    n_spans = sum(len(v) for d in sel.values() for v in d.values())
    tot_chars = meta["total_body_chars"] or 1
    cov = sel_chars / tot_chars

    print(f"selected {sel_lines} lines / {n_spans} spans / {sel_chars} chars "
          f"= {100*cov:.1f}% of body text  (budget {100*a.budget:.0f}%)")
    by_tier = defaultdict(int)
    for p, d in sel.items():
        for i, spans in d.items():
            rec = lines[str(p)][i]
            if rec.get("fig"):
                continue
            for s in spans:
                by_tier[s["role"]] += len(span_text(rec, s))
    for t in ROLES:
        if by_tier[t]:
            print(f"  {t:5s} {100*by_tier[t]/tot_chars:5.1f}%  {ROLES[t][1]}")
    unlabelled = sum(1 for p, d in sel.items() for spans in d.values()
                     for s in spans if s["role"] == "def" and not s["note"])
    if unlabelled:
        print(f"  note: {unlabelled} definition span(s) carry no label — a label "
              f"becomes a sidebar entry in the reader, so name the term")
    chrome_hits = sum(1 for p, d in sel.items() for i in d if lines[str(p)][i].get("chrome"))
    if chrome_hits:
        print(f"  note: {chrome_hits} selected line(s) are running headers/footers")
    for w in dict.fromkeys(warnings):
        print(f"  warning: {w}")

    print("\nper-chunk coverage:")
    for c in meta["chunks"]:
        lo, hi = c["pages"]
        s = sum(page_chars(p) for p in range(lo, hi + 1) if p in sel)
        pct = 100 * s / (c["chars"] or 1)
        flag = ("  <-- nothing selected" if s == 0 else
                "  <-- over budget" if pct > 100 * a.budget * 1.5 else "")
        title = (c["sections"][0][:38] if c["sections"] else "")
        print(f"  {os.path.basename(c['file']):14s} p{lo}-{hi:<5} {pct:5.1f}%  {title}{flag}")

    if cov > a.budget:
        print(f"\nOVER BUDGET: {100*cov:.1f}% > {100*a.budget:.0f}%. Cut the weakest 'core' "
              f"lines or demote them to ctx.")
    if cov < 0.05:
        print(f"\nSUSPICIOUSLY THIN: {100*cov:.1f}%. Check for skipped chunks above.")

    audit(sel, lines, meta)

    if a.plan:
        tocmap = {}
        for e in meta["toc"]:
            tocmap.setdefault(e["page"], e["title"])
        buf = [f"# {meta['title']} — essential reading",
               f"\n{sel_lines} lines, {sel_chars} chars, {100*cov:.1f}% of the book. "
               f"`**bold**` = key definition/result, `>` = context.\n"]
        last_page = None
        for pg, i, txt, role, weight, note in paragraphs(sel, lines):
            if pg != last_page:
                if pg in tocmap:
                    buf.append(f"\n## {tocmap[pg]}")
                buf.append(f"\n*p{pg}*")
                last_page = pg
            if role == "def":
                txt = f"**{txt}**"
            if weight == "~":
                txt = f"> {txt}"
            if note:
                txt += f"  <!-- {note} -->"
            buf.append(txt)
        with open(a.plan, "w") as f:
            f.write("\n".join(buf) + "\n")
        print(f"\nwrote {a.plan}")

    if a.dry_run:
        print("\ndry run — no PDF written")
        return

    out = a.out or re.sub(r"\.pdf$", "", meta["pdf"], flags=re.I) + "-highlighted.pdf"
    doc = pymupdf.open(meta["pdf"])
    wcache = {}
    n_annot = 0
    for p in sorted(sel):
        page = doc[p - 1]
        recs = lines[str(p)]
        for run in runs(sel[p], recs):
            by_tier_quads = defaultdict(list)
            notes = defaultdict(list)
            for i in run:
                for s in sel[p][i]:
                    x0, y0, x1, y1 = recs[i]["b"]
                    trimmed = s["w0"] is not None or s["w1"] is not None
                    if not recs[i].get("fig") and trimmed:
                        ws = line_words(page, recs[i]["b"], wcache)
                        if s["w0"] is not None and s["w0"] < len(ws):
                            x0 = ws[s["w0"]][0]
                        if s["w1"] is not None and s["w1"] < len(ws):
                            x1 = ws[s["w1"]][2]
                    if not recs[i].get("fig"):
                        # Inline formulas and glyph art sit on the line but outside
                        # its text bbox; pull them in. A trimmed span only absorbs
                        # graphics that fall inside the words it kept.
                        for g in recs:
                            if not g.get("fig"):
                                continue
                            gx0, gy0, gx1, gy1 = g["b"]
                            if min(y1, gy1) - max(y0, gy0) <= 0.55 * (gy1 - gy0):
                                continue
                            inside = (gx0 >= x0 - 2 and gx1 <= x1 + 2) if trimmed \
                                else (gx0 < x1 + 40 and gx1 > x0 - 40)
                            if inside:
                                x0, y0 = min(x0, gx0), min(y0, gy0)
                                x1, y1 = max(x1, gx1), max(y1, gy1)
                    style = (s["role"], s["weak"])
                    by_tier_quads[style].append(
                        pymupdf.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1).quad)
                    if s["note"]:
                        notes[style].append(s["note"])
            for style, quads in by_tier_quads.items():
                role, weak = style
                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=ROLES[role][0])
                if notes[style]:
                    annot.set_info(content="; ".join(dict.fromkeys(notes[style])),
                                   title=f"pdf-highlight · {role}")
                annot.set_opacity(OPACITY[weak])
                annot.update()
                n_annot += 1
    if not a.no_legend:
        add_legend(doc, meta, lines,
                   {p: body_chars_of(lines, p) for p in range(1, meta["page_count"] + 1)},
                   {p: page_chars(p) for p in sel}, cov, sel_chars)
    doc.save(out, garbage=3, deflate=True)
    print(f"\nwrote {out}  ({n_annot} annotations on {len(sel)} pages)")


if __name__ == "__main__":
    main()
