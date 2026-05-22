# Upstream DB MCP Server

mneme proxies all DB tool calls to a FastMCP server hosted in the `saaz` Repl.
This file documents that server's tools, safety guarantees, and how to test the
connection from within the mneme codebase.

## Connection URLs

| Environment | URL |
|---|---|
| Production (published) | `https://saaz-aloomrani.replit.app/mcp` |
| Dev workspace (Repl open) | `https://da7891db-5379-4802-bdac-2a2c1d5e6fe8-00-cobeful7wxin.janeway.replit.dev/mcp` |

The production URL is what `UPSTREAM_DB_MCP_URL` should be set to in Replit Secrets.
The dev URL is only reachable while that Repl's workspace is open; don't use it in config.

## What's running on saaz

- **Workflow:** `MCP Server` — `uv run python -m scripts.mcp_server` on `0.0.0.0:5000`
- **Deployment:** Autoscale; run command `uv run python -m scripts.mcp_server`
- **Weekly refresh:** APScheduler inside the same process on `REFRESH_CRON=0 6 * * 1`
  (Mondays 06:00 UTC). Verified live via full MCP handshake:
  `initialize → notifications/initialized → tools/list → tools/call stats`.

## Tools exposed (7 total)

| Tool | Purpose |
|---|---|
| `list_tables` | Enumerate the 6 saaz tables |
| `describe_table` | Column info for a named saaz table |
| `query` | Ad-hoc read-only SELECT (single statement; DDL/write blocked; 10 s timeout; 1 000-row cap) |
| `get_artist` | One artist with links, images, and provenance |
| `list_artists` | Filter artists by genre / status |
| `search_artists` | Semantic search via pgvector + OpenAI embeddings |
| `stats` | Dataset health: row counts, embedding coverage, enrichment spend |

Verified dataset state (as of initial setup):
- 30 artists across 3–4 genre buckets
- 30 embeddings (100 % coverage)
- ~$1.87 enrichment spend

## Safety guarantees baked into saaz

1. `describe_table` uses a `SAAZ_TABLES` whitelist — mneme's own tables are unreachable.
2. Every `query` call runs inside `SET LOCAL default_transaction_read_only = on` +
   `statement_timeout = 10s` and pre-validates that the statement is a single
   `SELECT` or `WITH … SELECT`. Multi-statement, write, and DDL calls are rejected
   before they reach Postgres.
3. mneme's tables (`query_episode`, `expertise_note`, etc.) are not reachable through
   this MCP server at all.

## Testing the connection

Run this from inside the Replit VM (or from the mneme integration test suite):

```bash
# Step 1: initialize — grab the mcp-session-id response header
curl -i -X POST "$UPSTREAM_DB_MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{
      "protocolVersion":"2024-11-05",
      "capabilities":{},
      "clientInfo":{"name":"test","version":"1"}
    }
  }'

# Step 2: list tools (replace SESSION_ID)
curl -X POST "$UPSTREAM_DB_MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Step 3: call stats
curl -X POST "$UPSTREAM_DB_MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"stats","arguments":{}}}'
```

Expected `stats` response: ~30 artists, ~30 embeddings, ~$1.87 spend.

## Future: tool renaming

The current tool names (`list_tables`, `query`, etc.) are saaz's internal names.
A future saaz update will add `saaz_` prefixes and LLM-oriented descriptions.
When that ships, update the integration smoke test names in `tests/integration/`
and flag the change in `docs/STATUS.md`.
