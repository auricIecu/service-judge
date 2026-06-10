# service-judge

**Grade your AI service like a rigorous QA engineer would.**

`service-judge` is an [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) that runs an LLM-as-judge evaluation of your LLM-powered service end-to-end:

1. **Discovers** your repo, database, and observability (read-only, always).
2. **Sizes** the eval with you — 30/50/100 questions, each with its statistical confidence.
3. **Generates & probes**: a cheap model writes realistic questions (including trap cases) and fires them at your live service, while ground-truth anchors are extracted from your DB through paths that never touch your LLM.
4. **Judges** every answer with the strongest Claude available (Fable 5 by default): rubric scoring plus cross-answer hunting for contradictions, broken tools, and hallucinated narratives.
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

### Claude Code

    npx skills add ericlb12/service-judge

Then ask: *"evaluate my service with service-judge"* (or `/service-judge`).

### claude.ai (web)

1. Download this repo as a ZIP (green **Code** button → Download ZIP).
2. claude.ai → **Settings → Capabilities → Skills → Upload skill**.
3. In a chat (ideally with the GitHub connector pointed at your service's repo), ask Claude to evaluate your service.

> On the web, Claude can read your repo via the GitHub connector and your DB via an MCP connector. If your service's API isn't publicly reachable, the skill switches to "bring your outputs" mode — you paste or upload your service's answers and it judges them the same way.

## What you need

- The repo of the service (local, or connected via GitHub).
- Ideally: **read-only** DB access (connection string or an MCP connector like Supabase's). Without it the eval still runs, but only grades behavior, not accuracy — and tells you so.
- The strongest Claude model you have access to, for judging.

## Safety

Read-only by design: the skill only ever `SELECT`s, never prints credentials, never edits your repo, tags every probe with an `eval-` session ID, and asks which environment (staging/production) before probing.

## For big or repeated runs

`scripts/batch_eval.py` (experimental) runs the per-answer scoring of the judging phase via the Anthropic Batches API (−50% cost, prompt caching; the cross-answer pass stays with Claude). Needs a terminal and `ANTHROPIC_API_KEY`. See its docstring.

## License

MIT © Likeik CX
