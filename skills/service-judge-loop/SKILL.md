---
name: service-judge-loop
description: >-
  Set up and run the autonomous improvement loop around a service-judge
  evaluation: freeze a golden question set, write the run config, and drive
  scripts/loop.py, which re-evaluates the service after each human fix until
  the quality gates pass (or it detects regression, stagnation, or the
  iteration limit). Use when the user wants to iterate on their AI service's
  quality over multiple eval runs — "keep evaluating until it passes",
  "track whether my fixes improved the bot", "run the eval loop". For a
  single one-off evaluation, use the service-judge skill instead.
license: MIT (see LICENSE)
metadata:
  author: auricIecu
  version: "1.5.0"
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
write `grade.json` (per-question scores, cross-answer findings, dev/holdout
aggregates, gates) → append to `history.json` → stop or wait for the next
human fix.

It stops on quality gates passed, regression (it notifies — never reverts),
stagnation (<2pp improvement twice in a row), or the iteration limit. LLM
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
2. **Run config.** Create `.service-judge/run-<id>/config.json` in the
   SERVICE's repo (not the skill repo):

   ```json
   {
     "probe_cmd": "curl -s https://staging.example.com/api/chat -H 'Content-Type: application/json' -d '{\"message\": {question}, \"session_id\": {qid}}'",
     "golden_set": ".service-judge/questions.golden.jsonl",
     "golden_sha256": "<sha256 of the file>",
     "judge": "codex",
     "max_iterations": 3
   }
   ```

   `{question}` and `{qid}` are placeholders loop.py fills (shell-quoted).
   Point `probe_cmd` at staging, not production. Optional key: `anchors`
   (path to the ground-truth file). Set `judge` to the active harness name
   (`codex` or `claude-code`); it is recorded as metadata.
3. **Prepare the iteration.** Run:
   `python3 <scripts-dir>/loop.py --run .service-judge/run-<id>/`
   The command probes once and returns `status: needs_judgment` with the pack,
   anchors, rubric, and exact output paths.
4. **Judge with this harness.** Follow the sibling service-judge judging
   contract. Claude Code may use its own judge subagents; Codex judges in the
   current session in batches of ~10. Write the requested `verdicts.json` and
   `cross-analysis.json`. Never call an external model API.
5. **Finalize.** Run the same `loop.py --run ...` command again. It validates
   the harness output, writes `grade.json`/`history.json`, and returns
   `stopped` or `needs_fix`. If `needs_fix`, wait for the human fix and repeat
   from step 3.
   Between iterations, show the user the dev detail but ONLY the aggregate
   and gap for holdout (D4 — holdout questions must not leak into fixes).
6. **When it stops,** report why (gates / regression / stagnation / limit),
   the grade trajectory from `history.json`, and what to fix next. Acting on
   fixes is the human's move; the loop only measures.

If the harness hits its subscription/session limit, stop and resume later;
the prepared pack remains on disk and is not reprobed.

Raw probe output lands in `run-<id>/raw/` (gitignored — it may contain real
customer data). `grade.json` and `history.json` are safe to commit.
