-- mneme initial schema. FROZEN. Do not modify.
-- New columns or tables require a new numbered migration.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()

-- ---------------------------------------------------------------------------
-- Semantic memory: schema snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS db_schema_snapshot (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    db_namespace  TEXT NOT NULL,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    tables        JSONB NOT NULL,        -- [{name, columns:[{name,type,nullable}], fks, sample}]
    schema_hash   TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('introspect', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_schema_snapshot_ns_time
    ON db_schema_snapshot (db_namespace, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_schema_snapshot_hash
    ON db_schema_snapshot (db_namespace, schema_hash);

-- ---------------------------------------------------------------------------
-- Semantic memory: per-column documentation + embeddings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS column_doc (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    db_namespace  TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    column_name   TEXT NOT NULL,
    description   TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('introspect', 'agent_inferred', 'user_confirmed')),
    embedding     vector(1536),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (db_namespace, table_name, column_name, source)
);

CREATE INDEX IF NOT EXISTS idx_column_doc_ns_table
    ON column_doc (db_namespace, table_name);

CREATE INDEX IF NOT EXISTS idx_column_doc_embedding
    ON column_doc USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Episodic memory: query -> outcome pairs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS query_episode (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    db_namespace    TEXT NOT NULL,
    thread_id       TEXT,
    tool_name       TEXT NOT NULL,
    user_query      TEXT,                 -- natural-language ask, if known
    tool_params     JSONB NOT NULL,
    result_summary  JSONB,                -- digest, not full payload
    row_count       INTEGER,
    duration_ms     INTEGER,
    error           TEXT,
    source          TEXT NOT NULL DEFAULT 'ok' CHECK (source IN ('ok', 'error')),
    embedding       vector(1536),
    audit_id        UUID NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_episode_ns_time
    ON query_episode (db_namespace, ts DESC);

CREATE INDEX IF NOT EXISTS idx_episode_audit
    ON query_episode (audit_id);

CREATE INDEX IF NOT EXISTS idx_episode_tool
    ON query_episode (tool_name, ts DESC);

CREATE INDEX IF NOT EXISTS idx_episode_embedding
    ON query_episode USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Procedural memory: learned advice / user corrections (Phase 2+)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expertise_note (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    db_namespace       TEXT NOT NULL,
    note               TEXT NOT NULL,
    trigger_pattern    TEXT,              -- e.g. "WHERE created_at >"
    confidence         REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confirmed_by_user  BOOLEAN NOT NULL DEFAULT FALSE,
    embedding          vector(1536),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_expertise_ns
    ON expertise_note (db_namespace, confidence DESC);

CREATE INDEX IF NOT EXISTS idx_expertise_embedding
    ON expertise_note USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Cache events (so the agent can reason about staleness, Phase 2+)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cache_event (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    db_namespace    TEXT NOT NULL,
    cache_key       TEXT NOT NULL,
    written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_seconds     INTEGER,
    invalidated_at  TIMESTAMPTZ,
    reason          TEXT CHECK (reason IN ('ttl', 'schema_change', 'manual', 'eviction'))
);

CREATE INDEX IF NOT EXISTS idx_cache_event_ns_key
    ON cache_event (db_namespace, cache_key, written_at DESC);
