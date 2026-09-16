# Tool authorship and the repair loop — 2026-09-16

Scope: Service Judge only. No new Orito answers, edits or deployments were
performed for this work. All new cases and service executions below are synthetic.

## Defects reproduced and corrected

The validator and rubric required every `failure_source: tool` to also set
`broken_tool`. That conflated cause with critical severity: a deterministic
renderer omitting a requested date could not retain its real cause without a
false critical flag. Tool attribution now requires captured tool evidence;
critical flags are assessed independently. Correct tool content subsequently
dropped by the model is attributed to the model. Missing authorship evidence
remains unknown. Incorrect retrieved facts remain critical.

A second engine defect dropped noncritical 4/5 answers from the fix brief and
adaptive focus, even when their missing dimension prevented the selected goals
from passing. Those demonstrated defects now remain actionable. Clean unanchored
4/5 answers are not defects. A clean focused pass no longer emits an empty coder
brief; the orchestrator proceeds to the full exam without an empty commit.

Each engine defect was reproduced by a failing executable assertion before
the implementation change. The Python unit and end-to-end suites were rerun.
Final independent review found one remaining path: legacy-valid anchored
4/5 verdicts could claim `failure_source: none`. A new failing test reproduced
the empty-brief outcome. The validator now rejects `none` below the question's
ceiling, and improvement selection uses score/ceiling so historical rows are
still targeted. Re-judging saved affected verdicts costs no new service probes.

## Frozen calibration

Codex `gpt-6-astra`, reasoning `high`, fresh read-only session per repetition.
The same six cases were judged five times with the old rubric and five times
with the candidate. An independent agent created six library-domain cases
without reading the rubric or candidate implementation, froze their expectations,
and returned only their file hash before candidate testing. Expected labels
never entered judge inputs. No failed attempt was replaced or retried.

| Set | Cases × repetitions | Per-answer comparison |
|---|---:|---|
| Old rubric, authorship control | 6 × 5 | 20/30 match; nine false critical flags and one wrong source |
| Candidate, same control | 6 × 5 | 30/30 match |
| Independent frozen reserve | 6 × 5 | 30/30 match all predeclared fields |
| Original regression cases | 12 × 1 | 12/12 sources and critical flags match |
| Evidence, causal, unsafe and accuracy regressions | 36 × 1 | 36/36 predeclared fields match |

The reserve intentionally left directness unspecified for two partial-answer
cases because its independent author had not read the rubric. The judge assigned
directness 0 in all ten judgments; this is observed stability, not agreement
with retroactively invented labels. The other 22 predeclared dimension values
matched in every repetition. The reserve SHA-256 before and after testing was
`1065ea9964c8910ba1bc20088dd6794b361eadc5c7d4ff41d58d029455340249`.

Cross-answer output is preserved, not included in the per-answer match rate.
One old-rubric repetition grouped two conflicting energy answers; the others,
including the candidate, did not. This test does not establish exhaustive or
stable cross-answer detection. Per-answer detection of the wrong tool value
remained present in every repetition, independently blocking the hard gate.

## Actual agent-driven local loop

The acceptance service retrieves four fixed energy readings and renders the
final answer deterministically. The frozen exam has two development and two
holdout questions. Goals: all quality minimums 100%, maximum dev/holdout gap 0;
adaptive probing; three iterations; nine-answer budget (4 + 1 + 4).
Only local product edits, tests and one commit per correction were authorized.
No service restart, staging or production deployment was authorized.

The coder received only the inline dev fix brief, authorized repo and action
map. It did not receive question-set, anchor, raw, grade or history paths.
Fresh judges received the question/answer pack, frozen anchors and rubric.

Attempt 1: baseline 95% overall (dev 90%, holdout 100%) correctly generated a
brief for the noncritical 4/5 tool-rendered omission. The coder made a two-line
product fix and added a regression test. Focused evaluation reached 100%, but
the full exam exposed an overbroad date condition: overall 85%, dev 90%, holdout
80%. The engine stopped at `MAX_ITERATIONS: 3 reached`, without certification.
It did not treat the focused pass as success or continue beyond the budget.

Attempt 2 starts from an independent clean copy of the same original service,
with byte-identical questions and unchanged goals/budget. The coder instruction
explicitly calls for checking positive and negative requested-field behavior
against the product README. This is a revised test procedure, not a claim that
an arbitrary first coding attempt always succeeds. Its trajectory was 95%
overall baseline, 100% focused, then 100% full (dev 100%, holdout 100%). The
engine returned `PASSED: hard gate and goals met` on iteration 3. Each attempt
used exactly 9 answers, 18 total, with no holdout in the focused pass and no
empty coder handoff after that pass.

An offline parameter check on the saved baseline passed when only directness
was relaxed from 100% to 75%, and failed at the frozen 100% target. This verifies
that the chosen parameter affects acceptance; neither actual run's config was
changed. Both product commits, original source, packs, judgments, selections,
dev-only briefs, history and stop reasons are retained in the
[autopilot evidence archive](2026-09-16-autopilot-results.json).

Final independent review approved the corrected validator/selection path and
reproduced all 108 candidate per-answer comparisons. The first failed coder
attempt is not erased by the successful second attempt.

## Evidence and limits

Inputs: [control](judge-authorship-calibration.json),
[independent reserve](judge-authorship-holdout.json).
All first outputs, manifests and comparisons:
[calibration archive](2026-09-16-authorship-results.json).
The older calibration archives remain unchanged; this run does not replace
their historical failures or claim all older cases had full dimension labels.

The calibration runner records input hashes, model and reasoning settings, but
not an effective-command hash or structured token usage. Its manifests retain
the earlier authorization text; this report records the current user's explicit
request to finish Service Judge, continuing the already authorized Codex judging.
CLI command shown: `codex …`; no direct model API was called. Synthetic service
answers perform zero model generations and incur no live service answer cost;
judges and coders consume harness subscription usage, not zero computation.

These are bounded software/skill acceptance results, not universal LLM accuracy,
production certification, or proof that Orito passes. Autonomous repair still
requires authorization, an appropriate trace/anchor, a coding-agent orchestrator,
and the configured stop conditions. `loop.py` alone does not launch a coder.
