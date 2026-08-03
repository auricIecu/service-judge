# service-judge — Design Spec

> Historical note: direct model-API execution described here was removed in
> v1.3. Judging now uses only the active Claude Code or Codex harness limits.

**Date:** 2026-06-10
**Status:** Approved design, pending implementation plan
**Owner:** Likeik CX (ComercialLikeik)

## 1. What it is

`service-judge` is a public Agent Skill that evaluates an LLM-powered service or app the way a rigorous QA engineer would: it discovers the project's context (repo, database, observability), generates a question set with a cheap model, probes the live service, has the strongest available model (Claude Fable 5 by default) judge every answer against ground-truth anchors, and delivers a per-question scorecard, a global grade, and ROI-prioritized improvement proposals. Then it exits, leaving the report as an artifact the user acts on.

The methodology is a productization of a manual eval performed on BIwise (2026-06-10, scored 107/150) where the LLM-as-judge pattern with ground-truth anchors surfaced broken tools, hallucinated narratives over placeholder data, and false-positive guardrails that no assert-based test had caught.

**Format decision:** pure Agent Skill (a folder with `SKILL.md` + resources). This is the only format that works today across Claude Code (local and web), claude.ai chat, and the API. Not a plugin (Claude Code only), not an MCP server (requires hosted infra for web users).

**Language:** all skill content in English. The final report is always written in the user's conversation language (instructed inside the skill).

## 2. User journey (the 5 phases)

The user invokes the skill (`/service-judge` in Claude Code, or by request in claude.ai with the skill enabled).

### Phase 1 — Discovery (automatic)
- **Repo scan:** stack, API endpoints, service modes/features, system prompts/tools if it's an agent.
- **Database detection cascade:** (1) repo config/`.env` (`DATABASE_URL`, `SUPABASE_URL`, ORM configs) → (2) MCP tools available in the session (Supabase/Postgres MCP) → (3) ask the user for a **read-only** connection string → (4) if nothing, propose how to set up a connector. 
- **Observability detection:** Langfuse, LangSmith, Braintrust, structured logs. If found, traces become an extra anchor source.
- Output: a **context brief** ("Your service is X with modes A/B/C/D; DB reachable ✅; observability not found ⚠️ — anchor confidence reduced").

### Phase 2 — Sizing
The user picks the question count; each option shows approximate statistical confidence (sampling margin ≈ 1/√n) and time:

| Questions | Confidence (approx.) | Time (approx.) |
|---|---|---|
| 30 | ~82% (±18% margin) | ~10 min |
| 50 | ~86% (±14%) | ~18 min |
| 100 | ~90% (±10%) | ~35 min |

The skill states honestly: these are sampling approximations, not guarantees; 30 questions detect systematic problems, rare problems (<5% of cases) need 100+.

### Phase 3 — Generation & probing (cheap model)
- A cheap model (Haiku subagents in Claude Code; the session model in web) generates questions spread over a **coverage matrix**: every mode/feature, plus trap cases (question with no tool available, nonexistent data, known-degraded dependency).
- **Ground-truth anchors** are extracted from the DB/observability via paths that do NOT go through the evaluated LLM (direct SQL, dashboard endpoints).
- Questions are fired at the live service tagged with `eval-*` session IDs.
- Output: a JSONL pack `{mode, question, answer, tools_called, model, error}` + an anchors file.

### Phase 4 — Judging (strongest model)
- Default judge: **Claude Fable 5**. If unavailable, escalate down: Opus → Sonnet. The report records which judge was used.
- In Claude Code: the judge runs as a subagent with model override — automatic.
- In claude.ai web: the skill tells the user "switch to Fable 5 now with the model picker", then judges in-session.
- Rubric per answer: appropriate tool · numbers match anchor · hallucination (numeric AND interpretive) · answers directly · score 0–5.

### Phase 5 — Report & exit
- Per-question table: ✅/⚠️/❌ + an improvement comment each.
- **Global service grade** (points / total, percentage, per-mode breakdown).
- **Improvement proposals prioritized by ROI.**
- The skill exits. The report is the artifact; acting on it happens outside the skill.

## 3. Two-tier environment support

| Environment | Repo | DB | Live probing | Cheap/strong model split |
|---|---|---|---|---|
| Claude Code (local) | full FS access | env/MCP/conn string | curl, any reachable service | native (subagents with model override) |
| Claude Code (web) | GitHub clone in sandbox | MCP or conn string | only if API public + domain allowed | native |
| claude.ai chat | GitHub connector (read) | MCP connectors only | not possible (no open network) → user provides outputs | manual model switch, guided |

The skill detects its environment at Phase 1 and adapts; it never fails hard because a capability is missing — it degrades (see §5).

## 4. Judging rigor (distilled from the BIwise eval)

- **Judge ≥ judged:** if the session model is weaker than the model that produced the answers, warn and ask to upgrade.
- **No anchors → no accuracy grade:** without DB/observability ground truth, the judge only scores plausibility/behavior and the report flags "reduced confidence: no ground truth" prominently.
- Hunt what asserts can't see: contradictions BETWEEN answers; "technical error" replies when the anchor shows the data EXISTS (tool bug, not data bug); narratives over artifacts (placeholder months read as business collapse); guardrails firing falsely (error fallbacks answering with out-of-scope messages); tables that must SUM.
- **Anti-bias:** judge each answer against the rubric and the anchor, never against "what the judge would have answered"; assign the score before writing the improvement comment.

## 5. Security rules (non-negotiable, written into the skill)

- DB access is **`SELECT` only**. Never DDL/DML. Credentials are never printed in chat or report.
- Repo is read-only. The skill proposes improvements; it never patches code.
- Probe questions carry identifiable `eval-*` session IDs; if the endpoint persists data, the report says so for cleanup.
- Before probing, confirm with the user which environment is targeted (staging vs production).

## 6. Graceful degradation

| Missing | Behavior |
|---|---|
| Service down / unreachable | "Bring your outputs" mode: user pastes/uploads answers; judging proceeds |
| No DB and no observability | Behavior-only eval, with reduced-confidence warning |
| Fable 5 unavailable | Escalate Opus → Sonnet; report records the judge used |
| Web chat (no network) | Repo via GitHub connector, DB via MCP, outputs provided by user |

## 7. Repo structure

```
service-judge/
├── README.md             ← what it is, install (CC + claude.ai), example report
├── SKILL.md              ← orchestrator: 5 phases, gates, CC/web environment detection
├── references/
│   ├── discovery.md      ← repo scan + DB cascade + observability detection
│   ├── questions.md      ← coverage matrix, trap cases, anchor extraction
│   ├── judging.md        ← rubric, anti-bias, judge≥judged, contradiction hunting
│   └── reporting.md      ← table template, global grade, ROI proposals, confidence math
├── assets/
│   └── report-template.md
├── scripts/
│   └── batch_eval.py     ← optional API harness (Batches API −50%, prompt caching, structured outputs) — experimental, terminal-only
└── docs/specs/           ← this document
```

Progressive disclosure: `SKILL.md` stays slim (flow + gates only); each phase's detail loads from `references/` only when that phase starts. This conserves context during long evals and follows Anthropic's skill-authoring guidance.

## 8. Acceptance test (dogfooding)

Re-run the BIwise eval **through the skill**: same service, new Supabase DB. The skill passes if it reproduces the manual eval end-to-end (discovers the repo, finds the DB, generates, probes, Fable judges, table + grade + proposals) without the operator improvising outside its instructions. Side benefit: validates the 2026-06-10 DB migration end-to-end and shows whether the 71% moved.

## 9. Distribution

1. **Claude Code:** `npx skills add ComercialLikeik/service-judge` → installs to `~/.claude/skills/`.
2. **claude.ai web:** zip the folder → Settings → Capabilities → Skills → Upload.
3. Public GitHub repo `ComercialLikeik/service-judge`; README documents both paths.

## 10. Out of scope (v1)

- CI/scheduled eval runs (the `batch_eval.py` harness is included but marked experimental).
- A standalone UI.
- Non-Claude judge models.
- Writing fixes to the evaluated repo.
