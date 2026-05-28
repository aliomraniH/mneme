-- mneme Phase 2.5: project-scoped extended memory + per-session context cache.
-- Run after 0003_database_registry.sql.
--
-- Adds:
--   * project_id columns on mcp_session, query_episode, expertise_note
--   * project_memory     — long-lived cross-session knowledge per project/db
--   * session_context_cache — versioned snapshot of what mneme injected into
--                             the live session (warm_up / thread_refresh)
--
-- All guarded with IF NOT EXISTS so re-runs are idempotent.

-- ---------------------------------------------------------------------------
-- Project scoping on existing tables
-- ---------------------------------------------------------------------------
ALTER TABLE mcp_session
    ADD COLUMN IF NOT EXISTS project_id TEXT;

ALTER TABLE query_episode
    ADD COLUMN IF NOT EXISTS project_id TEXT;

ALTER TABLE expertise_note
    ADD COLUMN IF NOT EXISTS project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_episode_project
    ON query_episode (project_id, db_namespace, ts DESC);

-- ---------------------------------------------------------------------------
-- project_memory: the persistent, self-improving brain per (project, db)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_memory (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    TEXT NOT NULL,
    db_namespace  TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'project'
                  CHECK (scope IN ('project', 'general')),
    entry_type    TEXT NOT NULL DEFAULT 'thread_summary'
                  CHECK (entry_type IN (
                      'thread_summary', 'expertise', 'schema_fact', 'user_note'
                  )),
    content       TEXT NOT NULL,
    key_findings  JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence    REAL NOT NULL DEFAULT 0.7 CHECK (confidence BETWEEN 0 AND 1),
    source        TEXT NOT NULL DEFAULT 'agent_inferred'
                  CHECK (source IN (
                      'thread_summary', 'warm_up_summary',
                      'user_confirmed', 'agent_inferred'
                  )),
    call_count    INTEGER NOT NULL DEFAULT 0,
    embedding     vector(1536),
    last_used_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_project_memory_lookup
    ON project_memory (project_id, db_namespace, scope, last_used_at DESC);

CREATE INDEX IF NOT EXISTS idx_project_memory_embedding
    ON project_memory USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- session_context_cache: versioned record of injected context per session
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_context_cache (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    db_namespace    TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    payload         JSONB NOT NULL,        -- {keys:[...], entries:[...], schema:{...}}
    token_estimate  INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, version)
);

CREATE INDEX IF NOT EXISTS idx_context_cache_session
    ON session_context_cache (session_id, version DESC);
