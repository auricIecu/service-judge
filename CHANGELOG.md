# Changelog

## 1.6.0 — 2026-09-02

Dimension-based loop grades, configurable goals, and corrected gate math.

- **Breaking:** schema v1 loop configs are rejected; start a new v2 run.
- **Breaking:** existing markdown anchor snapshots are invalidated; anchors
  now live as `raw/anchors.snapshot.json`.
- **Loop grade v2:** verdicts carry dimensions, while `verdict` and
  `unanchored` are derived by `loop.py`.
- **Goals:** `grade.json` records configured goals and whether each was met;
  `soft_gate` is removed.
- **Gate math:** certifiable accuracy metrics use anchored questions only;
  behavior dimensions and hard gates use all questions.

## 1.5.0 — 2026-08-27

Adaptive probing for the improvement loop. The default loop behavior is still
full-set probing, but runs can now opt in to targeted dev-only probes during
development while reserving one full golden-set run for certification.

- **Adaptive loop strategy**: `probe_strategy: "adaptive"` selects dev
  failures, related questions, all-dev contradiction groups, and a
  deterministic regression sample for focused iterations.
- **Full-only certification**: quality gates, regression, and stagnation are
  evaluated only on full runs; `max_iterations` still counts every iteration so
  the loop always terminates.
- **Budget reserve**: adaptive configs require `answer_budget`, reserve
  `len(golden_set)` answers for the certifying full run, and fall back to full
  when a focused probe does not fit.
- **Resume safety**: each iteration persists `selection.json`; mismatched pack
  IDs are rejected, and `selection.json` without `raw/pack.jsonl` reports
  `in_progress` instead of silently re-probing.
- **Judging contract**: loop cross-analysis is scoped to the current pack, so
  focused iterations never emit findings for answers that were not probed.

## 1.4 — 2026-08-03

Cost control, from an audit of ~4.700 real eval cases. Judging is already free
(it runs on the harness subscription); what costs money is every answer asked
of the evaluated service, and the audit found full 100-question suites being
re-run as an *exploration* tool. This release makes the skill buy as few
answers as possible.

- **Canary gate**: whatever tier is chosen, the first 10–12 questions are
  probed and judged BEFORE the rest. The run aborts there on a cross-tenant
  leak, a bypassed guardrail, a severely wrong figure, an unauthorized side
  effect, >20% ❌, the wrong answering model, or the wrong environment — a
  decisive finding for the price of 12 answers instead of 100.
- **Sizing is now tiered**: canary (10–12) / diagnostic (30) / release
  (50–100), with guidance to pick the smallest tier that answers the question
  actually being asked, to scope by the change rather than by the inventory of
  modes, and to reserve `100 × 3` for formal baselines only.
- **Answer-pack reuse**: re-judge a stored pack instead of re-probing when
  nothing that alters the service's behaviour has changed. Changing the
  rubric, the judge, or the report now costs zero answers.
- **Usage telemetry**: packs record `model_generations` and input/cached/
  output tokens when the service exposes them, and every report ends with a
  "Cost of this run" section — judge cost stated as $0, service cost broken
  down, cost per correct answer for candidate comparisons.
- **Write policy fixed**: the old "never modify the user's repo" rule
  contradicted Phase 5 writing to `eval-runs/`. It now reads "never modify
  product code; write eval artifacts only to the location confirmed in
  Phase 1".

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
