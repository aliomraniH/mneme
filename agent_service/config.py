from __future__ import annotations

import functools
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Required
    database_url: SecretStr
    upstream_db_mcp_url: str

    # Optional — needed for Phase 2+
    anthropic_api_key: SecretStr | None = None
    langsmith_api_key: SecretStr | None = None
    langchain_tracing_v2: bool = False
    langsmith_project: str = "mneme"

    # Server
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 5000
    log_level: str = "INFO"

    # Routing
    mneme_namespaces: list[str] = ["pg_main", "pinecone_main", "saaz_demo", "default"]

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

    def database_url_str(self) -> str:
        return self.database_url.get_secret_value()

    def as_log_safe(self) -> dict[str, Any]:
        return {
            "upstream_db_mcp_url": self.upstream_db_mcp_url,
            "mcp_server_host": self.mcp_server_host,
            "mcp_server_port": self.mcp_server_port,
            "log_level": self.log_level,
            "mneme_namespaces": self.mneme_namespaces,
            "session_idle_timeout_seconds": self.session_idle_timeout_seconds,
            "per_call_timeout_seconds": self.per_call_timeout_seconds,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "langsmith_enabled": self.langsmith_api_key is not None,
        }


@functools.cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
