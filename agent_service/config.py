from __future__ import annotations

import functools
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Required
    database_url: SecretStr

    # Upstream DB MCP server(s).
    # UPSTREAM_DB_MCP_URL: single server, backward-compatible (assigned to "default" namespace).
    # UPSTREAM_DB_MCP_SERVERS: JSON mapping of namespace→URL for multiple databases.
    #   Example: {"saaz_demo": "https://saaz.replit.app/mcp", "pg_main": "https://pg.replit.app/mcp"}
    # At least one must be set.
    upstream_db_mcp_url: str | None = None
    upstream_db_mcp_servers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_at_least_one_upstream(self) -> Settings:
        if not self.upstream_db_mcp_url and not self.upstream_db_mcp_servers:
            raise ValueError(
                "Set UPSTREAM_DB_MCP_URL (single server) or "
                "UPSTREAM_DB_MCP_SERVERS (JSON mapping of namespace→URL)."
            )
        return self

    def all_upstream_servers(self) -> dict[str, str]:
        """Return all configured upstream servers as a namespace→URL mapping.

        UPSTREAM_DB_MCP_SERVERS takes precedence. If only UPSTREAM_DB_MCP_URL
        is set, it is assigned to the 'default' namespace.
        """
        if self.upstream_db_mcp_servers:
            return dict(self.upstream_db_mcp_servers)
        assert self.upstream_db_mcp_url is not None
        return {"default": self.upstream_db_mcp_url}

    # Per-namespace routing keywords (JSON mapping).
    # When set, overrides the built-in defaults in routing.py for all namespaces.
    # Example: {"my_db": ["keyword1", "keyword2"], "pinecone_main": ["pinecone"]}
    # Leave empty to use routing.py's built-in defaults.
    namespace_routing_keywords: dict[str, list[str]] = Field(default_factory=dict)

    # Vercel integration — database provisioning via provision_database tool
    # Get a token at https://vercel.com/account/tokens (Storage write scope).
    vercel_api_token: SecretStr | None = None
    vercel_team_id: str | None = None  # required for team-scoped Vercel accounts

    # Optional — needed for Phase 2+
    anthropic_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None
    langchain_tracing_v2: bool = False
    langsmith_project: str = "mneme"

    # Server
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 5000
    log_level: str = "INFO"

    # Memory
    embedding_model: str = "text-embedding-3-small"

    # Session management
    session_idle_timeout_seconds: int = 1800  # 30 minutes
    session_idle_check_interval_seconds: int = 60
    per_call_timeout_seconds: int = 30
    graceful_shutdown_timeout_seconds: int = 5

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Audit
    result_summary_max_bytes: int = 4096

    # Trusted proxy hops for client_ip resolution.  (Task 10)
    #
    # Background:
    #   X-Forwarded-For can be trivially forged by any caller.  Reading
    #   XFF[0] directly (the old behaviour) allowed a client to lie about its
    #   IP address.  The safe default is to trust only the TCP peer address
    #   (req.client.host), which is set by the kernel and cannot be spoofed.
    #
    # How to set this:
    #   0 (default) — use req.client.host; safe for direct connections or
    #                 setups where you do not control any reverse proxy.
    #   1           — mneme sits behind exactly one trusted reverse proxy
    #                 (e.g. Replit's internal router, nginx, or a load balancer).
    #                 Use XFF[-(1+1)] = XFF[-2].
    #   N           — mneme sits behind exactly N trusted proxies.
    #                 Use XFF[-(N+1)].
    #
    # Set via env var: TRUSTED_PROXY_HOPS=1
    trusted_proxy_hops: int = 0

    def database_url_str(self) -> str:
        return self.database_url.get_secret_value()

    def as_log_safe(self) -> dict[str, Any]:
        return {
            "upstream_db_mcp_servers": list(self.all_upstream_servers().keys()),
            "mcp_server_host": self.mcp_server_host,
            "mcp_server_port": self.mcp_server_port,
            "log_level": self.log_level,
            "session_idle_timeout_seconds": self.session_idle_timeout_seconds,
            "per_call_timeout_seconds": self.per_call_timeout_seconds,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "langsmith_enabled": self.langsmith_api_key is not None,
        }


@functools.cache
def get_settings() -> Settings:
    return Settings()
