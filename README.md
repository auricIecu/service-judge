# service-judge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-blueviolet)](https://agentskills.io)

**Grade your AI service like a rigorous QA engineer would.**

`service-judge` is an [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) that runs an LLM-as-judge evaluation of your LLM-powered service end-to-end:

1. **Discovers** your repo, database, and observability (read-only, always).
2. **Sizes** the eval with you — canary (10–12) / diagnostic (30) / release (50–100), each with its honest statistical confidence.
3. **Generates & probes**: a cheap model writes realistic questions (including trap cases) and fires them at your live service, while ground-truth anchors are extracted from your DB through paths that never touch your LLM. The canary is probed and judged first, and the run aborts there on a leak, a bypassed guardrail, or a severely wrong figure — a decisive finding for the price of 12 answers.
4. **Judges** every answer with the active harness by default, or an optional external Codex, Claude Code, or DeepSeek harness: rubric scoring plus causal attribution across model, tool, anchor, or missing evidence, with unsafe side effects as a hard gate.
5. **Reports**: per-question scorecard, global grade, and improvement proposals ordered by ROI. Then it gets out of your way.

Born from a real eval that caught broken tools, placeholder data narrated as a business collapse, and guardrails firing on legitimate questions — none of which assert-based tests had seen.

## What the report looks like

| Q# | Mode | Question | Verdict | Improvement comment |
|---|---|---|---|---|
| Q03 | sales | How many active customers do we have? | ✅ 5/5 | — |
| Q07 | finance | What was the P&L for May? | ❌ 1/5 | `get_pnl_detail` errors while `/api/pnl/dashboard` serves this data — wire the tool to the same source |
| Q11 | finance | How did revenue evolve this year? | ⚠️ 3/5 | Caps missing: empty future months narrated as a sales collapse — cap at last real month |

> **Global grade: 107/150 (71%)** · Top proposal: fix the broken P&L tools (converts 4 ❌ → ✅, low effort)

## Install

Two skills ship in this repo: **service-judge** (one-off evaluation) and
**service-judge-loop** (autonomous improvement loop around it). Both install
together from any of the channels below.

    service-judge/
    ├── skills/
    │   ├── service-judge/        SKILL.md + references/ + assets/ + scripts/
    │   └── service-judge-loop/   SKILL.md
    ├── .claude-plugin/           plugin.json + marketplace.json  (Claude Code)
    ├── .codex-plugin/            plugin.json                     (Codex CLI)
    └── .agents/plugins/          marketplace.json                (Codex CLI)

> Since 1.3 the skills live under `skills/`, not at the repo root. Cloning the
> repo straight into a skills directory no longer works — use one of the
> channels below.

### Claude Code — as a plugin (recommended: versioned updates)

    /plugin marketplace add auricIecu/service-judge
    /plugin install service-judge@service-judge

Then ask: *"evaluate my service with service-judge"* (or `/service-judge`).

### Claude Code / Codex / Cursor / Windsurf — as a static skill copy

    npx skills add auricIecu/service-judge

Installs to the right place for whichever agent you use, but does NOT
auto-update — re-run the command to pick up new versions.

### Codex CLI — as a plugin

    codex plugin marketplace add https://github.com/auricIecu/service-judge
    codex plugin add service-judge@service-judge

The skill uses available subagents and otherwise judges in-session.

### claude.ai (web)

1. Download `service-judge.skill` from the [latest release](https://github.com/auricIecu/service-judge/releases/latest) — check the tag matches the version you expect; the plugin channels above always track `main`, releases are cut per version.
2. claude.ai → **Settings → Capabilities → Skills → Upload skill**.
3. In a chat (ideally with the GitHub connector pointed at your service's repo), ask Claude to evaluate your service.

> On the web, Claude can read your repo via the GitHub connector and your DB via an MCP connector. If your service's API isn't publicly reachable, the skill switches to "bring your outputs" mode — you paste or upload your service's answers and it judges them the same way.

## What you give vs. what you get

The eval never fails hard — it adapts to whatever access you provide and
records every limitation in the report's confidence notes:

| You provide | It runs? | What the grade covers |
|---|---|---|
| Just the service URL | ✅ | Behavior only: errors, contradictions, deflections. No accuracy — there's no ground truth to check against (flagged in the report). |
| URL + repo (local or GitHub connector) | ✅✅ | Discovery finds your modes, tools, and prompts → targeted questions and smart trap cases. |
| URL + repo + **read-only** DB (connection string or MCP connector) | ✅✅✅ | The full experience: answers verified against ground truth extracted through paths that never touch your LLM. |
| No reachable URL at all | ✅ | "Bring your outputs" mode: paste or upload your service's answers and they're judged the same way. |

## The improvement loop

The second skill, `service-judge-loop`, turns a one-off grade into a measured
iteration. Ask for it in words — *"keep evaluating my bot until it passes"* —
and it freezes a golden question set, then runs:

    probe + judge → you fix your service → the SAME exam again → compare

It stops on its own for four reasons: the gates passed, a fix caused a
regression (it tells you — it never reverts), the score stagnated (<2pp twice
in a row), or the iteration limit was reached. It measures; it never edits
your service.

The golden set carries a dev/holdout split, and between iterations you only
ever see the holdout **aggregate**, never the individual questions — so your
fixes can't quietly overfit to the exam.

By default, the loop deliberately re-probes the whole set every iteration;
that is what makes iteration N comparable to N−1, so the canary shortcut
doesn't apply. The entire bill is `questions × max_iterations`, and it's
decided before the first run.

For cheaper development passes, set `"probe_strategy": "adaptive"`. Adaptive
still starts with a full baseline and only a full run can satisfy the quality
gates, but intermediate iterations probe a focused dev-only subset: failures,
related questions, all-dev contradiction groups, and a small deterministic
regression sample. `answer_budget` reserves `len(golden_set)` answers for the
certifying full run before any focused probe is allowed. On a 30-question set,
a typical adaptive run costs `30 + 10 + 10 + 30 = 80` answers instead of
`30 × 4 = 120`.

## What it costs

Judging is free by default on the active Claude Code or Codex subscription.
An optional external judge consumes that other harness's subscription. The
plugin never reads an LLM API key or calls a model API directly.

What you do pay for is **your own service answering the questions** — and one
question can trigger several internal generations with long context. So the
skill optimises for asking as few questions as possible:

- The canary (10–12) runs first and aborts the run on a critical finding.
- The size menu recommends the smallest tier that answers your actual question;
  100 × 3 is reserved for formal baselines, never for exploration.
- Stored answer packs are re-judged instead of re-probed when your service
  hasn't changed — changing the rubric or judge costs no new service answers.
- Every report ends with what the run consumed: questions, generations,
  tokens, latency, and cost per correct answer.

## Safety

Read-only by design: the skill only ever `SELECT`s, never prints credentials, never touches your product code (eval artifacts only, in a location you approve), tags every probe with an `eval-` session ID, and asks which environment (staging/production) before probing.

## License

MIT © Likeik CX — built and maintained by [auricIecu](https://github.com/auricIecu).
