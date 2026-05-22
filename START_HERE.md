# Claude Code Web — Kickoff Prompt for `mneme`

Paste the block between the `---` lines into Claude Code Web's first message.
Make sure `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/DATABASE.md`, and `migrations/0001_init.sql` are committed to the repo before you start the session so Claude Code can read them.

---

You're bootstrapping a new Python project called **mneme**: a memory-and-advisory MCP middleware that sits between Claude Code and a fleet of database MCP servers (Postgres/pgvector, Pinecone, object stores).

Before writing any code, read these files in order and treat them as the source of truth for every decision:

1. `CLAUDE.md` — project conventions, stack constraints, and security defaults
2. `docs/ARCHITECTURE.md` — the reference architecture, middleware pattern, and forward-compat interfaces
3. `docs/ROADMAP.md` — the phased plan; you are executing **Phase 1 (Week 1)** only
4. `docs/DATABASE.md` — how mneme's memory schema coexists with the saaz demo dataset in the same Replit Helium Postgres
5. `migrations/0001_init.sql` — the locked-in memory schema; do not modify

After reading those, do this **in order** and stop after each step for me to review:

**Step 0 — Verify the environment.** Confirm these are set in Replit Secrets and reachable from the Repl:

- `DATABASE_URL` — should be `postgresql://postgres:<password>@helium/heliumdb?sslmode=disable`. The hostname `helium` only resolves from inside Replit; do NOT try to connect from your laptop.
- `UPSTREAM_DB_MCP_URL` — the existing FastMCP server with the SQL guardrails (built in the earlier project). If this isn't deployed yet, stop and tell me.
- `ANTHROPIC_API_KEY` — needed for the Phase 2 agent loop. Not strictly needed for Phase 1, but check now so we catch it early.

Run `psql "$DATABASE_URL" -c "SELECT version(), current_database();"` to confirm the Postgres is reachable. If it isn't, stop. If it is, run:

```sql
\dt                              -- list tables
SELECT extname FROM pg_extension; -- confirm pgvector is installed
```

Tell me which tables already exist. If the `artist`, `artist_link`, etc. tables from the `saaz` project are present, that's expected — mneme and saaz share this Postgres. If pgvector is missing, install it before continuing: `CREATE EXTENSION IF NOT EXISTS vector;`.

**Step 1 — Plan.** Produce a written build plan for Phase 1 only. List the files you intend to create, the public Python interfaces (signatures, not bodies), the Replit deployment shape, and any open questions you have for me. Do not write code yet.

**Step 2 — Scaffold.** Once I approve the plan, scaffold the project:
- `pyproject.toml` with the pinned versions from `CLAUDE.md`
- `agent_service/` package with empty modules matching your plan
- `tests/` with `pytest` + `pytest-asyncio` skeleton
- `Makefile` with `dev`, `test`, `lint`, `migrate`, `run` targets
- Wire up `ruff` + `mypy --strict` + `pre-commit`
- Verify `make test` runs (even with zero tests) and `make run` boots a FastMCP server on `$MCP_SERVER_PORT` answering an empty tool list

**Step 3 — Migrate.** Run `make migrate` against `$DATABASE_URL`. This creates the mneme memory tables (`db_schema_snapshot`, `column_doc`, `query_episode`, `expertise_note`, `cache_event`) plus the LangGraph `store` and checkpoint tables. After it runs, confirm with `\dt` that the new mneme tables appear alongside the existing saaz tables. They should coexist cleanly — no overlapping names.

**Step 4 — Phase 1 deliverables.** Implement, in this order, with a passing test before moving to the next:

a. **Proxy passthrough.** Mount the upstream DB MCP server (URL in `$UPSTREAM_DB_MCP_URL`) via `FastMCP.as_proxy()` so every upstream tool is exposed unchanged through `mneme`. Test: a fake upstream FastMCP server returns a tool; `mneme` re-exposes it; a client gets the same result.

b. **Postgres memory store.** Connect to `$DATABASE_URL` via a single `psycopg_pool.AsyncConnectionPool`, instantiate LangGraph's `AsyncPostgresStore` and `AsyncPostgresSaver` against it, and expose a `memory.py` module with typed `write_episode()` / `search_episodes()` / `write_expertise_note()` functions backed by Pydantic models. Test: round-trip an episode through pgvector search.

c. **Audit middleware.** A `FastMCP` middleware class that fires on every `on_call_tool`, writes the call (params, namespace, duration, error, result digest) to `query_episode`, and adds a structured `meta.audit_id` field to every response. No advisories yet. Test: call a proxied tool, assert one row lands in `query_episode`.

d. **Namespace router.** A `routing.py` module with `route_to_namespace(tool_name, params) -> str` returning one of the configured `db_namespace` values from `MNEME_NAMESPACES`. For Phase 1, regex/keyword routing is fine — keep the function signature stable. Test: parameterised tests covering tool name patterns → `pg_main` / `pinecone_main` / `saaz_demo` / `default`.

e. **Health/observability.** Expose `/healthz` (FastAPI sidecar mounted on the same process) and ship every middleware event to stdout in JSON via `structlog`. If `$LANGSMITH_API_KEY` is set, also stream traces to LangSmith.

**Step 5 — Smoke test against saaz.** This is the key Phase 1 acceptance gate. Use the upstream DB MCP through `mneme` to run this query end-to-end (Claude Code → mneme → upstream DB MCP → Helium Postgres):

```sql
SELECT genre, count(*) AS n FROM artist GROUP BY genre ORDER BY n DESC;
```

Confirm the result comes back AND that `select count(*) from query_episode where tool_name like '%sql%' or tool_name like '%query%'` shows at least one row. Tell me what `db_namespace` the audit row landed in — it should be `saaz_demo` if the router is working.

**Hard rules for this phase:**
- No LangGraph agent loop yet. No advisories. No conflict detection. No cache logic. Phase 1 is *observe and remember*, not advise.
- No new tables beyond what's in `migrations/0001_init.sql`. The saaz tables are read-only to mneme.
- Do not add MCP sampling, OAuth, or write-back tools. Read-only everywhere.
- Every DB call uses the shared `psycopg_pool.AsyncConnectionPool`. One pool per process.
- All new code: Python 3.12, async-first, full type hints, Pydantic v2 models for any cross-module data.

When all five Phase-1 deliverables pass tests on Replit, stop and paste the smoke-test output (the genre breakdown + the audit row). We'll review before unlocking Phase 2.

If anything in `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, or `migrations/0001_init.sql` contradicts what I just said, surface the contradiction before proceeding — do not silently pick a side.

---

## Optional follow-up prompts you can use later

**After Phase 1 review, to start Phase 2:**
> Phase 1 looks good. Read `docs/ROADMAP.md` Phase 2 and produce a plan for the LangGraph agent loop, the `get_advisories` tool, and the three advisory signals (cache-staleness, conflict, schema-drift). Same plan-then-build cadence as Phase 1.

**To check on saaz from inside the mneme repo (sanity check that the shared DB looks right):**
> Connect to `$DATABASE_URL` and run the saaz health-check queries from `docs/DATABASE.md` § "Sanity-checking saaz". Don't modify anything; just print the row counts and tell me whether saaz looks ready for mneme to observe queries against it.

**If Claude Code drifts from the architecture:**
> Stop. Re-read `docs/ARCHITECTURE.md` section "Forward-compatibility interfaces" and tell me which interface you just violated and how you'll fix it without rewriting Phase 1.

**To force a checkpoint:**
> Commit current state. Write a one-paragraph status in `docs/STATUS.md` summarising what works, what's stubbed, and what's broken. Then wait.
