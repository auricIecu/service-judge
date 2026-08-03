# Changelog

## 1.3 — 2026-07-27

- **Plugin distribution** (BREAKING for manual-clone installs): `SKILL.md`
  moved from the repo root to `skills/service-judge/` (with `references/`,
  `assets/`, and `scripts/` inside it), so the repo now serves as a plugin
  for both Claude Code (`.claude-plugin/`) and Codex CLI (`.codex-plugin/` +
  `.agents/plugins/marketplace.json`) — the channels with versioned updates.
  `npx skills add auricIecu/service-judge` still works (it discovers skills
  in subdirectories); a raw `git clone` into a skills dir no longer does.
- **New skill `service-judge-loop`**: sets up and drives `scripts/loop.py`
  (golden set, run config, stop-condition reporting).
- **Harness-only loop**: frozen golden set with dev/holdout split and a
  prepare → harness judgment → finalize protocol. Scoring consumes only the
  active Claude Code or Codex limits; model API providers, keys, polling, and
  token billing were removed.
- **Hard gate** now fails on explicit broken-tool, hallucinated-narrative,
  and false-guardrail findings even when a noisy judge scores them above 1.
- **Cross-analysis** now runs with the pinned judge on every loop iteration,
  is persisted in `cross-analysis.json` and `grade.json`, and feeds the hard
  gate without rewriting cold per-question scores.
- Plugin/skill versions, marketplace description, and loop token-budget
  configuration are consistent across both distribution channels.

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
