"""Test the proxy passthrough (deliverable a).

Uses an in-process fake FastMCP server as the upstream so no network is needed.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from fastmcp.server import create_proxy


def _make_fake_upstream() -> FastMCP:  # type: ignore[type-arg]
    server: FastMCP = FastMCP("fake-upstream")  # type: ignore[type-arg]

    @server.tool
    def echo(message: str) -> str:
        return f"echo: {message}"

    @server.tool
    def add(a: int, b: int) -> int:
        return a + b

    return server


@pytest.mark.asyncio
async def test_proxy_exposes_upstream_tools() -> None:
    """Proxy re-exposes the upstream tool list unchanged."""
    upstream = _make_fake_upstream()
    proxy = create_proxy(upstream, name="test-proxy")

    tools = await proxy.list_tools()
    tool_names = {t.name for t in tools}
    assert "echo" in tool_names
    assert "add" in tool_names


@pytest.mark.asyncio
async def test_proxy_call_returns_upstream_result() -> None:
    """A tool call through the proxy produces the upstream result."""
    upstream = _make_fake_upstream()
    proxy = create_proxy(upstream, name="test-proxy")

    result = await proxy.call_tool("echo", {"message": "hello"})
    assert result is not None
    # ToolResult.content is a list of content blocks; first block has text
    assert "hello" in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_proxy_mount_on_parent() -> None:
    """Tools from mounted proxy appear on the parent server."""
    upstream = _make_fake_upstream()
    proxy = create_proxy(upstream, name="test-proxy")

    parent: FastMCP = FastMCP("parent")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)

    tools = await parent.list_tools()
    tool_names = {t.name for t in tools}
    assert "echo" in tool_names
    assert "add" in tool_names


@pytest.mark.asyncio
async def test_proxy_add_tool() -> None:
    """add tool returns the correct sum."""
    upstream = _make_fake_upstream()
    proxy = create_proxy(upstream, name="test-proxy")

    result = await proxy.call_tool("add", {"a": 3, "b": 4})
    assert result is not None
    assert "7" in result.content[0].text  # type: ignore[union-attr]
