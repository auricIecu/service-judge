# Phases 2–3 — Sizing, question generation, probing

## Sizing (Phase 2)

Present exactly this menu (translate to the user's language):

| Tier | Questions | Use it for | Confidence (approx.) | Time |
|---|---:|---|---|---|
| Canary | 10–12 | Deciding whether this candidate is worth evaluating at all | none — it finds problems, it can't prove their absence | ~4 min |
| Diagnostic (recommended) | 30 | Measuring one domain, or one change you just made | ~82% (±18% sampling margin) | ~10 min |
| Release | 50 | Confirming a candidate that already passed the lower tiers | ~86% (±14%) | ~18 min |
| Expanded release | 100 | Confirming a high-stakes candidate | ~90% (±10%) | ~35 min |

With this honest framing: "These are sampling approximations (margin ≈ 1/√n),
not guarantees. 30 questions reliably surface systematic problems; rare
failures (<5% of interactions) need 100+. The canary proves nothing
statistically — it exists to stop a bad candidate before it costs you 100
answers."

Pick the smallest tier that answers the question actually being asked:

| What the user is doing | Tier |
|---|---|
| Checking whether a fix worked | The fixed questions + canary |
| Changed one mode's prompt or one tool | Canary, then diagnostic on that mode only |
| Comparing two models / prompt rewrites | Diagnostic on each, same question set |
| Shipping to production | Release, once, on the candidate that already won |

For `service-judge-loop` adaptive runs, pair the tier with the matching focused
iteration size:

| Golden set | Focused max | Regression sample |
|---:|---:|---:|
| Canary 10–12 | 10 | 3 |
| Diagnostic 30 | 15 | 4 |
| Release 50–100 | 20 | 5 |

**Repeats:** one run by default. Only repeat the same set 3× when the two
candidates are within noise of each other, the service is visibly stochastic,
or the user is establishing a formal baseline before production. Three repeats
of a release set is the single most expensive thing this skill can do — it is
a confirmation tool, never an exploration tool.

**Scope by change, not by inventory.** If discovery found six modes but the
user touched one, evaluate that one plus a small regression sample of the
rest. Offer to widen; never widen silently.

## Reuse (Phase 2, before anything else)

In-session judging is free by default; external judging uses that harness's
subscription. Answers are not free. Re-judge a stored pack instead of re-probing
whenever NONE of these changed since it was captured: service code/commit,
system prompt, tool schemas, candidate model, the underlying data the
questions touch, and the conversation fixtures. That covers the common cases:
changing the rubric, using a different judge, fixing a reporting error,
recomputing metrics, or running an extra cross-answer pass.

Re-probe when any of those DID change — a reused answer would be measuring
the old service.

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
REST endpoints that serve raw data, or independent source-system observability.
Traces from the evaluated chatbot are execution evidence, never anchors. Store it as
`raw/anchors.snapshot.json` — in a loop run that is
`.service-judge/run-<id>/raw/anchors.snapshot.json`, the only path the ignore
rules protect — keyed by canonical question id:

```json
{"Q01": {"anchor": "total_customers=24305", "query": "SELECT count(*) FROM customers"},
 "Q02": {"anchor": null, "note": "date beyond available data - trap"}}
```

A question is anchored only when its key exists and `anchor` is not `null`.
Before freezing, check that the reference answers the proposition actually
asked, at the requested entity, time and precision. An event's date or status
does not anchor its cause. When only partial facts are available, use
`"anchor": null` and preserve those facts and their provenance in `note`;
do not count them as accuracy coverage for a different proposition. For
example, delivery timestamps can anchor "when?" but not "why was it late?".
This applicability check happens before judging and is independent of the
service's answer. If a frozen run used an inapplicable anchor, preserve that
run and create a separately identified corrected snapshot/run; never rewrite
its history or silently change which answers count as anchored.
Questions with no extractable anchor use `"anchor": null` and can only certify
behavior dimensions plus plausibility. If more than half the set has no anchor,
warn the user before probing: exact accuracy certification will be limited by
anchor coverage.

## Question generation (cheap model)

Use the cheapest capable model. In Claude Code: dispatch subagents with a
small model (e.g. Haiku), one per mode, each given: the mode description, the
tool list, the anchor data available, and the trap-case quota. In claude.ai:
generate in-session.

Questions must be REALISTIC end-user questions (not test-suite phrasing),
in the language of the service's real users.

## Freeze as golden set (offer once)

After generating the set, offer ONCE to freeze it as
`.service-judge/questions.golden.jsonl` — shared across runs (it lives
outside any run directory; runs reference it by sha256). If the user
declines, or the set was reused from an existing golden set (Phase 2), skip
this and probe as usual.

If frozen, assign each question a `split`: ~70% `dev` / 30% `holdout`,
stratified by mode and by type (normal/trap), so every mode/type
combination keeps roughly the same dev:holdout ratio. One JSON object per
line:

    {"id":"Q01","mode":"<mode>","type":"normal|trap","split":"dev|holdout","question":"..."}

Holdout questions exist so a future loop can detect overfitting (dev score
up, holdout flat). This skill's human mode does NOT hide holdout from the
user — hiding it is the loop's job, not this one.

## Probing (Phase 3c)

- Fire each question at the confirmed endpoint with a session ID like
  `eval-<date>-Q<NN>`. One question per session unless testing multi-turn.
  Note: the `eval-` prefix trades stealth for auditability (it enables the
  cleanup list in the report). An "eval-aware" service could special-case
  these requests to look better; for adversarial or pre-launch audits, offer
  the user non-obvious session IDs and cross-check a few questions against
  unmarked requests.
- Probe the canary first (see §Canary gate below), not the whole set.
- Record per question: `{id, mode, question, answer, tools_called, tool_results, model,
  latency_ms, error}` — one JSON object per line (the "pack"). When the
  service's response, its logs, or observability expose usage, also record
  `{model_generations, input_tokens, cached_input_tokens, output_tokens}`.
  `model_generations` is how many times the model was called for that one
  question (tool loops make it >1) — it is what makes an eval expensive, so
  capture it whenever it's available and say so in the report when it isn't.
  Record `tools_called: []` when no tool ran; reserve `null` for unavailable
  telemetry. Record `tool_results` as the non-null, non-empty JSON value exposed
  by responses, logs, or observability; otherwise omit it. Booleans, blank
  strings, empty containers, and lists of empties do not count as captured.
  Never turn unavailable evidence into an empty trace.
- The pack `id` IS the canonical question key `Q<NN>` (e.g. `Q07`): the same
  key used in `anchors.snapshot.json`, in the scorecard's `#` column, and as the suffix
  of the probe session ID (`eval-<date>-Q<NN>`).
- On transport errors: retry once, then record the error as the answer
  (a service that 503s IS a finding, not a skipped question).
- Circuit breaker: if more than ~30% of a mode's probes error consecutively,
  pause that mode and flag it to the user before continuing — don't keep
  hammering an already-degraded service.
- Throttle: stay under ~2 req/s unless the user says otherwise.

## Optional Langfuse evidence

Enable only for the project/environment being evaluated. In loop `config.json`:

```json
{"langfuse": {"base_url": "https://cloud.langfuse.com", "wait_seconds": 10}}
```

Credentials come from evaluator-only `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` environment variables, never config or command arguments.
`base_url` defaults to `LANGFUSE_BASE_URL`, then the EU Cloud URL above; set it
for US Cloud or self-hosting. HTTPS is required except on loopback. This uses
the [Observations API v2](https://langfuse.com/docs/api-and-data-platform/features/observations-api)
(Cloud or self-hosted v4+), with Basic Auth, cursor pagination and a bounded
time window. Unsupported servers degrade to missing evidence; no SDK dependency.
Older ingestion SDKs can make traces visible after the configured deadline.

The probe adapter preserves the IDs returned by that exact service request:

```json
{"answer":"42","tools_called":null,"trace_id":"returned-trace-id","session_id":"returned-session-id"}
```

`trace_id` takes precedence. Never query the latest trace or match by question
text. With only `session_id`, enable `"session_per_request": true` **only if the
adapter creates a fresh, exclusive session for every execution**, including
retries and iterations. A session shared by concurrent requests or reused
across iterations cannot identify this answer; supply a trace ID instead.
The loop's stable `{qid}` (`eval-Q01`) is not a unique execution ID; the adapter
must create and return the exclusive service session. Without safe correlation,
the answer is still judged and `trace_evidence.reason` records the limitation.

The collector fills absent/null `tools_called`, `tool_results`, `model`,
`model_generations` (count), `input_tokens`, `cached_input_tokens`, `output_tokens`,
`latency_ms`, `trace_url`, and `cost_usd` (observed generation costs in USD).
Existing service telemetry wins; conflicting details remain visible in the
snapshot. Typed `TOOL` observations carry arguments/results. For integrations
that emit tools as generic `SPAN`s, list exact names in `langfuse.tool_names`;
other spans are not guessed to be tools. Absent tool spans do not prove zero
tool calls. Usage is summed across observed generations only when each exposes
the relevant metric; absent usage remains unknown. Root start/end times give
execution latency, excluding the wait for telemetry.

`trace_evidence` retains observations with model inputs/outputs, tools,
parent IDs, timestamps, levels/status messages, usage/costs, and exported retry
metadata. Repeated calls retain their distinct observation IDs; identical
calls alone do not prove a retry. This untrusted evidence explains execution;
accuracy still requires the independent anchors.

Each answer gets a sanitized `raw/langfuse-<question-hash>.json` snapshot
(directory mode 700, file mode 600). The enriched pack embeds the same evidence
for judging, so re-judging/reopening an iteration uses stored data without
querying Langfuse or the chatbot again. No collection occurs for an existing
pack, even if Langfuse is enabled later. For a one-off evaluation, reuse
`scripts/loop.py`'s `probe(questions, probe_cmd, langfuse=..., raw_dir=...)`
with that run's private raw directory; no improvement loop is required.

`wait_seconds` is a total per-answer budget, integer 1–60 (default 10).
`available` means a non-empty snapshot with ended roots/spans was stable on
two reads, not a guarantee that all asynchronous SDK batches have arrived.
`partial` preserves the observations fetched before timeout/error;
`missing` means none were usable. Auth/quota errors stop collection promptly;
delayed ingestion and transient failures retry only within the budget.
Pagination is capped at 100 cursors / 8 MiB per sweep. Never turn missing
telemetry into a zero cost or empty tool history. Report partial metrics as
observed lower bounds, not complete totals; report missing evidence as reduced
causal confidence, not a chatbot failure.

Before saving or judging, redact sensitive fields, known credential values and
authorization strings, including serialized JSON. Also mask secrets at the
service's instrumentation source: arbitrary secrets in free text cannot be
recognized reliably. Keep raw data private and gitignored; do not publish traces.
The fixer gets only reviewed dev conclusions, never these snapshots or Langfuse
access. Follow the loop skill's enforced-isolation rule before autopilot.

## Canary gate (Phase 3c → 4 → 3c)

Probe the first 10–12 questions, judge them with the Phase 4 rules, and only
then decide whether to probe the rest.

Compose the canary from the highest-risk cases, not the first ones generated —
roughly: 3–4 happy paths across the most important modes, one question whose
data does not exist, one guardrail, one isolation/permission case (if the
service has tenants, sellers, or per-user scope), one ambiguous phrasing, and
one numeric comparison that a second tool could contradict.

**Abort immediately** — skip to Phase 5, report what you have and why you
stopped — on any of:

- data from another tenant / account / user leaked into an answer
- a guardrail or authorization check was bypassed
- a financial or inventory figure is severely wrong (not a rounding nit)
- the service performed a destructive or unauthorized side effect
- more than 20% of the canary scored ❌
- the answering model is not the one the user declared (check the response
  metadata or traces, don't assume)
- you are hitting an environment other than the one confirmed in Phase 1

An abort is a completed evaluation with a decisive finding, delivered for the
price of 12 answers. Say that plainly in the report — do not apologise for a
short run or offer to "continue anyway" unless the user asks.

**Continue to the rest of the set** when the canary has zero critical
failures, at most one non-systemic minor observation, and the environment,
model and data all check out.

## "Bring your outputs" fallback

If the service is unreachable (or this is claude.ai without network): ask the
user to run the questions themselves (give them the list + a copy-paste
script if they have a terminal) or to upload/paste existing transcripts.
Normalize whatever they bring into the same pack format. Anchors still come
from the DB if available.
