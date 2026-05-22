# mneme — Status

## Phase 1 — Observe ✅ done

| Deliverable | Status | Notes |
|---|---|---|
| Scaffold: pyproject.toml, Makefile, ruff, mypy, pre-commit | ✅ done | All tooling wired |
| migrations/0001_init.sql | ✅ applied | Applied by Replit Agent on Helium |
| migrations/0002_sessions.sql | ✅ applied | Applied on startup via `apply_pending_migrations` |
| (a) Proxy passthrough | ✅ done | `create_proxy` + `mneme.mount` in lifespan |
| (b) Memory store | ✅ done | `write_episode`, `get_recent_episodes`, `write_expertise_note` |
| (c) Audit middleware | ✅ done | `AuditMiddleware` writes 1 row per call, injects `meta.audit_id` |
| (d) Namespace router | ✅ done | `route_to_namespace` keyword-based, full test coverage |
| (e) Observability | ✅ done | structlog JSON, `/healthz` with pool check |
| Session tracking | ✅ done | `mcp_session` table, idle reaper, shutdown marking |
| Rate limiting | ✅ done | `slowapi` 60 req/min per IP on `/mcp` |
| Per-call timeout | ✅ done | `TimeoutMiddleware` 30 s |
| Result summary cap | ✅ done | 4 096-byte cap with `truncated` flag |
| No-op auth middleware | ✅ done | `RequireAuthMiddleware` pass-through, Phase 2 ready |
| docs/UPSTREAM.md | ✅ done | Moved from `attached_assets/` |
| Unit tests (Helium + TRUNCATE isolation) | ✅ done | 39 tests: routing, memory, audit, proxy, provisioners |
| Integration tests | ✅ passing | All 3 tests in `tests/integration/test_smoke.py` green |
| Vercel provisioner | ✅ done | `provision_database` + `list_database_regions`; get-or-error semantics (dashboard-first) |
| DB registry (registered_database table + 5 CRUD tools) | ✅ done | migration 0003, `agent_service/db_registry.py`, 5 tests |
| Generic db_mcp server (env-driven tool prefix) | ✅ done | `db_mcp/server.py`; neon_mcp is now a 5-line shim |

**Phase 1 exit criterion: ✅ done**

Claude Code makes tool calls against mneme; audit rows land in `query_episode`
with correct `db_namespace`, and `mcp_session` tracks the session end-to-end.

---

### B4 — Live smoke evidence (2026-05-22)

**Genre breakdown query** (`saaz_query` → `SELECT genre, count(*) AS n FROM artist GROUP BY genre ORDER BY n DESC`):

```json
{
  "row_count": 4,
  "rows": [
    {"genre": "indie_persian_jazz", "n": 13},
    {"genre": "traditional",        "n": 8},
    {"genre": "persian_jazz",       "n": 8},
    {"genre": "other",              "n": 1}
  ]
}
```

**Audit-row summary** (`SELECT db_namespace, tool_name, count(*) FROM query_episode GROUP BY ...`):

```
db_namespace         | tool_name                    | count
----------------------------------------------------------------
saaz_demo            | saaz_query                   | 3
saaz_demo            | saaz_list_artists            | 2
saaz_demo            | saaz_stats                   | 1
saaz_demo            | saaz_list_tables             | 1
```

All rows land in `db_namespace = 'saaz_demo'` — namespace routing is working correctly.

**tools/list** (confirms provisioner tools are exposed alongside saaz_ tools):

```
['provision_database', 'list_database_regions',
 'saaz_list_tables', 'saaz_describe_table', 'saaz_query',
 'saaz_get_artist', 'saaz_list_artists', 'saaz_search_artists', 'saaz_stats']
```

---

### Step 5 — provision_database live evidence (2026-05-22)

`provision_database(name="neon-purple-kite", provider="vercel", region="iad1")` response:

```json
{
  "status":               "created",
  "provider":             "vercel",
  "database_name":        "neon-purple-kite",
  "suggested_namespace":  "neon_purple_kite",
  "host":                 "ep-broad-dawn-aq9rs7up-pooler.c-8.us-east-1.aws.neon.tech",
  "port":                 "5432",
  "database":             "neondb",
  "username":             "neondb_owner",
  "region":               "iad1",
  "provider_id":          "store_25ZLaez6thQ6CeNp",
  "connection_url":       "postgresql://neondb_owner:***@...neon.tech/neondb?sslmode=require",
  "next_steps":           "1. Add to Replit Secrets: DATABASE_URL_NEON_PURPLE_KITE=<connection_url>\n..."
}
```

**Note on Vercel API**: Vercel retired `POST /v1/storage/postgres` (legacy Vercel Postgres
product) in 2024. Postgres databases are now Neon integration stores created through the
Vercel dashboard (Storage → Create Database → Neon). The provisioner was updated to use
**get-or-error semantics**: it lists existing stores via `GET /v1/storage/stores`, matches
by name (with hyphen/underscore normalisation), and reads secrets via
`GET /v1/storage/stores/{id}/secrets`.  If the named store does not exist it raises a
`ProvisionError` with dashboard-creation instructions.

---

### Step 6 — neon-purple-kite wired as second upstream (2026-05-22)

**Architecture**: local `neon_mcp/server.py` (FastMCP on port 3000) proxies the
`DATABASE_URL_NEON_PURPLE_KITE` Neon database and exposes 4 tools:
`neon_list_tables`, `neon_describe_table`, `neon_query`, `neon_stats`.

**Config**:
- `UPSTREAM_DB_MCP_SERVERS = {"saaz_demo":"https://saaz-aloomrani.replit.app/mcp","neon_purple_kite":"http://localhost:3000/mcp/"}`
- `NAMESPACE_ROUTING_KEYWORDS` — JSON with `"neon_purple_kite": ["neon_", "patient", "mrn", ...]`
- Startup log confirms: `"upstream_db_mcp_servers": ["saaz_demo", "neon_purple_kite"]`

**tools/list** (13 tools total):
```
['provision_database', 'list_database_regions',
 'saaz_list_tables', 'saaz_describe_table', 'saaz_query',
 'saaz_get_artist', 'saaz_list_artists', 'saaz_search_artists', 'saaz_stats',
 'neon_list_tables', 'neon_describe_table', 'neon_query', 'neon_stats']
```

**Routing fix**: env var must be `NAMESPACE_ROUTING_KEYWORDS` (not `MNEME_NAMESPACE_ROUTING_KEYWORDS`)
— pydantic-settings reads field names directly with no prefix.

**Audit rows** (neon_ tools → `db_namespace = 'neon_purple_kite'`):
```
neon_purple_kite  neon_query
neon_purple_kite  neon_describe_table
neon_purple_kite  neon_stats
neon_purple_kite  neon_list_tables
```

**neon-purple-kite schema** (patients table, 0 synthetic rows):
`id, mrn, first_name, last_name, birth_date, is_synthetic, …`

**Test suite**: 39 unit tests + 3 integration tests — all pass.

---

---

### Capability probe & test battery (2026-05-22)

**Live MCP probe results** — all tools called via the connected MCP session:

| Tool | Result | Notes |
|---|---|---|
| `saaz_stats` | ✅ | 30 artists, 4 genres, enrichment cost returned |
| `saaz_list_tables` | ✅ | 6 tables with row counts |
| `saaz_list_artists` (no filter) | ✅ | 30 artists |
| `saaz_list_artists` (genre=indie_persian_jazz) | ✅ | 13 artists |
| `saaz_list_artists` (status=deceased) | ✅ | 1 artist (Shajarian) |
| `saaz_get_artist` (valid slug) | ✅ | Full record: bio, links, images, provenance |
| `saaz_get_artist` (invalid slug) | ✅ | Returns `{"error": "..."}`, no crash |
| `saaz_query` SELECT + JOIN | ✅ | Multi-table JOIN executes correctly |
| `saaz_query` INSERT | ✅ BLOCKED | "Only SELECT...statements are allowed" |
| `saaz_query` DROP TABLE | ✅ BLOCKED | Same rejection |
| `saaz_query` UPDATE | ✅ BLOCKED | Same rejection |
| `saaz_search_artists` (semantic) | ✅ | pgvector ranked results, 100% embedding coverage |
| `list_registered_databases` | ✅ | Returns registry with audit trail |
| `get_database_info` | ✅ | Returns entry + call stats |
| `neon_list_tables` | ❌ SSL error | local `neon_mcp/server.py` not running on port 3000 |
| `neon_stats` | ❌ empty `{}` | Same root cause |

**Data integrity checks** (via live `saaz_query` probes):
- Embedding coverage: **100%** across all 4 genres (30/30 artists)
- Bio presence: **29+/30** artists have bios > 50 chars
- Provenance: `data_provenance` has rows for all 30 artists via `fact_id` join
- `anthropic_web` source confidence: **≥ 0.9** on all enriched artists

**Schema correction discovered**: `data_provenance` uses `fact_id` (not `artist_id`) with a polymorphic `fact_table` column. Updated join pattern: `dp.fact_id = a.id WHERE dp.fact_table = 'artist'`.

**Test counts** (2026-05-22):

| Suite | Pass | Skip | Notes |
|---|---|---|---|
| `make test` (unit, no DB) | **56** | 18 | 18 skipped = need DATABASE_URL |
| `tests/test_prompt_battery.py` | **19** | 0 | New: realistic Claude usage scenarios |
| `tests/integration/test_mcp_capabilities.py` | pending | — | Requires `MNEME_INTEGRATION=1` + live server |

**Known issue**: `neon_mcp/server.py` local process not started at boot time. Start it or add it to Replit's `.replit` run command to fix `neon_*` tools.

---

## Phase 2 — Advise (not started)
## Phase 2.5 — Per-DB experts (not started)
## Phase 3 — Surface (not started)
## Phase 4 — Approve and act (deferred)
