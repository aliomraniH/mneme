-- 0003_database_registry.sql
-- Registry of upstream Postgres MCP servers known to mneme.
-- Populated via the register_database MCP tool; read at startup to
-- merge with UPSTREAM_DB_MCP_SERVERS / NAMESPACE_ROUTING_KEYWORDS env vars.

CREATE TABLE IF NOT EXISTS registered_database (
    namespace        TEXT PRIMARY KEY,
    mcp_url          TEXT NOT NULL,
    tool_prefix      TEXT,
    description      TEXT,
    routing_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_verified_at TIMESTAMPTZ
);

COMMENT ON TABLE registered_database IS
    'Each row maps a namespace label to its upstream MCP server URL plus '
    'routing keywords and metadata. Managed via the register_database MCP tool.';

CREATE INDEX IF NOT EXISTS idx_registered_database_active
    ON registered_database (is_active, namespace);
