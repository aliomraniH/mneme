from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server import create_proxy

from agent_service.config import Settings


def build_mneme_server() -> FastMCP:  # type: ignore[type-arg]
    """Construct the top-level FastMCP server at module scope.

    No pool or upstream client is created here. Both are deferred to
    FastAPI's lifespan (see server.py) and injected at startup.
    """
    return FastMCP("mneme", instructions="mneme memory-and-advisory MCP middleware")


def mount_upstream(mneme: FastMCP, settings: Settings) -> None:  # type: ignore[type-arg]
    """Create a proxy to the upstream saaz MCP and mount it on mneme.

    Called once from the FastAPI lifespan after the pool is ready.
    All tools from the upstream server are exposed verbatim — no renaming,
    no description rewrites. saaz will add its own prefixes in a future release.
    """
    proxy = create_proxy(settings.upstream_db_mcp_url, name="saaz-proxy")
    # namespace=None: upstream tool names pass through unchanged
    mneme.mount(proxy, namespace=None)
