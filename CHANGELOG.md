# Changelog

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
