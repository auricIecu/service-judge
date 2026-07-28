# Changelog

## 1.2 — 2026-07-03

- **Security hardening** (from an adversarial audit of the skill):
  - Judge now treats the probed service's answers as untrusted data — never
    follows instructions embedded in them, and runs with no tool access.
  - `batch_eval.py` maps verdicts by the authoritative `custom_id` instead
    of the model-echoed id (silent mis-scoring bug).
  - Parameterized-SQL rule for anchor extraction; config reading scoped to
    DB/observability keys only; data-privacy warning for the Batches API
    path; `eval-` session-ID stealth tradeoff documented; probe circuit
    breaker (>30% consecutive errors → pause); judge comments plain-text
    only; offer to gitignore `eval-runs/`.
- **Codex CLI support**: judge-model language made agent-neutral (Fable 5
  remains the default in Claude environments), in-session sequential path
  for agents without subagents, Codex install instructions in the README.
- **Machine-readable scorecard**: `eval-runs/<date>-scorecard.json` saved
  next to the report, with automatic delta section against the previous run.

## 1.1 — 2026-06-13

- **Description rewritten and validated with trigger evals** (20 realistic
  queries × 3 runs, judged with Claude Fable 5): trigger rate on
  should-trigger queries went from 17% to 37% with zero false triggers on
  near-miss queries (code review, unit tests, deployment, essay grading...).
- Frontmatter completed for distribution: `license`, `compatibility`,
  `metadata` (author, version).
- `references/judging.md` now documents how to resume the flow from
  `scripts/batch_eval.py` verdicts (load the JSONL, map by id, continue with
  the cross-answer pass).
- Rubric sync notes added to both `references/judging.md` and
  `scripts/batch_eval.py` (the rubric is intentionally duplicated so the
  batch path stays standalone).
- README: access-tier table (what you give vs. what you get), badges,
  `.skill` release artifact for claude.ai users, GitHub references unified
  to `auricIecu`.

## 1.0 — 2026-06-10

- Initial release: 5-phase LLM-as-judge evaluation (discovery, sizing,
  generation & probing, judging, report) with ground-truth anchors,
  trap cases, cross-answer hunting, and ROI-ordered improvement proposals.
