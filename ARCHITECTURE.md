# Architecture — mneme

This document distills the reference architecture for the Claude Code ↔ MCP database fleet middleware. It's the canonical technical spec for the MVP and Phase 1.5. Treat conflicts with this doc as bugs.

## System diagram

```
┌────────────────────────────────────────────────────┐
│                  Claude Code (web/CLI)             │
└────────────────────────────────────────────────────┘
                       │
                       │  MCP / Streamable HTTP
                       ▼
┌────────────────────────────────────────────────────┐
│   Replit Reserved VM — single Python process       │
│   ┌──────────────────────────────────────────┐     │
│   │  FastAPI                                 │     │
│   │  ├─ /mcp           (FastMCP server)      │     │
│   │  │   ├─ AuditMiddleware     (Phase 1)    │     │
│   │  │   ├─ AdvisoryMiddleware  (Phase 2)    │     │
│   │  │   └─ Tools:                           │     │
│   │  │       ├─ proxied DB tools (auto)      │     │
│   │  │       ├─ get_advisories               │     │
│   │  │       ├─ get_schema_summary           │     │
│   │  │       ├─ get_query_history            │     │
│   │  │       └─ refresh_schema               │     │
│   │  ├─ /healthz                             │     │
│   │  └─ /dashboard     (Phase 2.5)           │     │
│   │                                          │     │
│   │  LangGraph agent  (Phase 2, in-process)  │     │
│   │  ├─ MessagesState + AsyncPostgresSaver   │     │
│   │  └─ AsyncPostgresStore (pgvector)        │     │
│   │                                          │     │
│   │  MCP client (langchain-mcp-adapters)     │     │
│   └──────────────────────────────────────────┘     │
└────────────────────────────────────────────────────┘
        │  MCP                          │  SQL
        ▼                               ▼
┌──────────────────────┐    ┌─────────────────────────┐
│ Upstream FastMCP DB  │    │ Vercel Postgres         │
│ server (existing)    │    │  + pgvector             │
│  - postgres tool     │    │   - LangGraph store     │
│  - pinecone tool     │    │   - LangGraph saver     │
│  - sql guardrails    │    │   - mneme memory schema │
└──────────────────────┘    └─────────────────────────┘
```

The agent service is one process. It speaks MCP north (to Claude Code) and MCP south (to the upstream DB server). No queues, no Redis, no extra services at MVP.

## The interception model

mneme is a **FastMCP server with middleware that wraps a proxied upstream server**. This is the smallest interception surface that captures every tool call.

```python
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.client import Client

upstream = Client(transport="streamable-http", url=os.environ["UPSTREAM_DB_MCP_URL"])
proxy = FastMCP.as_proxy(upstream, name="mneme-proxy")

mneme = FastMCP("mneme", lifespan=lifespan)
mneme.mount(proxy)            # all upstream tools appear under mneme
mneme.add_middleware(AuditMiddleware(store=store))     # Phase 1
mneme.add_middleware(AdvisoryMiddleware(...))          # Phase 2
```

Every `on_call_tool` invocation:
1. Resolves a `db_namespace` via `routing.route_to_namespace(tool, params)`.
2. (Phase 2) Calls advisors; collects `Advisory` objects.
3. Forwards the call via `await call_next(ctx)`.
4. Writes a `query_episode` row with the result digest.
5. Augments the response with `meta.audit_id` and (Phase 2) `meta.advisories`.

Middleware never raises — failures inside middleware become an `Advisory(kind="middleware_error", ...)` and the call still passes through. The agent's job is to be a careful narrator, not a gate.

## Memory model

A tiered hybrid: relational tables for structured DB metadata, JSONB for flexible payloads, pgvector for semantic recall. All in one Postgres.

| Tier | What | Backed by |
|---|---|---|
| Working | Current conversation state | LangGraph `MessagesState` + `AsyncPostgresSaver` |
| Warm | Hot schema + recent episodes | In-process LRU (no Redis at MVP) |
| Cold (semantic) | Schema snapshots, column docs | `db_schema_snapshot`, `column_doc` + HNSW |
| Cold (episodic) | Query→outcome pairs | `query_episode` + HNSW |
| Cold (procedural) | Learned advice, user corrections | `expertise_note` + HNSW |

The schema is locked in `migrations/0001_init.sql`. CoALA mapping:
- **Working** → LangGraph state, ephemeral
- **Semantic** → `db_schema_snapshot`, `column_doc`
- **Episodic** → `query_episode`
- **Procedural** → `expertise_note` (Phase 2)

Recall on each tool call (Phase 2):
1. Vector top-k=8 from `query_episode` filtered by `db_namespace`
2. Vector top-k=5 from `expertise_note` filtered by `db_namespace`
3. Exact-match pull of relevant table rows from `db_schema_snapshot`
4. Cross-encoder rerank to ~3 episodes + ~5 column docs
5. Inject as a system preamble, wrapped in `<<<UNTRUSTED_DATA>>> ... <<<END>>>`, capped at ~1500 tokens

Write triggers:
- **Successful call** → `query_episode` (with `error=null`, hash-deduped against last 60s)
- **Failed call** → `query_episode` (with `error` set, `source='error'`, downweighted in retrieval)
- **User correction in chat** → `expertise_note` with `confirmed_by_user=true` (Phase 2.5)
- **Schema refresh** → new `db_schema_snapshot` row, `cache_event` written for affected `column_doc` rows

## Forward-compatibility interfaces

Lock these in during Phase 1. They cost nothing now and make Phase 2.5 (per-DB experts) a refactor instead of a rewrite.

```python
# agent_service/interfaces.py
from typing import Protocol

class ExpertAgent(Protocol):
    db_namespace: str
    async def advise(self, call: ToolCall, ctx: AgentContext) -> list[Advisory]: ...
    async def remember(self, episode: Episode) -> None: ...
    async def recall(self, query: str, k: int) -> list[Memory]: ...

class Router(Protocol):
    async def route(self, call: ToolCall) -> ExpertAgent: ...
```

At MVP there is **one** `ExpertAgent` implementation that handles all namespaces via internal dispatch. At Phase 2.5, the LangGraph supervisor instantiates `PostgresExpert`, `PineconeExpert`, etc., each with its own system prompt and namespace, behind the same `Router` interface.

**Message envelope** (use this dataclass from day one even if it just gets passed in-process):

```python
@dataclass(slots=True)
class Event:
    type: Literal["tool_call", "tool_result", "memory_write", "advisory"]
    db_namespace: str
    payload: dict
    ts: datetime
```

At Phase 3 this swaps to Redis Streams or NATS without changing producers.

## Routing (Phase 1)

`route_to_namespace(tool_name: str, params: dict) -> str`

Phase 1 implementation is rule-based:
- Tool names containing `postgres`, `sql`, `pg_` → `pg_main`
- Tool names containing `pinecone`, `vector`, `embedding` → `pinecone_main`
- Params containing `connection` or `db_name` → use that value
- Fallback → `default`

Keep the signature stable. Phase 2 swaps in a tiny LLM router for ambiguous cases without touching callers.

## Advisory signals (Phase 2 — design now, build later)

Three signals, each cheap, each backed by a heuristic that's good enough for MVP:

1. **Cache staleness.** Compare current `schema_hash` (computed from `pg_catalog` introspection or `describe_index_stats()` for Pinecone) against the hash on the `db_schema_snapshot` used to seed prior episodes. If different → `Advisory(kind="cache_stale", ...)`.

2. **Conflict detection.** Parse the incoming SQL with `sqlglot`. Compute read-set (tables in FROM/JOIN) and write-set (tables in INSERT/UPDATE/DELETE — Phase 3, not now). Compare to in-flight or recently-cached queries. Overlap on same indexed column = `Advisory(kind="potential_conflict", ...)`.

3. **Schema drift.** On startup and via `refresh_schema`, compute `schema_hash`. If different from latest `db_schema_snapshot.schema_hash`, write a new snapshot and emit `Advisory(kind="schema_drift", ...)` on the next call.

`Advisory` schema:

```python
class Advisory(BaseModel):
    kind: Literal["cache_stale", "potential_conflict", "schema_drift", "middleware_error"]
    db_namespace: str
    message: str
    confidence: float  # 0..1
    requires_approval: bool = False  # always False until Phase 3
    metadata: dict = {}
```

Advisories ride in the tool response's `meta.advisories` array. Claude Code's tool-result rendering surfaces them naturally.

## Deployment shape

- **Replit Reserved VM**, not Autoscale. Streamable HTTP needs long-lived connections; Autoscale kills them.
- One `psycopg_pool.AsyncConnectionPool` instantiated in FastAPI's `lifespan`, shared by both the MCP server and the dashboard.
- `pgvector` extension enabled in migration. HNSW indexes (`m=16, ef_construction=64`) on every embedding column.
- Secrets: Replit Secrets, surfaced via `pydantic-settings`. Never a raw `os.environ` read outside `config.py`.
- Logging: `structlog` to stdout in JSON. Optional LangSmith via env var.

## Threat model summary

The three risks that actually matter for this layer:

1. **Prompt injection via cached query results.** A `users.bio` row containing "ignore previous instructions" lands in the agent's context when we inject memory. **Mitigation:** wrap all DB-returned strings in `<<<UNTRUSTED_DATA>>>` blocks, strip control characters and `<|...|>` tokens on write to memory.

2. **Tool poisoning.** If the upstream DB MCP server is compromised, its tool descriptions can attack mneme. **Mitigation:** pin tool descriptions in memory on first connect; emit `Advisory(kind="tool_drift")` if descriptions change between sessions (Phase 2).

3. **Memory poisoning.** Stored episodes containing injected instructions replay on every recall. **Mitigation:** sanitize on write, downweight episodes from errored calls in retrieval, require `confirmed_by_user=true` for notes that become system-prompt-level guidance.

We do not enable MCP sampling. We do not run with service-role DB keys. We do not write to downstream databases. Those three rules close the worst of the "lethal trifecta" surface.
