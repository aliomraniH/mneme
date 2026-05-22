"""Test the audit middleware (deliverable c).

Verifies that every tool call writes one query_episode row with correct
fields, and that audit_id appears in the response metadata.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from psycopg_pool import AsyncConnectionPool

from agent_service.memory.episodes import get_recent_episodes
from agent_service.middleware.audit import AuditMiddleware

pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")


def _make_instrumented_server(
    pool_factory: Callable[[], AsyncConnectionPool],  # type: ignore[type-arg]
) -> FastMCP:  # type: ignore[type-arg]
    upstream: FastMCP = FastMCP("fake-upstream")  # type: ignore[type-arg]

    @upstream.tool
    def greet(name: str) -> str:
        return f"Hello, {name}"

    @upstream.tool
    def fail_always() -> str:
        raise RuntimeError("upstream is broken")

    proxy = create_proxy(upstream, name="proxy")

    parent: FastMCP = FastMCP("parent")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)
    parent.add_middleware(AuditMiddleware(pool_factory=pool_factory))
    return parent


@pytest.mark.asyncio
async def test_audit_row_written_on_success(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    server = _make_instrumented_server(lambda: unit_pool)

    result = await server.call_tool("greet", {"name": "Alice"})
    assert result is not None

    episodes = await get_recent_episodes(unit_pool, "default")
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.tool_name == "greet"
    assert ep.source == "ok"
    assert ep.error is None
    assert ep.duration_ms is not None
    assert ep.duration_ms >= 0


@pytest.mark.asyncio
async def test_audit_id_in_response_meta(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    server = _make_instrumented_server(lambda: unit_pool)

    result = await server.call_tool("greet", {"name": "Bob"})
    assert result is not None
    assert result.meta is not None
    assert "audit_id" in result.meta


@pytest.mark.asyncio
async def test_audit_row_written_on_error(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    server = _make_instrumented_server(lambda: unit_pool)

    with pytest.raises((RuntimeError, ToolError)):
        await server.call_tool("fail_always", {})

    episodes = await get_recent_episodes(unit_pool, "default")
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.source == "error"
    assert ep.error is not None


@pytest.mark.asyncio
async def test_audit_namespace_routing(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    """namespace_keywords_factory routes tool calls to the correct namespace."""
    upstream: FastMCP = FastMCP("fake")  # type: ignore[type-arg]

    @upstream.tool
    def search_artists(q: str) -> str:
        return "ok"

    proxy = create_proxy(upstream, name="proxy")
    parent: FastMCP = FastMCP("parent")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)

    # Pass explicit project keywords via the factory (mirrors production behaviour
    # where keywords come from NAMESPACE_ROUTING_KEYWORDS / DB registry).
    project_keywords: dict[str, list[str]] = {
        "saaz_demo": ["artist", "song", "persian", "jazz", "saaz", "genre"],
    }
    parent.add_middleware(
        AuditMiddleware(
            pool_factory=lambda: unit_pool,
            namespace_keywords_factory=lambda: project_keywords,
        )
    )

    await parent.call_tool("search_artists", {"q": "test"})

    saaz_episodes = await get_recent_episodes(unit_pool, "saaz_demo")
    assert len(saaz_episodes) == 1


@pytest.mark.asyncio
async def test_result_summary_capped_at_4096(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    """Audit row result_summary is capped; truncated=True when payload exceeds limit."""
    upstream: FastMCP = FastMCP("fake")  # type: ignore[type-arg]

    @upstream.tool
    def big_result() -> str:
        return "x" * 6000

    proxy = create_proxy(upstream, name="proxy")
    parent: FastMCP = FastMCP("parent")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)
    parent.add_middleware(AuditMiddleware(pool_factory=lambda: unit_pool))

    result = await parent.call_tool("big_result", {})

    # Caller gets the full result
    assert result is not None
    full_text = result.content[0].text  # type: ignore[union-attr]
    assert len(full_text) == 6000

    # Audit row is capped
    episodes = await get_recent_episodes(unit_pool, "default")
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.truncated is True
    summary_str = str(ep.result_summary)
    assert len(summary_str.encode()) <= 4096 + 200  # small slack for JSON framing
