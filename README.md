# service-judge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-blueviolet)](https://agentskills.io)

**Grade your AI service like a rigorous QA engineer would.**

`service-judge` is an [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) that runs an LLM-as-judge evaluation of your LLM-powered service end-to-end:

1. **Discovers** your repo, database, and observability (read-only, always).
2. **Sizes** the eval with you — 30/50/100 questions, each with its statistical confidence.
3. **Generates & probes**: a cheap model writes realistic questions (including trap cases) and fires them at your live service, while ground-truth anchors are extracted from your DB through paths that never touch your LLM.
4. **Judges** every answer with the active Claude Code or Codex harness: rubric scoring plus cross-answer hunting for contradictions, broken tools, and hallucinated narratives.
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
**service-judge-loop** (autonomous improvement loop around it).

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
    codex plugin install service-judge

Codex has no subagents — the skill detects this and judges in-session with your strongest model.

### claude.ai (web)

1. Download `service-judge.skill` from the [latest release](https://github.com/auricIecu/service-judge/releases/latest) (or the repo as ZIP via the green **Code** button).
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

Judging uses only the active Claude Code or Codex subscription/session limits.
The plugin never reads an LLM API key or calls a model API directly.

## Safety

Read-only by design: the skill only ever `SELECT`s, never prints credentials, never edits your repo, tags every probe with an `eval-` session ID, and asks which environment (staging/production) before probing.

## License

MIT © Likeik CX
