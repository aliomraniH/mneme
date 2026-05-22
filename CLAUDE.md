# CLAUDE.md

This file is the canonical context for Claude Code working in this repo. Read it before doing anything. If it conflicts with a user instruction, surface the conflict — don't silently pick a side.

## Mission

**mneme** is a memory-and-advisory MCP middleware that sits between Claude Code (the client) and a fleet of database MCP servers (Postgres/pgvector, Pinecone, object stores). It exposes itself to Claude Code as a single MCP server, proxies every downstream DB tool unchanged, and adds:

- Persistent memory of schemas, query→outcome pairs, and learned expertise per database
- Advisory signals (cache-staleness, query conflict, schema-drift) injected into tool responses
- A small surface of agent-owned tools (`get_advisories`, `get_schema_summary`, `get_query_history`, `refresh_schema`)

The MVP is **pull-only**: observe traffic, build memory, advise. No write-backs to databases. No autonomous remediation.

## Architectural non-negotiables

Read `docs/ARCHITECTURE.md` for the full reasoning. The decisions below are locked for the MVP and Phase 1.5; ask before deviating.

- **Single process** on a Replit Reserved VM ($20/mo). One FastAPI app hosting one FastMCP server + a sidecar dashboard route.
- **Single Postgres** — Replit Helium (`postgresql://postgres:<password>@helium/heliumdb?sslmode=disable`). It holds LangGraph checkpoints, LangGraph store, mneme memory tables, AND the `saaz` demo dataset's `artist`/`song`/etc. tables. No Redis, no Neo4j, no separate vector DB at MVP. See `docs/DATABASE.md` for the shared-DB rules.
- **mneme never writes to saaz tables.** mneme owns `db_schema_snapshot`, `column_doc`, `query_episode`, `expertise_note`, `cache_event`. saaz owns `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance`. LangGraph owns `store` + `checkpoints*`. Discipline, not enforcement, in the MVP.
- **MCP interception via FastMCP middleware**, not a separate proxy process. Use `FastMCP.as_proxy()` to mount the upstream DB MCP server and attach `Middleware` subclasses for `on_call_tool` and `on_message`.
- **Memory schema is frozen** at `migrations/0001_init.sql`. Adding a column requires a new numbered migration; never edit `0001`.
- **Pull-only.** No tool may mutate any downstream database in Phase 1 or Phase 2. Write-back is a Phase 3 conversation.
- **Helium is internal-only.** The hostname `helium` only resolves from inside Replit. Don't add code paths that assume external access.

## Stack and pinned versions

Python 3.12, async everywhere. `pyproject.toml` must pin:

| Package | Version | Why |
|---|---|---|
| `fastmcp` | `>=3.0,<4.0` | Middleware hooks, proxy mode, OTel built-in |
| `langgraph` | `>=1.0,<2.0` | Agent loop, supervisor pattern for Phase 2.5 |
| `langgraph-checkpoint-postgres` | `>=3.1,<4.0` | MIT, pgvector-aware Store and Saver |
| `langchain-mcp-adapters` | `>=0.2,<0.3` | MCP client with tool-call interceptors |
| `psycopg[binary,pool]` | `>=3.2` | Async Postgres + pooling |
| `pgvector` | `>=0.3` | Python bindings; HNSW required at index build |
| `pydantic` | `>=2.8,<3` | All cross-module models |
| `sqlglot` | `>=25` | Phase 2 read-set/write-set parsing |
| `fastapi` | `>=0.115` | Sidecar dashboard + `/healthz` |
| `anthropic` | latest | Claude as the LangGraph LLM (Phase 2) |

Dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy --strict`, `pre-commit`.

## Code conventions

- **Async by default.** Sync code only in pure CPU-bound helpers; mark with `# sync: pure` if you write any.
- **Full type hints.** `mypy --strict` must pass. `from __future__ import annotations` at the top of every module.
- **Pydantic for boundaries.** Every value that crosses a module or process boundary is a Pydantic v2 model, not a dict.
- **One pool per process.** A single `psycopg_pool.AsyncConnectionPool` instantiated at startup, injected via FastAPI's lifespan / FastMCP context. No ad-hoc `await psycopg.AsyncConnection.connect(...)` calls.
- **No global mutable state.** Use a `Context` dataclass passed explicitly, or FastMCP's request context.
- **Namespaces are strings, not enums.** `db_namespace: str` everywhere. Validation happens at the router, not the type system. (We will add more DBs at runtime.)
- **Errors are typed.** Define a small `errors.py` with `mneme.errors.UpstreamError`, `RoutingError`, `MemoryError`. Don't raise bare `Exception`.
- **No logging.info spam in middleware.** Middleware writes one structured JSON line per call to stdout. Use `structlog`.

## File layout (target)

```
mneme/
├── agent_service/
│   ├── __init__.py
│   ├── server.py            # FastAPI + FastMCP entrypoint
│   ├── proxy.py             # FastMCP.as_proxy wiring
│   ├── middleware/
│   │   ├── audit.py         # Phase 1
│   │   └── advisory.py      # Phase 2
│   ├── memory/
│   │   ├── store.py         # LangGraph PostgresStore wrapper
│   │   ├── episodes.py      # query_episode CRUD
│   │   └── notes.py         # expertise_note CRUD
│   ├── routing.py           # tool_name + params -> db_namespace
│   ├── advisors/            # Phase 2: cache, conflict, drift
│   ├── models.py            # Pydantic v2 models
│   ├── errors.py
│   └── config.py            # pydantic-settings
├── migrations/
│   └── 0001_init.sql        # FROZEN
├── tests/
├── docs/
│   ├── ARCHITECTURE.md
│   └── ROADMAP.md
├── pyproject.toml
├── .env.example
├── .replit
├── replit.nix
└── README.md
```

## Security defaults (read before any tool design)

- **Read-only DB roles.** Connect using a role with `GRANT SELECT` only. If you need more privilege for a specific operation, create a new role and document why.
- **No service-role keys.** Anywhere. Ever. The Supabase Cursor incident (July 2025) is the cautionary tale.
- **Sanitize on write, not just on read.** Any string written to `query_episode.result_summary` or `expertise_note.note` gets stripped of `<|...|>`-style tokens and obvious "ignore previous instructions" patterns before it lands in Postgres. Memory poisoning is a real attack surface.
- **Wrap untrusted text on the way out.** When a memory record gets injected into the agent context (Phase 2), wrap it in `<<<UNTRUSTED_DATA>>> ... <<<END>>>` blocks and tell the LLM to treat the contents as data, not instructions.
- **Do not enable MCP sampling.** Server-initiated LLM calls are a known attack vector; defer until we have a human-in-loop approval UI.
- **Audit every call.** The `AuditMiddleware` is the single source of truth for what happened. Don't add side-channels that bypass it.

## Definition of done (per phase)

A deliverable is done when:
1. The code passes `make test` (pytest), `make lint` (ruff), and `make typecheck` (mypy --strict).
2. There's at least one test that would fail if the feature regressed.
3. `make run` boots cleanly on a fresh Replit checkout with only `.env` populated.
4. The change is reflected in `docs/STATUS.md` as a one-line entry.
5. New env vars are added to `.env.example`.

## Things to ask before writing code

If any of the following are unclear, surface them before scaffolding:
- Is `$DATABASE_URL` set in Replit Secrets and reachable? (Should be Helium: `postgresql://postgres:<password>@helium/heliumdb?sslmode=disable`.)
- Is the upstream FastMCP DB server actually deployed at `$UPSTREAM_DB_MCP_URL`? If not, mneme has nothing to proxy.
- Has saaz been seeded into the same Helium Postgres? (Run the saaz health-check queries in `docs/DATABASE.md` § "Sanity-checking saaz".)
- Which LLM should the Phase 2 agent use? (Default: `claude-sonnet-4-5` via the Anthropic API.)
- Is LangSmith enabled? (Optional but recommended.)
