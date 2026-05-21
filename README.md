# mneme

> Memory-aware MCP middleware between Claude Code and your databases.

**mneme** sits between Claude Code and your database MCP servers (Postgres/pgvector, Pinecone, object stores) and gives it something it doesn't have on its own: persistent memory of every schema, query, and outcome — plus advisory signals about cache staleness, query conflicts, and schema drift.

Claude Code talks to `mneme` as a single MCP server. `mneme` proxies every upstream database tool unchanged, then layers memory and advisories on top.

> Named after **Mnēmē**, the Greek Muse of memory.

## Status

🚧 Early — Phase 1 (Observe). See [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What it is

- **A FastMCP middleware** that proxies your database MCP servers and intercepts every tool call.
- **A memory layer** on Postgres + pgvector — schema snapshots, query→outcome episodes, learned expertise notes, all namespaced per database.
- **An advisor** (Phase 2) that surfaces cache-staleness, conflict, and schema-drift warnings inline in tool responses.
- **Pull-only.** It observes, remembers, and advises. It never writes to your databases.

## What it isn't

- Not a text-to-SQL tool. It augments whatever upstream MCP server already exposes SQL or vector tools.
- Not a vector DB. Memory lives in your existing Postgres.
- Not a multi-agent framework. One agent at MVP, per-DB experts at Phase 2.5.
- Not an autonomous remediation system. Advisories are surfaced to the human (via Claude Code); no auto-fixes.

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full spec. The short version:

```
Claude Code ──MCP──▶ mneme (FastMCP + LangGraph) ──MCP──▶ your DB MCP server
                          │
                          ▼
                   Postgres + pgvector
                   (schema, episodes, notes)
```

## Stack

- Python 3.12, async everywhere
- [FastMCP](https://github.com/jlowin/fastmcp) 3.x — MCP server + middleware
- [LangGraph](https://github.com/langchain-ai/langgraph) 1.x — agent loop (Phase 2+)
- [`langchain-mcp-adapters`](https://pypi.org/project/langchain-mcp-adapters/) 0.2.x — MCP client
- [`langgraph-checkpoint-postgres`](https://pypi.org/project/langgraph-checkpoint-postgres/) 3.1.x — `AsyncPostgresStore` + `AsyncPostgresSaver`
- Postgres + [pgvector](https://github.com/pgvector/pgvector) — single store for all memory tiers
- FastAPI — `/healthz` and Phase 3 dashboard
- Deployed on [Replit](https://replit.com) Reserved VM

## Quick start

```bash
# clone and install
git clone https://github.com/YOU/mneme.git
cd mneme
uv sync                          # or: pip install -e ".[dev]"

# configure
cp .env.example .env
# edit .env: DATABASE_URL, UPSTREAM_DB_MCP_URL, ANTHROPIC_API_KEY

# migrate and run
make migrate
make run                          # boots on $MCP_SERVER_PORT
```

Add to Claude Code:

```bash
claude mcp add mneme https://your-mneme.replit.app/mcp
```

## Repo layout

```
agent_service/    Python package (server, middleware, memory, routing, advisors)
docs/             ARCHITECTURE.md, ROADMAP.md, STATUS.md
migrations/       Numbered SQL migrations (0001 is frozen)
tests/            pytest + pytest-asyncio
```

## Roadmap

| Phase | Goal | Week |
|---|---|---|
| 1 | Observe — every tool call lands in memory | 1 |
| 2 | Advise — cache/conflict/drift advisories in responses | 2 |
| 2.5 | Per-DB expert agents behind a LangGraph supervisor | 3 |
| 3 | Dashboard, LangSmith, weekly consolidation | 4 |
| 4 | Approve-and-act (write-back, human-in-loop) | deferred |

Details in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Security defaults

- Read-only DB roles. No service-role keys, ever.
- All memory writes pass through a sanitiser that strips prompt-injection patterns.
- Retrieved memory is wrapped in `<<<UNTRUSTED_DATA>>>` blocks before injection into the agent context.
- MCP sampling disabled.
- See `CLAUDE.md` § "Security defaults" for the full list.

## License

MIT.

## Acknowledgements

Built on the shoulders of FastMCP, LangGraph, pgvector, and the broader MCP ecosystem. The memory taxonomy follows [CoALA (Sumers et al., 2023)](https://arxiv.org/abs/2309.02427).
