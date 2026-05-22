from __future__ import annotations

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy

from agent_service.config import Settings


def build_mneme_server() -> FastMCP:  # type: ignore[type-arg]
    """Construct the top-level FastMCP server at module scope.

    No pool or upstream client is created here. Both are deferred to
    FastAPI's lifespan (see server.py) and injected at startup.
    """
    return FastMCP("mneme", instructions="mneme memory-and-advisory MCP middleware")


def mount_upstream(mneme: FastMCP, settings: Settings) -> None:  # type: ignore[type-arg]
    """Mount one proxy per configured upstream database MCP server.

    Called once from the FastAPI lifespan after the pool is ready. Each
    upstream is keyed by its namespace (from UPSTREAM_DB_MCP_SERVERS, or
    "default" when only UPSTREAM_DB_MCP_URL is set). All tools are exposed
    verbatim — no renaming, no description rewrites.

    verify=False works around the Replit NixOS sandbox's incomplete CA bundle
    which cannot verify *.replit.app certificates. Upstream URLs are
    operator-controlled so this is acceptable for dev/staging.
    """
    for namespace, url in settings.all_upstream_servers().items():
        client = Client(url, verify=False)
        proxy = create_proxy(client, name=f"{namespace}-proxy")
        # namespace=None: upstream tool names pass through unchanged
        mneme.mount(proxy, namespace=None)
