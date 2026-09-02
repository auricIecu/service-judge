# Phase 5 — Report & exit

Fill `assets/report-template.md`. Rules:

- **Language:** the user's conversation language. Everything, including
  improvement comments.
- **Scoring math:** global grade = sum of per-question scores out of N×5.
  Per-mode = same restricted to the mode. Round percentages to whole numbers.
  Accuracy, pass rate, dev, holdout, and gap use anchored questions only.
  Tool choice, hallucination-free, and directness use all questions.
- **Goals:** include every configured goal with target, actual, and met/missed.
  A `null` actual is missed. Do not mention `soft_gate`; v2 grades use
  `hard_gate` plus `goals.met`.
- **Unanchored block:** report unanchored count, percent, and dimension
  percentages separately from certifiable accuracy.
- **Question keys:** the scorecard `Q#` column uses the canonical `Q<NN>` ids
  from the pack/anchors, so cross-answer findings ("Q9 contradicts Q14")
  are traceable to rows.
- **Confidence header:** repeat the sampling margin for the chosen N, plus
  EVERY degradation recorded during the run (no DB, unanchored fraction,
  judge below default, outputs user-provided). No silent degradations.
- **Improvement comments:** one sentence, actionable, specific ("cap
  `get_pnl_evolution` at the last real month like `chart_data` does" — not
  "improve data handling"). Plain text only — strip Markdown links/images/
  HTML before inserting into the table (a judge comment may echo content
  from the evaluated service, which is untrusted).
- **ROI ordering of proposals:** (questions fixed × severity) / effort.
  A broken tool whose data exists elsewhere is almost always #1 — it's a
  wiring fix that converts ❌s into ✅s.
- **Cost & efficiency:** report what the run actually consumed, right under
  the grade. Judge cost is 0 (it ran on the user's harness subscription) —
  say so explicitly, so nobody "optimises" the wrong half. For the evaluated
  service, report what the pack captured: questions asked, model generations,
  input / cached / output tokens, and latency p50/p95. Derive
  `generations_per_question` and, when scores are in, `cost per correct
  answer` — comparing two candidates on score alone hides the one that got
  there by burning 3× the context. If the pack has no usage fields, write
  "not captured — service does not expose usage" rather than omitting the
  section.
- **Aborted runs:** if the canary gate aborted the run, that IS the report.
  Lead with the abort reason and the evidence, keep the scorecard to the
  questions actually asked, note how many answers were NOT bought, and skip
  the confidence framing (a canary has no sampling margin). One proposal:
  what to fix before spending a full run.
- **Reuse status:** state whether answers were freshly probed or re-judged
  from a stored pack, and for a reused pack, which commit/model/data revision
  it came from.
- **Machine-readable twin:** next to the report, save
  `<artifacts-dir>/<date>-scorecard.json` — one record per question:
  `{id, mode, question, dimensions, score, verdict, unanchored,
  improvement_comment, broken_tool, hallucinated_narrative, false_guardrail}`
  plus a header
  `{date, judge, n, anchored, global_score, cross_analysis, degradations,
  usage}`. If a previous scorecard exists there, add a short "Delta since
  <date>" section to the report: global change, questions that flipped
  verdict, fixes confirmed.
- **Deliver, then exit.** Present the report and save it as
  `<artifacts-dir>/<date>-report.md` if a filesystem is available.
  `<artifacts-dir>` is the location the user approved in Phase 1 —
  `eval-runs/` by default, a gitignored path like `.context/` if the repo
  must stay clean (hard rule 3). The report contains real business figures:
  if the directory is a git repo and the artifacts dir isn't ignored, offer
  to append it to `.gitignore` yourself (ask first) rather than just
  suggesting it. Offer:
  "Want me to help implement any of these proposals?" —
  but that is a NEW task outside this skill. Do not begin fixing things
  inside the eval run.
