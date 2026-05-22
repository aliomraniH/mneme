-- mneme session tracking + audit column extensions.
-- Adds mcp_session table and extends query_episode with per-call metadata.
-- Run after 0001_init.sql.

-- ---------------------------------------------------------------------------
-- Session registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mcp_session (
    session_id      TEXT PRIMARY KEY,
    client_name     TEXT,
    client_version  TEXT,
    client_ip       INET,
    user_agent      TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    end_reason      TEXT CHECK (
        end_reason IN ('explicit', 'idle_timeout', 'shutdown')
        OR end_reason IS NULL
    ),
    total_calls     INT NOT NULL DEFAULT 0,
    total_errors    INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mcp_session_active
    ON mcp_session (last_seen_at DESC) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_mcp_session_client
    ON mcp_session (client_name, started_at DESC);

-- ---------------------------------------------------------------------------
-- Extend query_episode with caller metadata and audit fields
-- ---------------------------------------------------------------------------
ALTER TABLE query_episode
    ADD COLUMN IF NOT EXISTS session_id     TEXT REFERENCES mcp_session(session_id),
    ADD COLUMN IF NOT EXISTS client_name    TEXT,
    ADD COLUMN IF NOT EXISTS client_version TEXT,
    ADD COLUMN IF NOT EXISTS client_ip      INET,
    ADD COLUMN IF NOT EXISTS user_agent     TEXT,
    ADD COLUMN IF NOT EXISTS truncated      BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_episode_session
    ON query_episode (session_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_episode_client_name
    ON query_episode (client_name, ts DESC);
