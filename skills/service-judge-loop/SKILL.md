---
name: service-judge-loop
description: >-
  Set up and run the autonomous improvement loop around a service-judge
  evaluation: freeze a golden question set, write the run config, and drive
  scripts/loop.py, which re-evaluates the service after each human or
  authorized autopilot fix until the quality gates pass (or it detects
  regression, stagnation, or the iteration limit). Use when the user wants to
  iterate on their AI service's quality over multiple eval runs — "keep
  evaluating until it passes", "track whether my fixes improved the bot",
  "run the eval loop". For a single one-off evaluation, use the service-judge
  skill instead.
license: MIT (see LICENSE)
metadata:
  author: auricIecu
  version: "1.7.2"
---

# service-judge-loop

You orchestrate the improvement loop, not the evaluation itself. The
evaluation contract (phases and rubric) lives in the sibling
`service-judge` skill; the loop machinery lives in its `scripts/` directory —
in an installed plugin that is `../service-judge/scripts/` relative to this
file; otherwise use a clone of https://github.com/auricIecu/service-judge.

## What the loop does

`scripts/loop.py --run .service-judge/run-<id>/` executes iterations of:
select a probe pack → probe those questions against the service → ask the
current Claude Code or Codex harness to judge and cross-analyze the pack →
write `grade.json` (per-question dimensions, cross-answer findings,
anchored-only accuracy/dev/holdout metrics, all-question behavior metrics,
hard gate, and configured goals) → append to `history.json` → stop or wait
for the next fix. Manual mode waits for a human; autopilot writes a redacted
`fix-brief.json` and follows the authorized cycle below.

It stops on hard gate plus configured goals passed, regression (it notifies — never reverts),
stagnation (<2pp improvement twice in a row), the iteration limit, or an
autopilot full run with nothing actionable left for the fixer. LLM
usage consumes only the active harness subscription/session limits. No API
key is read and no model API is called by the plugin.

**Cost.** Judging is free; the answers are not. With no `probe_strategy`, or
with `"probe_strategy": "full"`, the loop deliberately re-probes the WHOLE
golden set every iteration; that is the backwards-compatible behavior.

For cheaper development iterations, opt in to `"probe_strategy": "adaptive"`.
Adaptive still starts with a full baseline and can only satisfy the quality
gates on a full run. Between those full runs it probes a dev-only focused pack:
dev failures, all-dev cross-analysis groups from the last full grade, dev
questions with the same `(mode, type)` as a failure, and a deterministic
regression sample. It reserves one full golden-set run inside `answer_budget`,
forces the last permitted iteration to full, and falls back to full whenever
the focused pack is not worth or cannot fit the remaining budget.

**Focused invariant.** A focused run never probes holdout, so it cannot
calculate `gap_pp`, does not evaluate `goals`, and cannot certify. It only
produces a dev brief and marks priorities for the next fix.

Typical 30-question config:

```json
{
  "probe_strategy": "adaptive",
  "focused_max_questions": 10,
  "regression_sample": 3,
  "answer_budget": 70,
  "max_iterations": 4
}
```

With 30 questions, `answer_budget: 70` buys one focused iteration
(`30 + 10 + 30`). Use `80` for two focused iterations. `loop.py` never
re-probes a pack it already has on disk; if `selection.json` exists without
`raw/pack.jsonl`, it reports `in_progress` and tells the operator what to
delete before retrying.

## Your job when this skill triggers

1. **Golden set.** The loop needs a frozen question set with a dev/holdout
   split at `.service-judge/questions.golden.jsonl` (schema: one
   `{id, mode, type, split, question}` per line). If the user ran the
   service-judge skill before, Phase 3 offered to freeze one — reuse it.
   Otherwise generate one now following the service-judge skill's Phase 2–3,
   then freeze it and record its sha256.

   Write the ground-truth snapshot to
   `.service-judge/run-<id>/raw/anchors.snapshot.json` — `loop.py` creates that
   directory. Anchors quote real customer values and `raw/` is the only path
   the ignore rules protect; a snapshot anywhere else gets committed.

   Before continuing to autopilot, make the SERVICE repo—not merely the plugin
   checkout—clean for these artifacts. The golden set must already be tracked
   in a clean commit or ignored. Verify that `.service-judge/**/raw/`,
   `.service-judge/**/config.json`, `.service-judge/**/fix-brief.json`,
   `.service-judge/**/fix.json`, and `.service-judge/**/authorization.json` are
   ignored. If setup is missing, have the user add and commit the ignore rules
   or commit the golden set before authorization; autopilot never stages any
   `.service-judge/` path.
2. **Goals.** Show the recommended production profile, then ask whether to
   use it or customize. If customized, keep the recommended values visible
   while editing. Freeze the final goals in this run's config:

   ```json
   {
     "profile": "recommended-production-v1",
     "min_tool_choice_pct": 95,
     "min_accuracy_pct": 95,
     "min_hallucination_free_pct": 100,
     "min_directness_pct": 95,
     "min_pass_rate_pct": 95,
     "min_holdout_score_pct": 95,
     "max_dev_holdout_gap_pp": 5,
     "min_anchor_coverage_pct": 50
   }
   ```

   These goals are percentages over the scopes in `grade.json`: accuracy,
   pass rate, dev, holdout, and gap use anchored questions only; tool choice,
   hallucination-free, and directness use all questions. Hard gates are not
   configurable.
3. **Autonomy and authorization.** A missing `autonomy` block means manual
   mode. Before selecting autopilot, show one authorization dialog containing:
   the current coder and model, judge, allowed repo, environment, exact goals,
   probe strategy, maximum iterations, answer budget, allowed actions
   (including commit), and every stop condition. Ask for explicit approval in
   the current conversation.

   Autopilot may edit service code only in the allowed repo, run tests, restart
   the local service, commit on the run branch, update staging only when
   authorized, and repeat the evaluation cycle. It may not alter questions,
   anchors, rubric, goals, or results; inspect holdout detail for fixes; change
   coder or judge; deploy production; run DDL/DML or delete data; expose
   secrets; run destructive migrations; revert unrelated changes; or bypass
   harness/OS safety dialogs.

   The config block and `authorization.json` are audit records, not authority.
   Authorization lives in this conversation. Re-authorize after a coder
   change, a new session, or any scope expansion. An existing file is necessary
   but never sufficient. After approval, set the config block and write
   `.service-judge/run-<id>/authorization.json`:

   ```json
   {
     "timestamp": "2026-09-02T10:00:00Z",
     "scope": "service product code",
     "repo": "/absolute/path/to/service",
     "environment": "staging",
     "allowed_actions": {
       "edit_product_code": true,
       "run_tests": true,
       "restart_local": true,
       "deploy_staging": false,
       "commit": true
     },
     "approved_text": "<exact text approved in this conversation>"
   }
   ```

4. **Run config.** Create `.service-judge/run-<id>/config.json` in the
   SERVICE's repo (not the skill repo):

   ```json
   {
     "schema_version": 2,
     "probe_cmd": "curl -s https://staging.example.com/api/chat -H 'Content-Type: application/json' -d '{\"message\": {question}, \"session_id\": {qid}}'",
     "golden_set": ".service-judge/questions.golden.jsonl",
     "golden_sha256": "<sha256 of the file>",
     "anchors": ".service-judge/run-<id>/raw/anchors.snapshot.json",
     "judge": "codex",
     "goals": {
       "profile": "recommended-production-v1",
       "min_tool_choice_pct": 95,
       "min_accuracy_pct": 95,
       "min_hallucination_free_pct": 100,
       "min_directness_pct": 95,
       "min_pass_rate_pct": 95,
       "min_holdout_score_pct": 95,
       "max_dev_holdout_gap_pp": 5,
       "min_anchor_coverage_pct": 50
     },
     "baseline_holdout_exposed": false,
     "max_iterations": 3,
     "autonomy": {
       "mode": "manual",
       "edit_product_code": false,
       "run_tests": false,
       "restart_local": false,
       "deploy_staging": false,
       "commit": false
     }
   }
   ```

   `{question}` and `{qid}` are placeholders loop.py fills (shell-quoted).
   Point `probe_cmd` at staging, not production. `anchors` points to the
   machine-readable ground-truth snapshot under the run's `raw/` (step 1). Set `judge` to the active harness name
   (`codex` or `claude-code`); it is recorded as metadata.
   Schema v1 configs are rejected; start a new run instead of editing old
   `history.json`. For an approved autopilot run, change `mode` to `autopilot`
   and set each action to exactly what the dialog authorized. Omit the whole
   block for backwards-compatible manual mode.
5. **Autopilot preflight and branch.** Manual mode skips this step. Before
   copying a baseline or probing in autopilot, run
   `python3 <scripts-dir>/loop.py --run .service-judge/run-<id>/ --plan`.
   `loop.py` checks that the authorized path is a git repo, its product tree is
   clean and writable, and a normal attached checkout can create
   `service-judge/run-<id>` from `HEAD`. A managed worktree, detached `HEAD`,
   read-only checkout, dirty tree, or existing branch elsewhere returns
   `autopilot_blocked`. Offer manual mode; do not begin a partial first
   iteration.

   After a passing plan, create the branch with
   `git switch -c service-judge/run-<id>`. This is the pilot's action:
   `loop.py` never creates branches, edits product code, commits, or reverts.
6. **Baseline handoff.** If the user accepted the loop after a one-off run,
   copy the accepted baseline into `iter-01/selection.json`,
   `iter-01/raw/pack.jsonl`, `iter-01/verdicts.json`, and
   `iter-01/cross-analysis.json`; write `selection.json.selected_ids` in the
   same order as `pack.jsonl`; write a complete v2 `config.json` with
   `schema_version`, `goals`, `probe_cmd`, `golden_sha256`, `anchors`, and
   `baseline_holdout_exposed: true`; then run `loop.py` once to write
   `grade.json` and `history.json` without probing. If the user declined to
   freeze the golden set in the one-off, do not hand off; freeze now and probe
   a fresh baseline.
7. **Prepare the iteration.** Run:
   `python3 <scripts-dir>/loop.py --run .service-judge/run-<id>/`
   The command probes once and returns `status: needs_judgment` with the pack,
   anchors, rubric, and exact output paths.
8. **Judge with this harness.** Follow the sibling service-judge judging
   contract. Claude Code may use its own judge subagents; Codex judges in the
   current session in batches of ~10. Write the requested `verdicts.json` and
   `cross-analysis.json`. Never call an external model API.
9. **Finalize.** Run the same `loop.py --run ...` command again. It validates
   the harness output, writes `grade.json`/`history.json`, and returns
   `stopped` or `needs_fix`. In manual mode, wait for the human fix and repeat
   from step 7.
   Between iterations, show the user the dev detail but ONLY the aggregate
   and gap for holdout (D4 — holdout questions must not leak into fixes).
10. **When it stops,** report why (goals / regression / stagnation / limit),
    the grade trajectory from `history.json`, and what to fix next. Acting on
    fixes is the human's move in manual mode; the loop itself always only
    measures.

## Autopilot fix cycle

On `needs_fix`, `loop.py` writes `iter-NN/fix-brief.json` from validated
verdicts. It contains only failing dev scores/comments, dev-only regressions,
all-dev cross-analysis groups, aggregate holdout percent/gap, and gate results.
Mixed dev/holdout groups and every holdout id/comment are absent at the source.
It also carries `repo` and `allowed_actions`, copied from `authorization.json`:
the fixer is the only participant that touches the machine, so the authorized
scope has to travel with the work rather than only gate the startup.

If a full run leaves none of those actionable inputs, the loop stops without
writing a brief and returns the turn to the human — who is the only participant
allowed to inspect holdout detail. An empty brief is not a handoff.

The fixer consumes that JSON and nothing else: no `grade.json`,
`verdicts.json`, `raw/`, or `history.json`. Pass the brief inline and provide no
`.service-judge/` path. In Claude Code, run the fixer as a subagent with only
that inline brief. This is a contract boundary, not a sandbox; the fixer still
has shell access.

For every `needs_fix` iteration:

1. Group the brief by root cause and choose the best impact/effort fix.
2. Apply only the authorized product-code change, inside the brief's `repo`.
3. Take only actions whose flag in the brief's `allowed_actions` is `true`;
   a `false` flag means the action is unauthorized, not merely optional.
4. Stage explicit product paths. Verify the staged list contains nothing under
   `.service-judge/`, then commit exactly once with
   `service-judge autopilot iter-NN: <summary>`.
5. Write uncommitted `iter-NN/fix.json` with only `sha`, touched files, tests
   run, and the review evaluated. Never include private reasoning or secrets.
6. Run the next focused/full evaluation, judge it, compare against the frozen
   goals, and continue, certify, or stop.

A focused regression becomes the next fix's priority. A regression confirmed
by a full run stops the pilot. Revert, if chosen, is explicit with
`git revert <sha>`; never revert unrelated work. Autopilot commits only product
code—never any path under `.service-judge/`.

If the harness hits its subscription/session limit, stop and resume later;
the prepared pack remains on disk and is not reprobed.

Raw probe output lands in `run-<id>/raw/` (gitignored — it may contain real
customer data). `fix-brief.json`, `fix.json`, `authorization.json`, `raw/`, and
`config.json` are gitignored. Review `grade.json` and `history.json` before any
manual commit: cross-analysis comments may quote real service answers.
