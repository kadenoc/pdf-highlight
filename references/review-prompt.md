# Blind-review prompt template

Dispatch this to a subagent with **no context about how the selection was made**.
Substitute the `{...}` fields. The blind-first ordering is what makes it work: a
reviewer who reads the source first can no longer tell what the digest fails to
convey.

---

You are evaluating the output of a tool that highlights textbooks. Be adversarial
and specific. No flattery, no hedging.

THE TOOL'S GOAL: a reader who reads ONLY the highlighted text should be able to
restate every claim in the section and apply every technique, while reading as few
words as possible. Both failure directions matter equally: essential content left
unhighlighted, and unnecessary content highlighted.

MATERIAL ({book title}, {what this section covers}):
- {dir}/digest.md — ONLY the highlighted text, in reading order.
- {dir}/source.md — the full text of those pages. Each line is `<id>|<text>`; `§`
  marks a heading, `▦` a figure or display equation.

In the digest: `**bold**` = tagged as a definition, `>` = tagged as
lower-importance, `<!-- ... -->` = a label the tool attached. Ignore the markup
and labels when judging length — measure only the highlighted text itself.

{coverage line printed by review_pack.py}

{If the PDF renders maths as vector art, add: This PDF renders mathematics as
vector art with no text layer, so formulas are missing from BOTH files and the
prose reads gappy in both. Do not count a missing formula as a failure unless the
surrounding prose fails to say what it establishes.}

FOLLOW THIS ORDER — do not read the source until you finish step 1.

1. BLIND PASS. Read only the digest. Write: (a) what you can now state
   confidently; (b) every term the digest uses but never defines; (c) every claim
   you cannot support from the digest alone; (d) every technique you could not
   actually carry out; (e) anything that reads as a fragment starting mid-thought.

2. AUDIT PASS. Now read the source. Report:
   - FALSE NEGATIVES: essential content not highlighted — especially theorem
     hypotheses, conditions of applicability, and the step that makes a procedure
     executable. Cite line ids and say what understanding is lost.
   - FALSE POSITIVES: highlighted content droppable with no loss. Quote it and say
     why it is not load-bearing.
   - Estimate what percentage of highlighted characters could be cut, and what
     percentage of essential content is missing.

3. RATINGS, 1-10 each with one line of justification: comprehension-per-word;
   completeness; precision; overall verdict against the stated goal.

4. Is this good enough to ship to someone who wants to digest the book? Yes or no,
   defended in three sentences, then the single highest-value change outstanding.

Return the report itself, under 900 words. Do not write any files.
