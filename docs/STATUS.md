# mneme — Status

## Phase 1 — Observe (in progress)

| Deliverable | Status | Notes |
|---|---|---|
| Scaffold: pyproject.toml, Makefile, ruff, mypy, pre-commit | ✅ done | All tooling wired |
| migrations/0001_init.sql | ✅ applied | Applied by Replit Agent on Helium |
| migrations/0002_sessions.sql | ✅ written | Applied on startup via `apply_pending_migrations` |
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
| Unit tests (Helium + TRUNCATE isolation) | ✅ done | routing, memory, audit, proxy |
| Integration tests | ✅ written | `tests/integration/`, behind `@pytest.mark.integration` |

**Phase 1 exit criterion:** Claude Code makes 10 tool calls against mneme;
`SELECT count(*) FROM query_episode` returns 10. ← Pending smoke test (Step 5)
against live Helium. Run `MNEME_INTEGRATION=1 make test-integration` once deployed.

## Phase 2 — Advise (not started)
## Phase 2.5 — Per-DB experts (not started)
## Phase 3 — Surface (not started)
## Phase 4 — Approve and act (deferred)
