#!/usr/bin/env -S uv run --quiet --with pymupdf python
"""Extract a PDF into line-addressable text chunks + a geometry sidecar.

Every text line gets a stable id "<page>:<line>" (1-based page, 0-based line).
The chunk files are what you read; the sidecar (lines.json) is what
highlight.py uses to turn ids back into rectangles on the page.

Usage:
  extract.py BOOK.pdf --out WORKDIR [--max-chars 45000] [--columns 2]
                      [--pages 10-120] [--merge-rows]
                      [--no-figures] [--no-chrome-filter] [--no-margin-filter]
"""
import argparse, json, os, re, sys
from collections import Counter

import pymupdf


def parse_pages(spec, npages):
    if not spec:
        return list(range(npages))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < npages]


def norm(s):
    """Normalize for header/footer repetition detection (strip page numbers)."""
    return re.sub(r"\d+", "#", " ".join(s.split())).lower()


def line_records(page, ncols):
    d = page.get_text("dict")
    W = page.rect.width
    blocks = [b for b in d["blocks"] if b.get("type") == 0]
    if ncols > 1:
        blocks.sort(key=lambda b: (int(b["bbox"][0] // (W / ncols)), round(b["bbox"][1], 1)))
    else:
        blocks.sort(key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1)))
    recs = []
    for b in blocks:
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = " ".join("".join(s["text"] for s in ln["spans"]).split())
            if not text:
                continue
            recs.append({
                "t": text,
                "b": [round(v, 2) for v in (
                    min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                    max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans))],
                "sz": max(round(s["size"], 1) for s in spans),
                "bold": all((s["flags"] & 16) or "bold" in s["font"].lower() for s in spans),
            })
    return recs


def body_column(raw):
    """Left and right edge of the main text column, per page parity: books are
    routinely printed mirrored, with the outer margin (and its side-notes) on the
    right of odd pages and the left of even ones. One global column would miss
    half of them."""
    x0s = {0: Counter(), 1: Counter()}
    x1s = {0: Counter(), 1: Counter()}
    for p, recs in raw.items():
        for r in recs:
            if len(r["t"]) >= 40:
                x0s[p % 2][round(r["b"][0])] += 1
                x1s[p % 2][round(r["b"][2])] += 1
    cols = {}
    for par in (0, 1):
        if x0s[par]:
            cols[par] = (x0s[par].most_common(1)[0][0], x1s[par].most_common(1)[0][0])
    if not cols:
        return None
    for par in (0, 1):                      # a parity with no data borrows the other
        cols.setdefault(par, cols[1 - par])
    return cols


def merge_rows(recs, W):
    """Fold fragments that share a visual row into one record. Display equations
    are typeset as many one-symbol pieces on a single baseline; this makes each
    such row a single id. Changes ids, so it is opt-in."""
    body = [r for r in recs if not r.get("margin")]
    other = [r for r in recs if r.get("margin")]
    body.sort(key=lambda r: (r["b"][1], r["b"][0]))
    heights = sorted(r["b"][3] - r["b"][1] for r in body) or [10.0]
    med_h = heights[len(heights) // 2]
    groups = []
    for r in body:
        placed = False
        for g in groups:
            gy0, gy1 = g["y"]
            h = min(r["b"][3] - r["b"][1], gy1 - gy0)
            if min(gy1, r["b"][3]) - max(gy0, r["b"][1]) > 0.6 * max(h, 1):
                g["items"].append(r)
                g["y"] = (min(gy0, r["b"][1]), max(gy1, r["b"][3]))
                placed = True
                break
        if not placed:
            groups.append({"y": (r["b"][1], r["b"][3]), "items": [r]})
    out = []
    for g in groups:
        items = sorted(g["items"], key=lambda r: r["b"][0])
        x0 = min(r["b"][0] for r in items); x1 = max(r["b"][2] for r in items)
        y0 = min(r["b"][1] for r in items); y1 = max(r["b"][3] for r in items)
        # A chain of inline-maths fragments can otherwise stitch consecutive
        # prose lines into one id; a merged row stays roughly one line tall.
        if len(items) == 1 or x1 - x0 > 0.9 * W or y1 - y0 > 1.8 * med_h:
            out.extend(items)
            continue
        out.append({
            "t": " ".join(r["t"] for r in items),
            "b": [x0, y0, x1, y1],
            "sz": max(r["sz"] for r in items),
            "bold": all(r["bold"] for r in items),
            "margin": False,
        })
    return sorted(out + other, key=lambda r: (r["b"][1], r["b"][0]))


def figure_records(page, text_recs):
    """Vector-drawn formulas, diagrams and images have no text layer. Capture
    them as pseudo-lines so they can still be selected by id.

    Math is drawn as hundreds of glyph-level paths, so cluster into horizontal
    bands first (one band ~ one formula row), then merge along each band."""
    W, H = page.rect.width, page.rect.height
    boxes = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 1:
            boxes.append([*b["bbox"]])
    drawings = page.get_drawings()
    if len(drawings) <= 8000:
        for d in drawings:
            r = d["rect"]
            if r.width <= 0 or r.height <= 0:
                continue
            if r.width > 0.95 * W and r.height > 0.95 * H:
                continue
            boxes.append([r.x0, r.y0, r.x1, r.y1])
    if not boxes:
        return []

    boxes.sort(key=lambda b: b[1])
    bands = []
    for b in boxes:
        if bands and b[1] <= bands[-1][1] + 1 and b[3] >= bands[-1][0] - 1:
            bands[-1][0] = min(bands[-1][0], b[1])
            bands[-1][1] = max(bands[-1][1], b[3])
            bands[-1][2].append(b)
        else:
            bands.append([b[1], b[3], [b]])

    segs = []
    for _, _, members in bands:
        members.sort(key=lambda b: b[0])
        cur = list(members[0])
        for b in members[1:]:
            if b[0] <= cur[2] + 12:
                cur = [min(cur[0], b[0]), min(cur[1], b[1]),
                       max(cur[2], b[2]), max(cur[3], b[3])]
            else:
                segs.append(cur); cur = list(b)
        segs.append(cur)

    out = []
    for x0, y0, x1, y1 in segs:
        w, h = x1 - x0, y1 - y0
        if w < 8 or h < 6:
            continue
        if any(x0 <= (t["b"][0] + t["b"][2]) / 2 <= x1 and y0 <= (t["b"][1] + t["b"][3]) / 2 <= y1
               for t in text_recs):
            continue
        out.append({"t": f"[graphic {int(w)}x{int(h)}]",
                    "b": [round(v, 2) for v in (x0, y0, x1, y1)],
                    "sz": 0.0, "bold": False, "fig": True,
                    "show": w >= 60 and h >= 22})
    return out


def merge_figures(text_recs, figs):
    """Insert each figure at its vertical position without reordering text."""
    recs = list(text_recs)
    for f in sorted(figs, key=lambda r: r["b"][1]):
        pos = len(recs)
        for i, r in enumerate(recs):
            if r.get("fig"):
                continue
            if r["b"][1] > f["b"][1] + 2:
                pos = i
                break
        recs.insert(pos, f)
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True, help="work directory to create")
    ap.add_argument("--max-chars", type=int, default=45000, help="target chars per chunk file")
    ap.add_argument("--columns", type=int, default=1, help="column count for reading order")
    ap.add_argument("--pages", help="page range, 1-based, e.g. 10-120 or 1,5,9-20")
    ap.add_argument("--merge-rows", action="store_true",
                    help="fold same-row fragments (display equations) into one id")
    ap.add_argument("--no-figures", action="store_true",
                    help="ignore images and vector graphics (formulas, diagrams)")
    ap.add_argument("--no-chrome-filter", action="store_true",
                    help="keep running headers/footers in the chunk text")
    ap.add_argument("--no-margin-filter", action="store_true",
                    help="keep side-margin notes in the chunk text")
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    pages = parse_pages(a.pages, doc.page_count)
    os.makedirs(os.path.join(a.out, "text"), exist_ok=True)

    raw = {p: line_records(doc[p], a.columns) for p in pages}

    # Side-margin notes: keyword indexes and asides that sit outside the text
    # column. They are not reading material, so they are hidden from the chunk
    # text and left out of the character budget — but they keep their ids.
    cols = body_column(raw)
    n_margin = 0
    if cols and a.columns == 1 and not a.no_margin_filter:
        for p, recs in raw.items():
            bl, br = cols[p % 2]
            for r in recs:
                r["margin"] = r["b"][2] < bl - 6 or r["b"][0] > br + 6
                n_margin += bool(r["margin"])

    if a.merge_rows:
        for p in pages:
            raw[p] = merge_rows(raw[p], doc[p].rect.width)

    if not a.no_figures:
        for p in pages:
            raw[p] = merge_figures(raw[p], figure_records(doc[p], raw[p]))

    sizes = Counter(r["sz"] for recs in raw.values() for r in recs if not r.get("fig"))
    body = sizes.most_common(1)[0][0] if sizes else 10.0

    # Running headers/footers: multi-line in many books (copyright blocks,
    # title + page number), so candidates are lines near the top or bottom edge
    # whose text repeats across pages.
    edge = Counter(); cand = {}
    for p in pages:
        recs = raw[p]
        H = doc[p].rect.height
        idxs = set()
        for i, r in enumerate(recs):
            if r.get("fig"):
                continue
            yc = (r["b"][1] + r["b"][3]) / 2
            if ((yc < 0.15 * H or yc > 0.85 * H)
                    and (i < 3 or i >= len(recs) - 6) and len(r["t"]) < 120):
                idxs.add(i)
                edge[norm(r["t"])] += 1
        cand[p] = idxs
    thresh = 3   # body text almost never repeats verbatim at a page edge
    chrome_keys = {k for k, c in edge.items() if c >= thresh and k}

    lines, stats = {}, {"pages": {}}
    for p in pages:
        out = []
        for i, r in enumerate(raw[p]):
            r["chrome"] = (not a.no_chrome_filter and i in cand[p]
                           and norm(r["t"]) in chrome_keys)
            r["head"] = (not r["chrome"]) and (not r.get("fig")) and (
                r["sz"] >= body * 1.12 or (r["bold"] and len(r["t"]) < 90))
            out.append(r)
        lines[str(p + 1)] = out
        body_lines = [r for r in out
                      if not r["chrome"] and not r.get("fig") and not r.get("margin")]
        stats["pages"][p + 1] = {"lines": len(out), "body": len(body_lines),
                                 "chars": sum(len(r["t"]) for r in body_lines)}

    nfig = sum(1 for recs in lines.values() for r in recs if r.get("fig"))
    toc = [{"level": l, "title": t, "page": pg} for l, t, pg in doc.get_toc()]
    tocmap = {}
    for e in toc:
        tocmap.setdefault(e["page"], e["title"])

    breaks = {e["page"] for e in toc if e["level"] <= 2}
    chunks, cur, size = [], [], 0
    for p in pages:
        pchars = stats["pages"][p + 1]["chars"]
        if cur and ((p + 1) in breaks and size > a.max_chars * 0.35 or size + pchars > a.max_chars):
            chunks.append(cur); cur, size = [], 0
        cur.append(p); size += pchars
    if cur:
        chunks.append(cur)

    index = []
    for n, cp in enumerate(chunks, 1):
        name = f"chunk-{n:03d}.md"
        titles = [tocmap[p + 1] for p in cp if (p + 1) in tocmap]
        buf = [f"# {name}  pages {cp[0]+1}-{cp[-1]+1}"]
        if titles:
            buf.append("sections: " + " | ".join(titles[:8]))
        buf.append("")
        for p in cp:
            buf.append(f"--- p{p+1} ---")
            for i, r in enumerate(lines[str(p + 1)]):
                if r["chrome"] or r.get("margin") or (r.get("fig") and not r.get("show")):
                    continue
                mark = "§ " if r["head"] else ("▦ " if r.get("fig") else "")
                buf.append(f"{i}|{mark}{r['t']}")
            buf.append("")
        with open(os.path.join(a.out, "text", name), "w") as f:
            f.write("\n".join(buf))
        index.append({"file": f"text/{name}", "pages": [cp[0] + 1, cp[-1] + 1],
                      "chars": sum(stats["pages"][p + 1]["chars"] for p in cp),
                      "sections": titles})

    total_chars = sum(v["chars"] for v in stats["pages"].values())
    empty = sum(1 for p in pages if stats["pages"][p + 1]["body"] == 0)
    meta = {
        "pdf": os.path.abspath(a.pdf),
        "title": doc.metadata.get("title") or os.path.basename(a.pdf),
        "page_count": doc.page_count,
        "pages_extracted": len(pages),
        "body_font_size": body,
        "total_body_chars": total_chars,
        "total_body_lines": sum(v["body"] for v in stats["pages"].values()),
        "pages_without_text": empty,
        "figures": nfig,
        "margin_notes": n_margin,
        "columns": a.columns,
        "merge_rows": bool(a.merge_rows),
        "toc": toc,
        "chunks": index,
    }
    with open(os.path.join(a.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    with open(os.path.join(a.out, "lines.json"), "w") as f:
        json.dump({"lines": lines}, f, separators=(",", ":"))

    print(f"pages: {len(pages)}/{doc.page_count}   body lines: {meta['total_body_lines']}   "
          f"chars: {total_chars}   figures: {nfig}   margin notes: {n_margin}   "
          f"chunks: {len(chunks)}")
    if empty:
        print(f"WARNING: {empty} page(s) have no text layer — scanned PDF? "
              f"OCR first: ocrmypdf in.pdf out.pdf")
    if not toc:
        print("note: no embedded TOC; chunks split on size only")
    for c in index:
        print(f"  {c['file']}  p{c['pages'][0]}-{c['pages'][1]}  {c['chars']} chars"
              + (f"  [{c['sections'][0]}]" if c["sections"] else ""))


if __name__ == "__main__":
    main()
