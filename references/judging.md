# Phase 4 — Judging

## Judge selection (before anything else)

The judge MUST be at least as strong as the model that produced the answers.
Default judge: `claude-fable-5`. If unavailable, escalate down: Opus, then
Sonnet — and record the judge used; it appears in the report.

- **Claude Code:** dispatch the judge as a subagent with the strongest model
  available. Give it: the full text of `references/rubric.md` inline in the
  prompt, the Phase 1 tool/mode catalog (so tool choice is judgeable),
  `anchors.md`, and the pack.
  Batch ~10 questions per subagent to keep each judgment focused.
- **claude.ai chat:** say: "Time to judge. Please switch to the strongest
  model you have (ideally Fable 5) with the model picker, then say 'go'."
  Then judge in-session.
- If the session/judge model is WEAKER than the evaluated model and cannot be
  upgraded: warn the user that verdicts on subtle errors are unreliable, and
  say so in the report (record it as the "judge < judged" caveat).

## Rubric

The rubric lives in `references/rubric.md` — the single source of truth,
shared with `scripts/batch_eval.py`. Load it and pass its FULL text inline
to whoever scores (do not paraphrase; inline it even if the scorer could
read the file itself, so the rubric always travels with the call).

## What to hunt beyond the rubric (the judge's real value)

After scoring individual answers, do a CROSS-ANSWER pass:
1. **Contradictions between answers** — Q9 says May has revenue, Q14 says
   April–June had no sales. Both scored fine alone; together they reveal a
   bug. The contradiction-bait pairs from Phase 3 land here.
2. **"Technical error" replies where the anchor shows the data EXISTS** —
   that is a broken tool, not missing data. Flag as tool bug (these are
   usually the highest-ROI fixes).
3. **Narrative over artifacts** — placeholder/empty periods narrated as real
   business events ("Q2 collapse"). The most dangerous hallucination class.
4. **Guardrails firing falsely** — error fallbacks answering legitimate
   questions with out-of-scope messages.
5. **Tables must SUM** — totals consistent with their parts, percentages
   adding to ~100.

## Anti-bias rules

- Judge against the rubric and the anchor, NEVER against "what I would have
  answered".
- Do not reward verbosity; directness is its own dimension.
- Judge each answer cold before the cross-answer pass (so pass-1 scores are
  independent).
