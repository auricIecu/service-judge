# Autopilot dogfood — 1.7.0, 2026-09-02

First execution of the autopilot cycle end to end, following
`service-judge-loop/SKILL.md` literally, against a real service in its own git
repo. `setup.py` materializes it: a toy analytics assistant over a SQLite shop
database, 8 questions (5 dev / 3 holdout), 5 of 8 anchored by direct SQL, with
six bugs planted across the four judged dimensions.

Judging was done in-session by Claude. The fixer ran as a Claude Code subagent
receiving only the inline brief, as item 10 requires.

## Trajectory

| | total | dev | holdout | gap | hard gate |
|---|---|---|---|---|---|
| iter-01 | 46 | 30 | 80 | -50 | false |
| iter-02 | 85 | 100 | 80 | 20 | false |
| iter-03 | 85 | 100 | 80 | 20 | false |

Stopped on `MAX_ITERATIONS: 3 reached`. One commit on `service-judge/run-01`,
product code only.

## What worked

**Redaction holds under real data.** Neither brief contains `Q08`, its
`improvement_comment`, or either mixed dev+holdout cross-analysis group. Only
`{percent, gap_pp}` and the gate results crossed over from holdout.

**The preflight and the branch discipline work.** Clean tree on iteration 1;
from iteration 2 on, the loop's own uncommitted artifacts under
`.service-judge/` correctly stopped counting as a dirty product tree. Nothing
under `.service-judge/` was ever staged.

**The declared limitation showed up concretely, which is the point.** The fixer
repaired a holdout-only branch (`cancelled`) that the brief never mentioned —
it found it by *reading the product code*, which is authorized. Its own
self-check passed on a phrasing it invented, while the real holdout question
("How many orders were cancelled?") still routed to the total-orders branch and
still failed. This is exactly why `gap_pp` is marked indicative in autopilot,
and now there is a reproducible instance of it rather than an argument.

## Findings

### 1. `anchors.snapshot.json` has no documented home in a loop run — HIGH

`questions.md` says to store it at `raw/anchors.snapshot.json`. In a loop run
the only `raw/` directories are per-iteration (`iter-NN/raw/`), created by
`loop.py`; anchors are run-scoped. The loop SKILL's step 4 only says to point
`anchors` at "the machine-readable ground-truth snapshot" — it never says where
it lives. `.gitignore` protects `.service-judge/**/raw/` and nothing else.

A user who does the obvious thing and writes
`.service-judge/run-<id>/anchors.snapshot.json` commits the ground truth — real
customer values extracted by direct SQL — to the service repo. Same class as
the `config.json`/`probe_cmd` leak the grill caught in Tramo 1, and found the
same way: by actually running the documented flow.

Fix: create a run-level `raw/` in `loop.py` (or document that path explicitly)
and say so in step 1. **Closed in 1.7.1.**

### 2. The authorized action flags never reach the actor — HIGH

`loop.py` validates `autonomy` against `authorization.json` and refuses to
start without it. Then the fixer — the only participant that actually touches
the machine — is handed "the brief and nothing else", and the brief carries no
autonomy block. Nothing tells it what it may do.

Observed: this run authorized `run_tests: false`. The fixer wrote a
`--selfcheck` and ran it. Harmless on a toy service; the identical mechanism is
how an unauthorized `restart_local` or `deploy_staging` happens. The
authorization gates *startup* and never reaches *conduct*.

Fix: the allowed-actions map belongs in the fixer's prompt, next to the brief.
It is the one piece of non-brief context the contract should mandate.
**Closed in 1.7.1**, inside the brief itself along with `repo` (finding 3).

### 3. The brief has no pointer to the product code — MEDIUM

"The fixer consumes that JSON and nothing else" leaves it with question ids,
scores and prose, and no repo, file or entrypoint. The repo path had to be
passed out of band, from `authorization["repo"]`. The prose never says to do
that, so the documented flow is not executable as written. **Closed in 1.7.1**
by the same brief field as finding 2.

### 4. `needs_fix` with an empty brief is a reachable dead end — MEDIUM-HIGH

At iteration 2 every dev question passed. What remained was a holdout-side
failure and a mixed dev+holdout cross-analysis finding — both correctly
withheld. The brief was `{"dev": [], "regressed_ids": [], "cross_analysis":
[], ...}` while the status was `needs_fix` and the gates were false.

The autopilot is told to fix and given nothing to fix. It burned iteration 3
producing an identical grade and stopped on the iteration limit. Item 13's stop
conditions (regression, stagnation, limit, goals met) have no entry for
"nothing actionable remains". This is the visible face of the plan's own
"holdout detail never reaches the fixer" decision, and the cycle needs an
explicit exit for it rather than a wasted iteration.

**Closed in 1.7.2.**

### 5. The preflight succeeds silently — LOW

`--plan` prints the same payload whether the preflight ran and passed or
`autonomy.mode` was never set to autopilot. A gate whose entire purpose is to
refuse to start should say when it decided to allow it.

**Closed in 1.7.2.**

### 6. `"reason": ""` on a plain `needs_fix` — COSMETIC

Pre-existing. The payload carries `dev_questions_below_4` and the brief path,
so nothing is lost, but an empty string reads like the 1.5.0 bug.

**Closed in 1.7.2.**

## Reproduce

```
python3 docs/dogfood/autopilot-service.py /tmp/dogfood-service
```

Then follow `service-judge-loop/SKILL.md` from step 1. Ground truth:
customers=347, orders=1204, products=62, avg_order_value=64.43, cancelled=109.
