#!/usr/bin/env -S uv run --quiet --with pymupdf python
"""Author, validate and run per-chapter quizzes for a highlighted book.

    quiz.py validate QUIZ.md --work WORKDIR [--spec sel.txt]
    quiz.py list   QUIZDIR
    quiz.py ask    QUIZ.md N        # the question alone - answers stay hidden
    quiz.py key    QUIZ.md N        # expected answer + where to re-read
    quiz.py record QUIZ.md N right|wrong [--note "..."]
    quiz.py due    QUIZDIR          # what was missed and not yet re-passed

`ask` and `key` are separate on purpose: run a quiz by asking one question at a
time, so the expected answers never enter the transcript before the reader has
answered.
"""
import argparse, datetime, json, os, re, sys

FIELDS = ("ask", "type", "look for", "covered by highlights", "if wrong, re-read")
TYPES = ("definition", "application", "contrast", "consequence", "navigation")
# types that cannot be answered by locating one sentence
REASONING = ("application", "contrast", "consequence")
STOPW = {"the","and","that","this","with","from","which","have","been","are","was",
         "for","not","but","can","its","their","when","then","than","into","such",
         "also","each","they","them","these","those","where","what","how",
         "all","any","one","two","use","used","using","data","book"}
ANCHOR = re.compile(r"p(\d+)(?::(\d+)(?:-(\d+))?)?")
YESNO = re.compile(r"^(is|are|was|were|does|do|did|can|could|will|would|should|has|have)\b", re.I)


def parse(path):
    text = open(path).read()
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    questions = []
    for block in re.split(r"^##\s+Q(\d+)\s*$", text, flags=re.M)[1:]:
        if block.strip().isdigit():
            questions.append({"n": int(block.strip())})
            continue
        cur, field = questions[-1], None
        for line in block.splitlines():
            fm = re.match(r"\*\*(.+?):\*\*\s*(.*)", line)
            if fm and fm.group(1).strip().lower() in FIELDS:
                field = fm.group(1).strip().lower()
                cur[field] = fm.group(2).strip()
            elif field and line.strip() and not line.startswith("#"):
                cur[field] = (cur[field] + " " + line.strip()).strip()
            elif not line.strip():
                field = None
    return meta, questions


def page_map(quizpath, work=None):
    """Translate extraction ids into what the reader's viewer shows."""
    info = {}
    for base in filter(None, [work, os.path.dirname(os.path.abspath(quizpath)),
                              os.path.join(os.path.dirname(os.path.abspath(quizpath)), "..")]):
        cand = os.path.join(base, "output.json")
        if os.path.exists(cand):
            info = json.load(open(cand))
            break
    return info.get("page_offset", 1), info.get("labels", {})


def render(text, offset, labels):
    """p18:35-40 -> 'page 19 (book p.12)'. Line ids are authoring coordinates;
    a reader never sees them, so they are dropped."""
    def one(m):
        pg = int(m.group(1))
        out = f"page {pg + offset}"
        lab = labels.get(str(pg))
        if lab and lab != str(pg + offset):
            out += f" (book p.{lab})"
        return out
    return ANCHOR.sub(one, text)


def anchors(q):
    return [(int(p), int(a) if a else None, int(b) if b else (int(a) if a else None))
            for p, a, b in ANCHOR.findall(q.get("if wrong, re-read", ""))]


def cmd_validate(a):
    meta, qs = parse(a.quiz)
    lines = json.load(open(os.path.join(a.work, "lines.json")))["lines"]
    sel_pages = {}
    if a.spec:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from highlight import parse_spec, expand
        errs, warns = [], []
        sel = expand(parse_spec(a.spec), lines, errs, warns)
        sel_pages = {p: set(d) for p, d in sel.items()}

    # Highlights of THIS chapter only: a reader taking chapter 1's quiz has read
    # chapter 1's highlights, not the whole book's.
    scope = set()
    if meta.get("pages"):
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)", meta["pages"])
        if m:
            scope = set(range(int(m.group(1)), int(m.group(2)) + 1))
    if not scope:
        scope = {pg for q in qs for pg, _, _ in anchors(q)}
    hl_spans = []
    if a.spec:
        from highlight import paragraphs
        for pg, i, txt, role, weight, note in paragraphs(sel, lines):
            if pg in scope:
                hl_spans.append((pg, txt))
    all_hl = " ".join(t for _, t in hl_spans).lower()

    def words(t):
        return {w for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in STOPW}

    errors, notes = [], []
    if not qs:
        errors.append("no questions found (expected '## Q1' blocks)")
    if not 5 <= len(qs) <= 12:
        notes.append(f"{len(qs)} questions; 6-10 suits a chapter")
    seen = set()
    for q in qs:
        tag = f"Q{q['n']}"
        if q["n"] in seen:
            errors.append(f"{tag}: duplicate number")
        seen.add(q["n"])
        for f in FIELDS:
            if not q.get(f):
                errors.append(f"{tag}: missing '{f}'")
        if len(q.get("look for", "").split()) < 4:
            notes.append(f"{tag}: 'Look for' is too thin to grade against")
        qtype = q.get("type", "").strip().lower()
        if qtype and qtype not in TYPES:
            errors.append(f"{tag}: type must be one of {', '.join(TYPES)}")
        if YESNO.match(q.get("ask", "")):
            notes.append(f"{tag}: yes/no question - a reader can guess it")
        if q.get("ask", "").strip().lower().startswith("what is the definition"):
            notes.append(f"{tag}: asks for a definition verbatim; ask them to use it instead")
        anc = anchors(q)
        if not anc:
            errors.append(f"{tag}: 'If wrong, re-read' names no page")
        for pg, lo, hi in anc:
            recs = lines.get(str(pg))
            if recs is None:
                errors.append(f"{tag}: p{pg} is not in the extracted range")
                continue
            if lo is not None and hi >= len(recs):
                errors.append(f"{tag}: p{pg}:{hi} out of range (page has {len(recs)} lines)")
        look = q.get("look for", "")
        # a key must not grade on a name the highlights never show the reader
        if all_hl:
            for name in re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\b", look):
                if name.lower() not in all_hl:
                    errors.append(f"{tag}: 'Look for' grades on {name!r}, which appears "
                                  f"nowhere in the highlights")
        # an answer that sits inside one highlighted sentence is a lookup, not a test
        if hl_spans and qtype in REASONING:
            lw = words(look)
            if lw:
                best = max((len(lw & words(t)) / len(lw) for _, t in hl_spans), default=0)
                if best > 0.65:
                    notes.append(f"{tag}: the whole answer sits in one highlighted "
                                 f"sentence - a reader can match words without understanding")
        if len(look) > 90 and ";" not in look and not re.search(r"\(a\)", look):
            notes.append(f"{tag}: 'Look for' is one long blob - split it into clauses "
                         f"and say how many are required to pass")
        if q.get("ask", "").count("?") > 1:
            notes.append(f"{tag}: more than one question in the stem - split it")
        claim = q.get("covered by highlights", "").lower()
        if claim not in ("yes", "no", "partial"):
            errors.append(f"{tag}: 'Covered by highlights' must be yes, no or partial")
        elif sel_pages and claim == "yes":
            hit = any(any(i in sel_pages.get(pg, ()) for i in range(lo, hi + 1))
                      for pg, lo, hi in anc if lo is not None)
            if not hit:
                errors.append(f"{tag}: claims the highlights cover it, but no cited line "
                              f"is highlighted")
    gaps = [q["n"] for q in qs if q.get("covered by highlights", "").lower() != "yes"]
    kinds = [q.get("type", "").strip().lower() for q in qs]
    reasoning = sum(1 for k in kinds if k in REASONING)
    if qs and reasoning < len(qs) / 2:
        notes.append(f"only {reasoning}/{len(qs)} questions require reasoning rather than "
                     f"recall - aim for at least half application/contrast/consequence")
    if kinds.count("navigation") > 2:
        notes.append(f"{kinds.count('navigation')} navigation questions - a reader can ace "
                     f"the table of contents without understanding the subject")

    print(f"{a.quiz}: {len(qs)} questions")
    for e in errors:
        print(f"  ERROR  {e}")
    for n in notes:
        print(f"  note   {n}")
    if gaps:
        print(f"  {len(gaps)} question(s) not answerable from the highlights alone: "
              f"{', '.join('Q'+str(n) for n in gaps)}")
        print("         each one is either out of scope for the digest, or a gap in it")
    if not errors:
        print("  structure ok")
    sys.exit(1 if errors else 0)


def cmd_list(a):
    for f in sorted(os.listdir(a.dir)):
        if f.endswith(".md") and f != "results.md":
            meta, qs = parse(os.path.join(a.dir, f))
            print(f"{f:16s} {meta.get('chapter', '?'):46s} {len(qs)} questions")


def _get(a):
    _, qs = parse(a.quiz)
    for q in qs:
        if q["n"] == a.n:
            return q
    sys.exit(f"no Q{a.n} in {a.quiz}")


def cmd_ask(a):
    print(f"Q{a.n}. {_get(a)['ask']}")


def cmd_key(a):
    q = _get(a)
    offset, labels = page_map(a.quiz, a.work)
    print(f"Look for: {q['look for']}")
    print(f"Re-read:  {render(q['if wrong, re-read'], offset, labels)}")
    print(f"In the highlights: {q.get('covered by highlights')}")


def cmd_record(a):
    path = os.path.join(os.path.dirname(os.path.abspath(a.quiz)), "results.md")
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("# Quiz results\n\n| date | quiz | q | result | note |\n"
                    "|---|---|---|---|---|\n")
        f.write(f"| {datetime.date.today()} | {os.path.basename(a.quiz)} | Q{a.n} "
                f"| {a.result} | {a.note or ''} |\n")
    print(f"recorded Q{a.n} {a.result} in {path}")


def cmd_due(a):
    path = os.path.join(a.dir, "results.md")
    if not os.path.exists(path):
        sys.exit("no results yet")
    state = {}
    for line in open(path):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0][:2].isdigit():
            state[(cells[1], cells[2])] = cells[3]
    due = sorted(k for k, v in state.items() if v == "wrong")
    if not due:
        print("nothing outstanding - every missed question has since been re-passed")
    for quiz, q in due:
        print(f"{quiz}  {q}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("quiz"); v.add_argument("--work", required=True)
    v.add_argument("--spec"); v.set_defaults(fn=cmd_validate)
    l = sub.add_parser("list"); l.add_argument("dir"); l.set_defaults(fn=cmd_list)
    k = sub.add_parser("ask"); k.add_argument("quiz"); k.add_argument("n", type=int)
    k.set_defaults(fn=cmd_ask)
    y = sub.add_parser("key"); y.add_argument("quiz"); y.add_argument("n", type=int)
    y.add_argument("--work", help="work dir holding output.json (page translation)")
    y.set_defaults(fn=cmd_key)
    r = sub.add_parser("record"); r.add_argument("quiz"); r.add_argument("n", type=int)
    r.add_argument("result", choices=["right", "wrong"]); r.add_argument("--note")
    r.set_defaults(fn=cmd_record)
    d = sub.add_parser("due"); d.add_argument("dir"); d.set_defaults(fn=cmd_due)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
