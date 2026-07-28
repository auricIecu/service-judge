---
name: service-judge-loop
description: >-
  Set up and run the autonomous improvement loop around a service-judge
  evaluation: freeze a golden question set, write the run config, and drive
  scripts/loop.py, which re-evaluates the service after each human fix until
  the quality gates pass (or it detects regression, stagnation, or budget
  exhaustion). Use when the user wants to iterate on their AI service's
  quality over multiple eval runs — "keep evaluating until it passes",
  "track whether my fixes improved the bot", "run the eval loop". For a
  single one-off evaluation, use the service-judge skill instead.
license: MIT (see LICENSE)
compatibility: >-
  Needs a terminal, a checkout of the service-judge repo (the loop runs
  scripts/loop.py from it), network access to the evaluated service, and an
  ANTHROPIC_API_KEY for the batch scorer.
metadata:
  author: auricIecu
  version: "1.3"
---

# service-judge-loop

You orchestrate the improvement loop, not the evaluation itself. The
evaluation contract (phases, rubric, API mode) lives in the sibling
`service-judge` skill; the loop machinery lives in its `scripts/` directory —
in an installed plugin that is `../service-judge/scripts/` relative to this
file; otherwise use a clone of https://github.com/auricIecu/service-judge.

## What the loop does

`scripts/loop.py --run .service-judge/run-<id>/` executes iterations of:
probe every golden question against the service → score answers via the
Anthropic Batches API with a pinned judge → write `grade.json` (per-question
scores, dev/holdout aggregates, gates) → append to `history.json` → stop or
wait for the next human fix.

It stops on its own for exactly four reasons (D7): quality gates passed,
regression detected (it notifies — never reverts), stagnation (<2pp
improvement twice in a row), or iteration/token budget exhausted.

## Your job when this skill triggers

1. **Golden set.** The loop needs a frozen question set with a dev/holdout
   split at `.service-judge/questions.golden.jsonl` (schema: one
   `{id, mode, type, split, question}` per line). If the user ran the
   service-judge skill before, Phase 3 offered to freeze one — reuse it.
   Otherwise generate one now following the service-judge skill's Phase 2–3,
   then freeze it and record its sha256.
2. **Run config.** Create `.service-judge/run-<id>/config.json` in the
   SERVICE's repo (not the skill repo):

   ```json
   {
     "probe_cmd": "curl -s https://staging.example.com/api/chat -H 'Content-Type: application/json' -d '{\"message\": {question}, \"session_id\": {qid}}'",
     "golden_set": ".service-judge/questions.golden.jsonl",
     "golden_sha256": "<sha256 of the file>",
     "judge_model": "claude-fable-5",
     "max_iterations": 3
   }
   ```

   `{question}` and `{qid}` are placeholders loop.py fills (shell-quoted).
   Point `probe_cmd` at staging, not production. Optional keys: `anchors`
   (path to ground-truth file), `max_total_tokens`.
3. **Run it.** `ANTHROPIC_API_KEY` must be set. Then:
   `python3 <scripts-dir>/loop.py --run .service-judge/run-<id>/`
   (where `<scripts-dir>` is the service-judge scripts location resolved
   above).
   Between iterations, show the user the dev detail but ONLY the aggregate
   and gap for holdout (D4 — holdout questions must not leak into fixes).
4. **When it stops,** report why (gates / regression / stagnation / budget),
   the grade trajectory from `history.json`, and what to fix next. Acting on
   fixes is the human's move; the loop only measures.

Raw probe output lands in `run-<id>/raw/` (gitignored — it may contain real
customer data). `grade.json` and `history.json` are safe to commit.
