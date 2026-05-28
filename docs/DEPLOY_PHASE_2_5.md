# Phase 2.5 Deployment — Replit Agent Prompt

Copy-paste this prompt verbatim into the Replit AI Agent to deploy Phase 2.5.

---

## Replit Agent Prompt

```
You are deploying Phase 2.5 of mneme on the Reserved VM Replit environment.
The branch `claude/optimistic-mayer-m0PEA` is already merged and the code is
committed. Follow each step in order; stop and report if any step fails.

---

### Step 0 — Pull latest code

```bash
git fetch origin claude/optimistic-mayer-m0PEA
git checkout claude/optimistic-mayer-m0PEA
git pull origin claude/optimistic-mayer-m0PEA
```

Expected: working tree is clean, HEAD matches the latest remote commit.

---

### Step 1 — Add the new Replit Secrets

Open Replit → Secrets and add the following (do NOT commit real values):

| Secret key                  | Required?      | Notes |
|-----------------------------|----------------|-------|
| `OPENAI_API_KEY`            | Recommended    | Enables semantic ranking (text-embedding-3-small). Without it, mneme falls back to rule-based ranking (still works). |
| `VOYAGE_API_KEY`            | Alternative    | Use instead of OpenAI. Most Voyage models are NOT 1536-dim; set EMBEDDING_DIMENSIONS if you use this. |
| `MNEME_PROJECT_ID`          | Optional       | Fallback project label when no X-Mneme-Project header is sent. Default: `default`. |
| `WARM_UP_MAX_TOKENS`        | Optional       | Token budget for warm_up context block. Default: `2000`. |
| `THREAD_REFRESH_MAX_TOKENS` | Optional       | Budget for thread_refresh new additions only. Default: `1200`. |
| `MEMORY_DEDUP_THRESHOLD`    | Optional       | Cosine similarity for memory merge (0..1). Default: `0.92`. |
| `EMBEDDING_DIMENSIONS`      | Optional       | Must match `vector()` column size. Default: `1536`. |

The existing secrets (`DATABASE_URL`, `ANTHROPIC_API_KEY`, `UPSTREAM_DB_MCP_URL`
or `UPSTREAM_DB_MCP_SERVERS`, `LANGSMITH_API_KEY`) should already be set.
If any are missing, set them from `.env.example` as a reference.

---

### Step 2 — Apply migration 0004 (runs automatically on boot)

Migration `migrations/0004_project_memory.sql` is applied automatically by
`apply_pending_migrations()` at startup. You can verify it ran cleanly by
checking the server logs for:

```
applied migration  path=migrations/0004_project_memory.sql
```

If the migration has already been applied, the log line will be absent (it is
idempotent due to `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF
NOT EXISTS`).

---

### Step 3 — Run the test suite

```bash
make test
```

Expected: 128 tests pass, 0 failures. If any test fails, check:
- `DATABASE_URL` resolves to Helium (hostname `helium` must be reachable from
  inside Replit; it will not work from a laptop).
- The `pgvector` extension is installed: `SELECT extname FROM pg_extension WHERE extname='vector';`
- Phase 2.5 tables exist: `\dt project_memory` and `\dt session_context_cache` in psql.

---

### Step 4 — Boot the server

```bash
make run
```

Or let Replit auto-start via `.replit → run`. Watch logs for:

```
mneme started  phase=2  port=5000
```

Confirm Phase 2.5 tools are registered by checking the MCP tool list:

```bash
curl -s -X POST https://<your-replit-hostname>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | grep -o '"name":"[^"]*"' | sort
```

Expected tool names (among others):
- `warm_up`
- `thread_refresh`
- `log_context_summary`
- `remember`
- `get_project_memory`

---

### Step 5 — Smoke tests

Run these four calls in order against the live server. Replace
`<SESSION_ID>` with the `mcp-session-id` header value returned by `initialize`.

**5a. Initialize a session:**
```bash
curl -s -X POST https://<hostname>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-Mneme-Project: smoke-test' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
  -D -   # capture headers to get mcp-session-id
```

**5b. warm_up:**
```bash
curl -s -X POST https://<hostname>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'mcp-session-id: <SESSION_ID>' \
  -H 'X-Mneme-Project: smoke-test' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"warm_up","arguments":{"project_goal":"test deployment","db":"saaz"}}}'
```
Expected: JSON with `"db": "saaz"`, `"schema_summary"`, `"memory_entries": []` (first run has no history), `"cache_version": 1`.

**5c. log_context_summary:**
```bash
curl -s -X POST https://<hostname>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'mcp-session-id: <SESSION_ID>' \
  -H 'X-Mneme-Project: smoke-test' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"log_context_summary","arguments":{"summary":"deployment smoke test","key_findings":["deployment ok"],"db":"saaz"}}}'
```
Expected: `"logged": true`, `"action": "created"`.

**5d. thread_refresh:**
```bash
curl -s -X POST https://<hostname>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'mcp-session-id: <SESSION_ID>' \
  -H 'X-Mneme-Project: smoke-test' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"thread_refresh","arguments":{"thread_summary":"shifting to testing refresh delta","db":"saaz"}}}'
```
Expected: `"cache_version": 2`, `"drop_keys": [...]`, `"add": {"memory_entries": [...]}`.

If all four return non-error JSON, Phase 2.5 is live.

---

### Step 6 — Clean up smoke-test data (optional)

The smoke-test data lives under `project_id = "smoke-test"` in the
`project_memory` and `session_context_cache` tables. It is harmless to leave;
it will not affect other projects. If you want it removed:

```sql
DELETE FROM project_memory        WHERE project_id = 'smoke-test';
DELETE FROM session_context_cache WHERE project_id = 'smoke-test';
```

---

### Rollback procedure

If anything is broken after Step 3:

1. **Do NOT** revert the migration — the tables are additive and presence of
   `project_memory` / `session_context_cache` does not affect Phase 1 or Phase 2
   tools.
2. To disable Phase 2.5 tools only, set `DISABLE_WARMUP_TOOLS=1` (the server
   checks this env var and skips `register_warmup_tools`). No restart needed
   once Replit picks up the new secret.
3. If the server won't boot, revert to the last stable commit on the branch:
   ```bash
   git revert HEAD --no-edit
   git push origin claude/optimistic-mayer-m0PEA
   ```

---
```
