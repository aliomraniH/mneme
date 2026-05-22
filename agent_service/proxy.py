from __future__ import annotations

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy

from agent_service.config import Settings


def build_mneme_server() -> FastMCP:
    """Construct the top-level FastMCP server at module scope.

    No pool or upstream client is created here. Both are deferred to
    FastAPI's lifespan (see server.py) and injected at startup.
    """
    return FastMCP("mneme", instructions="mneme memory-and-advisory MCP middleware")


def mount_upstream_map(
    mneme: FastMCP,
    servers: dict[str, str],
) -> None:
    """Mount one proxy per entry in `servers` (namespace → URL mapping).

    Tool names are passed through verbatim (namespace=None).
    verify=False works around the Replit NixOS sandbox's incomplete CA bundle.
    """
    for namespace, url in servers.items():
        client = Client(url, verify=False)
        proxy = create_proxy(client, name=f"{namespace}-proxy")
        mneme.mount(proxy, namespace=None)


def mount_upstream(mneme: FastMCP, settings: Settings) -> None:
    """Mount upstreams from Settings only (env-var config, no DB registry).

    Kept for backward compatibility with tests and one-off scripts.
    The lifespan in server.py uses mount_upstream_map with the merged map.
    """
    mount_upstream_map(mneme, settings.all_upstream_servers())
