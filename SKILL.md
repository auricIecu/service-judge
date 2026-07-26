---
name: service-judge
description: Evaluate an LLM-powered service end-to-end. Discovers the repo, database, and observability; generates a question set with a cheap model; probes the live service; judges every answer against ground-truth anchors with the strongest available Claude model (Fable 5 by default); and delivers a per-question scorecard, a global grade, and prioritized improvement proposals. Use when the user wants to evaluate, audit, grade, score, or QA their AI service, agent, chatbot, or LLM app.
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
available Claude model — default `claude-fable-5`; if unavailable, escalate
down (Opus, then Sonnet) and record which judge was used.
- Claude Code: run the judge as a subagent with the strongest model.
- claude.ai chat: tell the user exactly when to switch models with the model
  picker, then judge in-session.
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

## Large runs (optional, advanced)

For 100+ question sets, repeated runs, or CI, `scripts/batch_eval.py` runs the
per-answer scoring of the judging phase via the Anthropic API (Batches −50%, prompt caching; the cross-answer pass stays in-session). It needs a
terminal and an `ANTHROPIC_API_KEY`. Only mention it if the user asks about
automation or the set is large; never require it.
