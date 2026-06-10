# Phase 1 — Discovery

Goal: build the Context Brief. Three scans (repo, database, observability),
then confirm with the user.

## 1. Repo scan

Identify, in this order:
- **Stack & entry points:** language, framework, where the API routes live.
- **The LLM surface:** system prompts, tool/function definitions, model names,
  provider adapters, fallback logic. This tells you the service's intended
  modes/capabilities — the coverage matrix in Phase 3 is built from this.
- **Service modes:** distinct user-facing capabilities (e.g. "sales analyst",
  "competitor radar", "financial Q&A"). List them explicitly.
- **Endpoints to probe:** the chat/completion endpoint(s) and any REST
  endpoints that return the same underlying data WITHOUT passing through the
  LLM — those are anchor sources.

Where: read configs first (`.env*`, `config.*`, `settings.*`), then the API
layer, then prompts/tools. In web chat without filesystem: use the GitHub
connector to read these same files; if no connector, ask the user to paste
the system prompt + tool list + endpoint list.

## 2. Database detection cascade

Try in order; stop at the first success:
1. **Repo config:** look for `DATABASE_URL`, `SUPABASE_URL`/`SUPABASE_KEY`,
   `POSTGRES_*`, ORM configs. If found, confirm with the user before using.
2. **Session tools:** is there a database MCP (Supabase, Postgres, MySQL)
   already connected? Prefer it — it works in claude.ai too.
3. **Ask the user:** request a READ-ONLY connection string or read-only
   credentials. Suggest they create a read-only role if they only have
   admin credentials.
4. **Nothing available:** record "no ground truth from DB" and move on —
   do NOT block. Propose (in the report) setting up a read-only connector.

Whatever you obtain: test it with a trivial `SELECT 1`, then enumerate tables
relevant to the service's modes. NEVER run anything but SELECT.

## 3. Observability detection

Look for Langfuse / LangSmith / Braintrust / OpenTelemetry / structured
request logs (repo config keys like `LANGFUSE_*`, `LANGCHAIN_*`). If present
and reachable, traces become a second anchor source (real production
questions, latencies, tool-call frequencies). If not: note it; suggest in
the report.

## 4. The Context Brief (output of this phase)

Present to the user, in their language:

> **Service:** <name> — <one-line description>
> **Modes detected:** <list>
> **Probe endpoint:** <URL> (<staging|production|unknown — ASK>)
> **Database:** <reachable via X | not available — behavior-only eval>
> **Observability:** <found X | none found>
> **Confidence impact:** <"full anchors available" | "no ground truth: the
> judge can only score behavior/plausibility, accuracy will not be graded">

Then ask: "Is this right — the environment I should probe, and the DB access mode?" Do not proceed
until confirmed.
