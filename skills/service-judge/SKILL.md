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
metadata:
  author: auricIecu
  version: "2.0.0"
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
3. Never modify the user's product code, except while the sibling
   `service-judge-loop` skill is in autopilot mode with explicit authorization
   granted in the current conversation for that repo and scope. Outside that
   single exception, propose improvements but do not patch. The ONLY files you
   write are eval artifacts (report, scorecard, pack, anchors), and only in the
   location the user approved in Phase 1: `eval-runs/` by default, `.context/`
   (or another gitignored path) if the repo must stay clean.
4. Tag every probe with an `eval-` prefixed session/request ID.
5. Before probing a live service, confirm with the user WHICH environment
   (staging vs production) you are hitting.
6. All user-facing output (the opening announcement AND the final report) is
   written in the USER'S conversation language. Internal work happens in
   English.
7. **Judging is free by default; answers are not.** The in-session judge uses
   the active harness subscription at no extra cost. An external judge consumes
   that other harness's subscription. Every question sent to the evaluated
   service does cost the user — one question can trigger several internal
   generations with long context. So the number to minimise is *answers
   requested*, never *judging thoroughness*. Never buy 100 answers to learn
   what 12 would have told you, and never re-probe for something a stored pack
   already answers (see `references/questions.md` §Reuse).

## Environment detection (do this first, silently)

Determine which tier you are in and adapt:

| Capability | How to check | If missing |
|---|---|---|
| Filesystem/shell | Can you run shell commands and read local files? | Use GitHub connector / ask user to paste key files |
| Network to service | Can you curl the service base URL? | Switch to "bring your outputs" mode (Phase 3 alt) |
| Database | Phase 1 cascade | Behavior/plausibility only; the loop cannot certify accuracy without anchors |
| Subagents with model override | Detect the active harness | Judge in-session if unavailable; ask user to switch models at the judge step |

Never fail hard because a capability is missing. Degrade per the table and
record every degradation — they all appear in the report's confidence notes.

## Phase 1 — Discovery

Load `references/discovery.md` and follow it.
**Gate:** you have produced the Context Brief and the user confirmed it
(especially: which environment to probe, and DB access mode).

## Phase 2 — Sizing

Check first: does a usable answer pack from a previous run already exist? If
the service hasn't changed in any way that alters its behaviour, re-judge that
pack instead of re-probing — zero new service answers
(`references/questions.md` §Reuse).

Then: does `.service-judge/questions.golden.jsonl` exist? If yes, offer to
REUSE it — skips generation, keeps scores comparable across runs.

Otherwise present the tier menu (exact table and confidence wording in
`references/questions.md` §Sizing) and ask the user to pick **canary (10–12)
/ diagnostic (30, recommended) / release (50) / expanded release (100)**. Recommend the smallest tier that
answers their actual question; if they are evaluating one specific change,
say so and weight the set toward the affected modes.
**Gate:** user picked a tier, or chose to reuse the golden set or a pack.

## Judge choice and external egress

Before Phase 3 spends answers, ask which judge to use with no preselected
option: the current session, Codex, Claude Code, or DeepSeek Harness. If the
user does not choose, use the current session; it is the free default. External
harnesses use their configured default model unless `judge_cmd` overrides it.
Show that configured default before requesting answers; do not claim to resolve
the "most capable" model automatically.

Before enabling an external judge, show that `{prompt}`, `{pack}`, `{rubric}`,
and `{anchors}` leave for the chosen harness and ask for explicit consent.
Record the same `external_judge` object in `<artifacts-dir>/authorization.json`,
where `<artifacts-dir>` is the Phase 1 location (`eval-runs/` by default):

```json
{
  "external_judge": {
    "timestamp": "2026-09-03T10:00:00Z",
    "harness": "codex",
    "files": ["{prompt}", "{pack}", "{rubric}", "{anchors}"],
    "approved_text": "<exact text approved in this conversation>"
  }
}
```

The authorization file is an audit record, not authority. The final report
records the consent, judge label, command SHA-256, and command redacted to its
first token; it never includes the literal `judge_cmd`.

## Phase 3 — Generation & probing (canary first)

Load `references/questions.md` and follow it. Use the CHEAPEST capable model
available (in Claude Code: spawn subagents with a small/cheap model for
question generation; otherwise do it in-session). If Phase 2 reused the
golden set, skip generation and probe those questions directly.

**Whatever tier was picked, probe a 10–12 question canary only, then stop and
judge it** (Phase 4 rules) before probing the rest — the canary gate. The
canary is the highest-risk slice of the set, not its first 12 rows.
Abort the run and go straight to Phase 5 if the canary shows a cross-tenant
leak, a bypassed guardrail, a severely wrong figure, an unauthorised side
effect, >20% ❌, the wrong answering model, or the wrong environment (full
list and canary composition in `references/questions.md` §Canary gate).
Aborting after 12 answers is a successful run, not a failed one: report the
finding and the reason. If the tier was `canary`, stop here regardless.

**Gate (two independent conditions):**
(a) an answer pack exists (JSONL, one record per question) — from live probing,
or from user-provided outputs normalized into the same format if the service
is unreachable; AND
(b) `raw/anchors.snapshot.json` exists, with `anchor: null` for questions
without ground truth, OR anchors are explicitly absent — which flags
"reduced confidence: no ground truth" and carries into Phases 4–5.
Anchor extraction is NEVER skipped just because the service was unreachable:
if a database is reachable, extract anchors regardless of how the pack was
obtained.

## Phase 4 — Judging

Load `references/judging.md` and follow it. The judge must be the STRONGEST
model available in the default in-session mode. In Claude environments that is
`claude-fable-5` by default; if unavailable, escalate down (Opus, then
Sonnet). In other agents (Codex CLI, etc.) use the strongest reasoning model
you have. Always record which judge was used.
- Claude Code: run the judge as a subagent with the strongest model.
- claude.ai chat: tell the user exactly when to switch models with the model
  picker, then judge in-session.
- If subagents are unavailable, judge in-session, sequentially, batching ~10
  questions per pass.
- If the user chose an external harness, use its approved `judge_cmd` and the
  warning and audit rules in `references/judging.md`.
In the default in-session mode, if the strongest reachable judge is WEAKER
than the model that generated the answers, warn the user and request an upgrade
before judging; if they decline, record "judge < judged" as a confidence
caveat in the report. For an external judge, use the auditable warning in
`references/judging.md`; its strength cannot be verified.
**Gate:** every question has a verdict object with dimensions, score 0–5,
`unanchored`, improvement comment, and the three critical booleans.

## Phase 5 — Report & exit

Load `references/reporting.md`, fill `assets/report-template.md`, deliver the
report, and END the skill. If the probed endpoint persisted data, the report
lists the `eval-*` IDs used so the user can clean up. Do not start fixing the
service. The report is the artifact; acting on it is the user's next move
(offer to help as a separate task if they ask).
