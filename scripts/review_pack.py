#!/usr/bin/env -S uv run --quiet --with pymupdf python
"""Build a blind-review pack for one section: the highlights alone, the full
source of the same pages, and the honestly measured coverage.

    review_pack.py --work WORKDIR --spec sel.txt --pages 17-22 --out DIR

Writes DIR/digest.md and DIR/source.md and prints the line to paste into the
reviewer's prompt. Slicing a generated highlights.md by hand gets this wrong:
chunk boundaries move between extractions, and the digest's markdown markers and
labels inflate a raw character ratio well above the real coverage.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from highlight import parse_spec, expand, paragraphs, span_text   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--pages", required=True, help="page range, e.g. 17-22")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    lo, hi = (int(x) for x in a.pages.split("-"))
    meta = json.load(open(os.path.join(a.work, "meta.json")))
    lines = json.load(open(os.path.join(a.work, "lines.json")))["lines"]

    errors, warnings = [], []
    sel = expand(parse_spec(a.spec), lines, errors, warnings)
    if errors:
        print(f"spec has {len(errors)} error(s); fix them first", file=sys.stderr)
        sys.exit(2)

    os.makedirs(a.out, exist_ok=True)
    tocmap = {}
    for e in meta["toc"]:
        tocmap.setdefault(e["page"], e["title"])

    # digest: only the highlighted text of these pages, in reading order
    buf, last, sel_chars = [], None, 0
    for pg, i, txt, role, weight, note in paragraphs(sel, lines):
        if not (lo <= pg <= hi):
            continue
        sel_chars += len(txt)
        if pg != last:
            if pg in tocmap:
                buf.append(f"\n## {tocmap[pg]}")
            buf.append(f"\n*p{pg}*")
            last = pg
        if role == "def":
            txt = f"**{txt}**"
        if weight == "~":
            txt = f"> {txt}"
        if note:
            txt += f"  <!-- {note} -->"
        buf.append(txt)
    open(os.path.join(a.out, "digest.md"), "w").write("\n".join(buf).strip() + "\n")

    # source: every body line of the same pages, with the ids the reviewer can cite
    src, body_chars = [], 0
    for p in range(lo, hi + 1):
        recs = lines.get(str(p), [])
        src.append(f"--- p{p} ---")
        for i, r in enumerate(recs):
            if r.get("chrome") or r.get("margin") or (r.get("fig") and not r.get("show")):
                continue
            if not r.get("fig"):
                body_chars += len(r["t"])
            src.append(f"{i}|{'§ ' if r.get('head') else ''}{r['t']}")
        src.append("")
    open(os.path.join(a.out, "source.md"), "w").write("\n".join(src))

    pct = 100 * sel_chars / max(body_chars, 1)
    title = next((tocmap[p] for p in range(lo, hi + 1) if p in tocmap), "")
    print(f"wrote {a.out}/digest.md and {a.out}/source.md")
    print(f"section: {title or f'pages {lo}-{hi}'}")
    print(f"coverage line for the prompt:  Measured by the tool, {pct:.0f}% of this "
          f"section's body characters are highlighted ({sel_chars} of {body_chars}).")


if __name__ == "__main__":
    main()
