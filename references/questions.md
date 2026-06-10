# Phases 2–3 — Sizing, question generation, probing

## Sizing (Phase 2)

Present exactly this menu (translate to the user's language):

| Questions | Confidence (approx.) | Time (approx.) |
|---|---|---|
| 30 | ~82% (±18% sampling margin) | ~10 min |
| 50 | ~86% (±14%) | ~18 min |
| 100 | ~90% (±10%) | ~35 min |

With this honest framing: "These are sampling approximations (margin ≈ 1/√n),
not guarantees. 30 questions reliably surface systematic problems; rare
failures (<5% of interactions) need 100+."

## Coverage matrix (Phase 3a)

Distribute the chosen N across:
- **Every mode** discovered in Phase 1, proportionally to importance (ask the
  user if unsure which modes matter most).
- **Trap cases — reserve ~15-20% of N for these:**
  - a question whose data does NOT exist (tests honest "I don't know")
  - a question with no suitable tool available (tests guardrails)
  - a question about a known-degraded dependency, if discovery found one
  - a numeric question answerable by two different tools (tests consistency)
  - 2–3 PAIRS of questions whose answers must agree (contradiction bait)

## Anchors (Phase 3b) — extract BEFORE probing

For every question that has a verifiable answer, extract the ground truth via
a path that does NOT go through the evaluated LLM: direct SQL (read-only),
REST endpoints that serve raw data, observability traces. Store as
`anchors.md`: one line per question id — `Q07: total_customers=24305
(SELECT count(*) FROM customers)` — value AND provenance.

Questions with no extractable anchor are marked `anchor: none` and will only
be judged on behavior. If more than half the set has no anchor, warn the user
before probing: the grade will be mostly behavioral.

## Question generation (cheap model)

Use the cheapest capable model. In Claude Code: dispatch subagents with a
small model (e.g. Haiku), one per mode, each given: the mode description, the
tool list, the anchor data available, and the trap-case quota. In claude.ai:
generate in-session.

Questions must be REALISTIC end-user questions (not test-suite phrasing),
in the language of the service's real users.

## Probing (Phase 3c)

- Fire each question at the confirmed endpoint with a session ID like
  `eval-<date>-Q<NN>`. One question per session unless testing multi-turn.
- Record per question: `{id, mode, question, answer, tools_called, model,
  latency_ms, error}` — one JSON object per line (the "pack").
- The pack `id` IS the canonical question key `Q<NN>` (e.g. `Q07`): the same
  key used in `anchors.md`, in the scorecard's `#` column, and as the suffix
  of the probe session ID (`eval-<date>-Q<NN>`).
- On transport errors: retry once, then record the error as the answer
  (a service that 503s IS a finding, not a skipped question).
- Throttle: stay under ~2 req/s unless the user says otherwise.

## "Bring your outputs" fallback

If the service is unreachable (or this is claude.ai without network): ask the
user to run the questions themselves (give them the list + a copy-paste
script if they have a terminal) or to upload/paste existing transcripts.
Normalize whatever they bring into the same pack format. Anchors still come
from the DB if available.
