# Phase 5 — Report & exit

Fill `assets/report-template.md`. Rules:

- **Language:** the user's conversation language. Everything, including
  improvement comments.
- **Scoring math:** global grade = sum of per-question scores out of N×5.
  Per-mode = same restricted to the mode. Round percentages to whole numbers.
- **Question keys:** the scorecard `Q#` column uses the canonical `Q<NN>` ids
  from the pack/anchors, so cross-answer findings ("Q9 contradicts Q14")
  are traceable to rows.
- **Confidence header:** repeat the sampling margin for the chosen N, plus
  EVERY degradation recorded during the run (no DB, unanchored fraction,
  judge below default, outputs user-provided). No silent degradations.
- **Improvement comments:** one sentence, actionable, specific ("cap
  `get_pnl_evolution` at the last real month like `chart_data` does" — not
  "improve data handling").
- **ROI ordering of proposals:** (questions fixed × severity) / effort.
  A broken tool whose data exists elsewhere is almost always #1 — it's a
  wiring fix that converts ❌s into ✅s.
- **Deliver, then exit.** Present the report (and save it as
  `eval-runs/<date>-report.md` in the working directory if a filesystem is
  available — suggest the user add `eval-runs/` to their .gitignore). Offer: "Want me to help implement any of these proposals?" —
  but that is a NEW task outside this skill. Do not begin fixing things
  inside the eval run.
