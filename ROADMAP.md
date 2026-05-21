# Roadmap — mneme

Five phases, each ~1 week of solo-dev work. Don't skip ahead — each phase locks in interfaces the next one depends on.

## Phase 1 — Observe (week 1)

Goal: every tool call from Claude Code lands a row in `query_episode`. Nothing else.

- Project scaffold: `pyproject.toml`, `agent_service/` package, `tests/`, `Makefile`, `ruff` + `mypy --strict` + `pre-commit`
- `FastMCP.as_proxy()` mounting the upstream DB MCP at `$UPSTREAM_DB_MCP_URL`
- `AsyncPostgresStore` + `AsyncPostgresSaver` against `$DATABASE_URL`; run `migrations/0001_init.sql` on boot
- `memory.py` with typed `write_episode()` / `search_episodes()` / `write_expertise_note()`
- `AuditMiddleware` on `on_call_tool` — writes one `query_episode` row per call
- `routing.py` with rule-based `route_to_namespace`
- `/healthz` FastAPI route; structured JSON logs to stdout
- Deployed on Replit Reserved VM, reachable from Claude Code as one MCP server

**Exit criterion:** Claude Code makes 10 tool calls against mneme; `select count(*) from query_episode` returns 10.

## Phase 2 — Advise (week 2)

Goal: each tool response carries useful advisories.

- LangGraph agent loop in-process, invoked only when `get_advisories` is called or `AdvisoryMiddleware` decides to spend cycles
- Three advisors: `CacheStaleAdvisor`, `ConflictAdvisor`, `SchemaDriftAdvisor`
- `get_advisories(query: str, db: str | None) -> list[Advisory]` agent-owned MCP tool
- `get_schema_summary(db: str) -> SchemaSummary`
- `get_query_history(filter: HistoryFilter) -> list[Episode]`
- `refresh_schema(db: str) -> SchemaSummary` — recomputes `schema_hash`, writes new `db_schema_snapshot`
- `<<<UNTRUSTED_DATA>>>` wrapping on every memory injection
- Tool description pinning + drift detection

**Exit criterion:** A query against a stale cached schema produces a `cache_stale` advisory in the tool response.

## Phase 2.5 — Per-DB experts (week 3)

Goal: same behaviour, multiple specialised agents.

- Refactor the single `ExpertAgent` into `PostgresExpert`, `PineconeExpert` behind the locked `ExpertAgent` Protocol
- LangGraph supervisor node that routes to the right expert based on `db_namespace`
- Each expert has its own system prompt and memory namespace (`(db_namespace, category)`)
- No new tables; same memory schema, just partitioned by namespace

**Exit criterion:** Adding a third DB requires only a new `ExpertAgent` subclass and a routing rule, no changes to middleware or memory code.

## Phase 3 — Surface (week 4)

Goal: humans can see what the agent knows.

- FastAPI dashboard at `/dashboard`:
  - Recent `query_episode` rows with retrieved-memory traces
  - `expertise_note` browser with confirm/reject buttons
  - `cache_event` timeline per DB
  - Advisory hit-rate by kind
- LangSmith integration verified end-to-end
- Weekly LLM-driven consolidation of duplicate `expertise_note` rows, run as a Replit Scheduled Deployment

**Exit criterion:** You can answer "what does mneme think about the `users` table?" by clicking, not by writing SQL.

## Phase 4 — Approve and act (deferred)

Goal: agent can request to take actions, with human approval.

- `Advisory.requires_approval = True` flows that propose specific actions (drop a stale cache key, run a `REINDEX`, deprecate a query)
- Human-in-loop approval UI in the dashboard
- Write-back tools gated behind explicit approval

**Not yet.** Don't even sketch the interfaces until Phase 3 has been running for two weeks without surprises.

## What we are deliberately not building

- A custom vector DB. pgvector is enough until episode count exceeds ~50k/month.
- A knowledge graph backend. Skip Graphiti/Neo4j until temporal queries become >10% of traffic.
- MCP sampling. Known prompt-injection vector; defer until Phase 4 has an approval UI.
- A separate proxy process or message bus. FastMCP middleware + in-process asyncio is sufficient.
- Multi-tenant auth. Single-user for now; OAuth 2.1 + RFC 8707 is a Phase 5 conversation when we move off Replit.
