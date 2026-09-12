---
name: pdf-highlight
description: Read a PDF (textbook, paper, report, manual) and produce a highlighted copy marking only the content a reader must actually read to digest it, plus a highlights-only markdown digest. Use when asked to highlight a PDF, mark up a textbook, find the essential/important parts of a document, condense a book into what matters, or build a reading plan from a PDF.
---

# PDF essential-content highlighter

Turn a PDF into (1) a highlighted PDF and (2) `highlights.md`, a linear digest of
just the highlighted text. Target: **a reader who reads only the highlights can
restate every claim and apply every technique in the book.**

Highlighting is compression. Value comes from what you leave out. A book with 60%
highlighted is worth nothing; the aim is 15-30%.

## Pipeline

```bash
S=~/.claude/skills/pdf-highlight/scripts
W=<scratchpad>/hl-<bookname>          # work dir, keep out of the user's folders

$S/extract.py BOOK.pdf --out $W                  # 1. parse
#   --columns 2     two-column papers (fixes reading order)
#   --pages 15-402  skip front/back matter
#   --merge-rows    fold display equations typeset as one-symbol-per-line into
#                   a single id. Decide BEFORE selecting: it renumbers ids, so
#                   a spec written without it silently points at the wrong lines.
cat $W/meta.json | python3 -m json.tool | head -60   # TOC, chunk list, page counts
# 2. read $W/text/chunk-NNN.md, write selections to $W/sel/chunk-NNN.txt
$S/highlight.py --work $W --spec $W/sel.txt --dry-run          # 3. check coverage
$S/highlight.py --work $W --spec $W/sel.txt \
    --out BOOK-highlighted.pdf --plan highlights.md            # 4. apply
```

Scripts self-install PyMuPDF via `uv run` — no setup. `highlight.py` always
rebuilds from the original PDF, so re-running never stacks duplicate highlights.

### Line ids

`extract.py` numbers every text line. Chunk files look like:

```
--- p41 ---
0|§ 3.2 The Chain Rule
1|If g is differentiable at x and f is differentiable at g(x), then
2|the composite f ∘ g is differentiable at x and
```

`§` marks a heading, `▦` a figure or display equation. Ids are not contiguous —
running headers and inline graphics occupy numbers that never appear in the text.
You select by id — `PAGE:LINE[.WORD][-LINE[.WORD]]`:

```
41:1-2      key    chain rule statement      # whole lines 1-2
41:4.6-7    core                             # from word 6 of line 4 to end of line 7
41:7-9.11   core                             # line 7 through word 11 of line 9
41:9.3-9.8  ctx                              # words 3-8 of line 9 only
```

A word index is the 0-based position of a whitespace-separated token in the line
as printed in the chunk file. It trims the **start of the first line** and the
**end of the last line**; lines in between are always taken whole.

Use it. A highlight that snaps to whole lines starts at the left margin when the
clause starts mid-line, and runs to the right margin when the sentence ended four
words earlier — it reads as a machine sweep, not as reading. Trim both ends to the
actual clause: begin at the first word of the thought and end on the word the
thought ends on (keep the period). The cost of an off-by-one is one extra word, so
count quickly and move on.

**Two colours. Green is a definition, yellow is everything else worth reading.**
Depth of colour says how important it is to read.

| Token | Renders as |
|---|---|
| `!def` / `def` / `~def` | green — what a term or symbol means |
| `!claim` / `claim` / `~claim` | yellow — the reading |

The prefix is the weight: `!` essential, nothing at all for ordinary "worth
reading", `~` supporting. Most spans take no prefix; reserve `!` for the lines
you would keep if you could keep only a tenth of the book, and `~` for what a
confident reader can skip.

Resist inventing more categories. Earlier versions had separate colours for
caveats and method steps, and the page turned into a rainbow that took longer to
read than the text. `core`/`key`/`ctx` and the old role names still parse, mapping
onto these two.

Trailing text on a spec line is a popup note. **Give every `def` a note naming
the term** — notes show up in the reader's annotation sidebar, so labelled
definitions become a clickable index of every concept in the book. The dry run
counts `def` spans that lack one. Keep other notes rare and short.

**Never invent an id.** Every id must come from a chunk file you actually read.
Ids are per page and 0-based, running headers/footers are already stripped, and
line numbers are not contiguous across pages. `--dry-run` hard-fails on bad ids.

### Math and figures

Formulas in many PDFs are vector art with no text layer, so the readable text
comes out gappy: `We introduce another sequence , let  and`. That is not
corruption — the missing pieces are graphics sitting in the id space between and
inside the lines you can read. You do not need to select them explicitly:

- **inline math** is pulled into the highlight of the line it sits on,
  automatically;
- **side-margin notes** (keyword indexes, asides, video links) are detected by
  x-position and hidden from the chunk text — they keep their ids but are out of
  the character budget, so you never need to work around them;
- **display equations** appear as their own `▦` line, or fall inside a range that
  spans them (`41:4-9` covers everything between line 4 and line 9).

So read gappy prose as "sentence, formula, sentence" and select the range across
it. Use a `key` note to say what an unreadable formula is when it matters.

## What to select

Work section by section. First classify what the section *is doing*, then apply
its budget — this is the whole job:

| Section type | Take | Leave |
|---|---|---|
| Definition / notation | the definition sentence, every symbol's meaning | restatement in words, "intuitively..." after a precise statement |
| Theorem / law / rule | the statement, its hypotheses, its scope conditions | the name-drop sentence, attribution history |
| Proof / derivation | the strategy line, the one non-obvious step, the result | routine algebra, every intermediate line |
| Mechanism / how it works | the causal chain, in order | metaphors and re-explanations of it |
| Worked example | the setup, the step that shows the *method*, the answer | arithmetic, repeated instances of the same pattern |
| Motivation / history | nothing, usually | all of it |
| Summary box / chapter recap | anything not already highlighted | duplicates of what you took |
| Exercises / problem sets | nothing | all of it |
| Figures / tables | the caption if it carries a claim | decorative captions |

## Rules that came from blind readers

These are the defects that actually cost understanding, found by handing the
digest alone to readers who had not seen the book. The `audit:` block in the dry
run counts most of them — get it near zero before shipping.

**Take the sentence that makes a technique executable.** A theorem states the
formula; a *neighbouring* sentence says how to apply it — apply it recursively,
compute the diagonal first, stop when the matrix is triangular. Miss that sentence
and the reader can restate the result but cannot use it. This was the single most
damaging omission found.

**Definitions hide inside navigation.** "X is Y, which we introduce in Chapter N"
is a definition wearing a signpost; keep the "X is Y" half. Chapter-opening
roadmaps often carry the only one-line characterisation of a concept in the entire
book, so do not skip them as mere motivation.

**A section's genre does not settle a sentence's.** An introduction labelled
motivation still fixes the book's core vocabulary — classify each sentence, not the
section around it. The worked failure: a chapter-1 pass took the narrative and
dropped the definition of *model*, the quantity that training optimises, and the
one-line description of all four parts of the book.

**Every span starts and ends at a sentence boundary.** Extend to the capital letter
and to the closing period, across line and page breaks. If the tail is not
reachable, drop the span rather than keep a truncated predicate. Never leave a
stranded scrap like "In the second sense," — under five words of prose is not a
highlight.

**A connective needs its premise.** If a span opens with *Therefore, Thus, This, It,
They*, either highlight the premise it points at or start the span later.

**One proposition, one highlight.** When two spans in a chapter say the same thing,
keep the sharper one: the general statement over the special case, the precise
statement over the intuitive restatement. Textbooks say everything three times, and
the audit's `repeats` check finds the near-duplicates.

**If the author flagged a term, define it.** Margin keywords, bold terms and index
entries are the author saying what matters; for each one, highlight the clause that
says what it *means*. The audit lists flagged terms with no nearby highlight naming
them — treat that as a checklist, not a verdict.

**A chapter number is not information.** Never keep a sentence whose only new
content is "in Chapter N we introduce X" — but do keep it when the same sentence
also names the method (PCA, Gaussian mixtures, maximum likelihood). Strip the
navigation, keep the noun.

**Prefer the discriminating clause.** When a paragraph both announces a topic and
says what separates it from its neighbour, take the second: "unlike regression,
there are no labels" teaches more than "dimensionality reduction is covered in
Chapter 10". Sentences carrying *unlike, however, in contrast, there are no* are
almost always the highest-value line in a definitional paragraph.

**A label needs its reason.** "Quantification of uncertainty is the realm of
probability theory" is a bare mapping; without the adjacent sentence about signal
and noise it costs words and teaches nothing. Keep the pair, or keep neither.

**Name every list member, or none.** "The four pillars are regression,
dimensionality reduction, density estimation and classification" is four names and
zero content. Take one defining clause per item, or leave the bare list alone.

**Parallel results deserve parallel treatment.** If you keep uniqueness for one
decomposition, keep it for its siblings; an asymmetry reads as an oversight.

**In worked examples, keep the method and drop the arithmetic.** Take the sentences
whose subject is the procedure and that carry no example-specific numerals.

**Notes label, they never supply.** A popup note must not carry content missing from
the highlight itself — a reader of the highlights alone never sees the note. If a
formula matters, highlight the formula.

Two tests for any line:

1. **Removal test** — if this line disappears, does a claim go unsupported or a
   term go undefined? If no, don't highlight it.
2. **Duplication test** — does this say something already highlighted, in new
   words? Textbooks state everything three times (preview, body, summary). Take
   the sharpest statement once.

Prose is mostly connective tissue. In a well-written chapter the load-bearing
content is a handful of sentences per page; most paragraphs contribute one clause.
Highlight fragments of a paragraph, not the paragraph.

**Budget by genre** (char coverage, reported by `--dry-run`):

| Math / CS / hard science texts | 25-35% |
| General textbook, survey | 15-25% |
| Technical report, audit, paper | 20-30% |
| Business / popular nonfiction | 8-15% |

If the user stated a purpose ("exam next week", "I only need chapters 4-7", "just
enough to use the API"), weight toward it and say so in the summary: skip what
serves other purposes, and drop the budget accordingly. With no purpose stated,
optimize for full comprehension of the whole book.

## Long books

For >6 chunks, dispatch chunk-level subagents in parallel (5-8 at a time). Give
each one: the chunk path, this file's "What to select" section, its budget, the
book's subject and the running glossary of terms already defined. Require it to
**write `$W/sel/chunk-NNN.txt` and return only the path + line count + a 5-line
summary of what that chunk covers** — never the selection text itself.

Read the first chunk yourself before dispatching any: it sets the book's voice,
notation, and density, and you need it to brief the others. Feed each round's
returned summaries into the next round's briefs so later chapters don't
re-highlight terms already established.

Then `cat $W/sel/*.txt > $W/sel.txt` and validate as one spec.

## Verify before delivering

`--dry-run` first, and act on what it prints — the coverage table, then the
`audit:` block, which counts fragments, scraps, orphan connectives, repeated
propositions and author-flagged terms nobody defined. Those counts are the fastest
read on selection quality; a chapter with dozens of fragments was selected by line
rather than by sentence.

- **chunk at 0%** — you skipped it. Never ship a book with unhighlighted chapters
  unless they're front/back matter or the user scoped them out.
- **over budget** — don't trim uniformly. Find the sections where you highlighted
  whole paragraphs and cut to the load-bearing clause.
- **under 5%** — you selected headings only. That's a table of contents, not a
  digest.

Then read `highlights.md` end to end as a reader who has not seen the book. You
are looking for breaks in the chain: a term used but never defined, a "therefore"
whose premise you cut, a pronoun with no antecedent, a formula whose variables
were introduced in a line you dropped. Patch those ids into the spec and re-apply.
This pass is what separates a real digest from a keyword sweep — do not skip it.

## Report to the user

The output PDF opens on a generated **reading map**: the colour legend, the
coverage figure in reading hours, and a bar per chapter showing how much of it is
highlighted (`--no-legend` to suppress). It is prepended, so PDF page numbers run
one ahead of the original — say so.

Give them: output PDF path, `highlights.md` path, coverage % and what it means in
reading time, the colour legend, and anything you deliberately excluded
(exercises, appendices, chapters they scoped out).

## Failure modes

- **No text layer** — `extract.py` warns when pages have no text. It's a scan;
  tell the user to run `ocrmypdf in.pdf out.pdf` first (`brew install ocrmypdf`).
- **Scrambled reading order** — two-column papers need `--columns 2`.
- **Text looks gappy or truncated** — formula-heavy PDFs; see "Math and figures".
  Extraction is working, the math is just graphics.
- **Diagram-heavy PDF highlights look blocky** — `--no-figures` turns off graphic
  capture and highlights text lines only.
- **Highlights look shifted** — the sidecar was built from a different PDF than
  the one being annotated. Re-run `extract.py`; never hand-edit `lines.json`.
