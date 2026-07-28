---
name: service-judge
description: >-
  Use this skill whenever the user wants to know how well their AI chatbot,
  assistant, agent, or LLM-powered service actually answers questions — e.g.
  "evaluate my chatbot", "audit my agent", "QA my bot", "benchmark answer
  accuracy", "test it before launch/production", or "is it hallucinating?".
  It runs a full LLM-as-judge evaluation: generates a realistic question set,
  fires it at the live service (or user-provided outputs), checks answers
  against ground truth (such as the service's own database), and scores every
  answer with a strong judge model. Delivers a per-question scorecard, an
  overall grade, and a prioritized fix list. Trigger for answer-quality or
  accuracy evaluation requests in any language, even casual or indirect ones
  ("how good is my bot?", "¿qué tan bien responde mi agente?"). Do NOT use
  for reviewing code for bugs/security, writing unit tests, deploying
  services, debugging runtime errors, analyzing existing A/B test data, or
  grading human-written content.
license: MIT (see LICENSE)
compatibility: >-
  Works in Claude Code, claude.ai, Codex CLI, and any SKILL.md-compatible
  agent. Richest results with shell + network access (to probe the live
  service) and read-only database access (for ground-truth anchors);
  degrades gracefully without either.
metadata:
  author: auricIecu
  version: "1.2"
---

# service-judge

You are about to run a rigorous LLM-as-judge evaluation of the user's service.
Work through the 5 phases IN ORDER. Each phase has a gate: do not advance
until the gate is met. Load each phase's reference file ONLY when you enter
that phase (conserve context — long evals need it).

**Announce at start:** "Running service-judge: I'll discover your service's
context, generate questions, probe it, judge the answers, and hand you a
scorecard. Starting with discovery."
(Translate the announcement to the user's language.)

## Hard rules (apply to every phase)

1. Database access is **read-only** (`SELECT` only). Never DDL/DML.
2. Never print credentials, connection strings, or API keys in chat or report.
3. Never modify the user's repo. You propose improvements; you do not patch.
4. Tag every probe with an `eval-` prefixed session/request ID.
5. Before probing a live service, confirm with the user WHICH environment
   (staging vs production) you are hitting.
6. All user-facing output (the opening announcement AND the final report) is
   written in the USER'S conversation language. Internal work happens in
   English.

## Environment detection (do this first, silently)

Determine which tier you are in and adapt:

| Capability | How to check | If missing |
|---|---|---|
| Filesystem/shell | Can you run shell commands and read local files? | Use GitHub connector / ask user to paste key files |
| Network to service | Can you curl the service base URL? | Switch to "bring your outputs" mode (Phase 3 alt) |
| Database | Phase 1 cascade | Behavior-only eval, reduced confidence (flag it) |
| Subagents with model override | Claude Code only | Do everything in-session; ask user to switch models at the judge step |

Never fail hard because a capability is missing. Degrade per the table and
record every degradation — they all appear in the report's confidence notes.

## Phase 1 — Discovery

Load `references/discovery.md` and follow it.
**Gate:** you have produced the Context Brief and the user confirmed it
(especially: which environment to probe, and DB access mode).

## Phase 2 — Sizing

Check first: does `.service-judge/questions.golden.jsonl` exist? If yes,
offer to REUSE it — skips generation, keeps scores comparable across runs.
If the user declines, or the file doesn't exist, proceed exactly as before:
present the question-count menu (exact table and confidence wording in
`references/questions.md` §Sizing) and ask the user to pick 30 / 50 / 100.
**Gate:** user picked a size, or user chose to reuse the golden set.

## Phase 3 — Generation & probing

Load `references/questions.md` and follow it. Use the CHEAPEST capable model
available (in Claude Code: spawn subagents with a small/cheap model for
question generation; otherwise do it in-session). If Phase 2 reused the
golden set, skip generation and probe those questions directly.
**Gate (two independent conditions):**
(a) an answer pack exists (JSONL, one record per question) — from live probing,
or from user-provided outputs normalized into the same format if the service
is unreachable; AND
(b) anchors were extracted, OR explicitly marked absent — which flags
"reduced confidence: no ground truth" and carries into Phases 4–5.
Anchor extraction is NEVER skipped just because the service was unreachable:
if a database is reachable, extract anchors regardless of how the pack was
obtained.

## Phase 4 — Judging

Load `references/judging.md` and follow it. The judge must be the STRONGEST
model available in your environment. In Claude environments that is
`claude-fable-5` by default; if unavailable, escalate down (Opus, then
Sonnet). In other agents (Codex CLI, etc.) use the strongest reasoning model
you have. Always record which judge was used.
- Claude Code: run the judge as a subagent with the strongest model.
- claude.ai chat: tell the user exactly when to switch models with the model
  picker, then judge in-session.
- Agents without subagents (Codex CLI and similar): judge in-session,
  sequentially, batching ~10 questions per pass.
If the strongest reachable judge is WEAKER than the model that generated the
answers, warn the user and request an upgrade before judging; if they decline,
record "judge < judged" as a confidence caveat in the report.
**Gate:** every question has a verdict (score 0–5 + improvement comment).

## Phase 5 — Report & exit

Load `references/reporting.md`, fill `assets/report-template.md`, deliver the
report, and END the skill. If the probed endpoint persisted data, the report
lists the `eval-*` IDs used so the user can clean up. Do not start fixing the
service. The report is the artifact; acting on it is the user's next move
(offer to help as a separate task if they ask).

## API mode (non-interactive, for scripts/loop.py)

Trigger: the invocation names a run config file (e.g. "run service-judge in
API mode, config `.service-judge/run-<id>/config.json`"). If not triggered,
ignore this section entirely — human mode is unchanged.

Ask the user NOTHING; never wait for confirmation. All hard rules still
apply. `config.json` stores references (paths, env var names), never
credential literals.

Per-phase deltas (everything else follows the phase files):

1. **Discovery:** read `config.json`. No Context Brief confirmation — the
   config's environment field IS the confirmation (hard rule 5 was satisfied
   by whoever authored the config).
2. **Sizing:** load the golden set from the config's `golden_set` path and
   verify its sha256 against `golden_sha256`. Mismatch → abort.
3. **Probing:** NO question generation. Probe every golden question (dev AND
   holdout) against the configured service; write the pack to
   `<out_dir>/raw/pack.jsonl`. Anchors come from the config's `anchors`
   path; if absent, record the "no ground truth" degradation and continue.
4. **Judging:** judge model = the config's `judge_model`, EXACTLY. If
   unavailable, abort — no silent degradation (D5: a judge swap invalidates
   the run). Write `<out_dir>/verdicts.json` (array of verdict objects,
   schema in `scripts/providers/base.py`).
5. **Report:** no narrative. Write `<out_dir>/grade.json` (schema:
   `compute_grade` in `scripts/loop.py`) and print its content as the final
   message.

Abort contract: on any unrecoverable condition, print
`{"error": "<reason>", "phase": <n>}` as the final message and stop. The
caller treats any final output without a `total` field as a failed
iteration.

## Large runs (optional, advanced)

For 100+ question sets, repeated runs, or CI, `scripts/batch_eval.py` runs the
per-answer scoring of the judging phase via the Anthropic API (Batches −50%, prompt caching; the cross-answer pass stays in-session). It needs a
terminal and an `ANTHROPIC_API_KEY`. Only mention it if the user asks about
automation or the set is large; never require it. Warn before use: it sends
pack/anchor content — potentially real customer data — to the Anthropic API,
which the user must confirm is covered by their data-handling agreement.
