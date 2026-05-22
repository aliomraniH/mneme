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
| Unit tests (Helium + TRUNCATE isolation) | ✅ done | 37 tests: routing, memory, audit, proxy, provisioners |
| Integration tests | ✅ passing | All 3 tests in `tests/integration/test_smoke.py` green |
| Vercel provisioner | ✅ done | `provision_database` + `list_database_regions` native tools |

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

## Phase 2 — Advise (not started)
## Phase 2.5 — Per-DB experts (not started)
## Phase 3 — Surface (not started)
## Phase 4 — Approve and act (deferred)
