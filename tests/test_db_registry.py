"""Unit tests for the database registry tools.

Uses the unit_pool fixture (Helium via $DATABASE_URL) with TRUNCATE isolation.
The registered_database table is included in the truncate set after each test.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from psycopg_pool import AsyncConnectionPool

from agent_service.db_registry import load_active_databases, register_db_registry_tools

pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")


def _make_registry_server(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> FastMCP:  # type: ignore[type-arg]
    server: FastMCP = FastMCP("registry-test")  # type: ignore[type-arg]
    register_db_registry_tools(server, lambda: pool)
    return server


@pytest.mark.asyncio
async def test_register_and_list(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    server = _make_registry_server(unit_pool)

    result = await server.call_tool(
        "register_database",
        {
            "namespace": "my_db",
            "mcp_url": "https://my-db.example.com/mcp",
            "routing_keywords": ["my_table", "my_schema"],
            "description": "Test database",
            "tool_prefix": "mydb",
        },
    )
    assert result is not None
    text = result.content[0].text  # type: ignore[union-attr]
    assert "registered" in text

    listed = await server.call_tool("list_registered_databases", {})
    assert listed is not None
    payload = listed.structured_content or listed.content[0].text  # type: ignore[union-attr]
    assert "my_db" in str(payload)


@pytest.mark.asyncio
async def test_get_database_info(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    server = _make_registry_server(unit_pool)

    await server.call_tool(
        "register_database",
        {
            "namespace": "info_db",
            "mcp_url": "https://info-db.example.com/mcp",
            "routing_keywords": ["info_"],
            "description": "Info test database",
        },
    )

    info = await server.call_tool("get_database_info", {"namespace": "info_db"})
    assert info is not None
    text = str(info.structured_content or info.content[0].text)  # type: ignore[union-attr]
    assert "info_db" in text
    assert "https://info-db.example.com/mcp" in text


@pytest.mark.asyncio
async def test_update_database_config(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    server = _make_registry_server(unit_pool)

    await server.call_tool(
        "register_database",
        {
            "namespace": "update_db",
            "mcp_url": "https://old-url.example.com/mcp",
            "routing_keywords": ["old_kw"],
        },
    )

    result = await server.call_tool(
        "update_database_config",
        {
            "namespace": "update_db",
            "mcp_url": "https://new-url.example.com/mcp",
            "routing_keywords": ["new_kw1", "new_kw2"],
        },
    )
    assert result is not None
    assert "updated" in str(result.structured_content or result.content[0].text)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_deregister_database(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    server = _make_registry_server(unit_pool)

    await server.call_tool(
        "register_database",
        {
            "namespace": "remove_db",
            "mcp_url": "https://remove-db.example.com/mcp",
            "routing_keywords": ["remove_"],
        },
    )

    result = await server.call_tool("deregister_database", {"namespace": "remove_db"})
    assert result is not None
    assert "deregistered" in str(result.structured_content or result.content[0].text)  # type: ignore[union-attr]

    # Should appear in list with is_active=False
    listed = await server.call_tool("list_registered_databases", {})
    payload = str(listed.structured_content or listed.content[0].text)  # type: ignore[union-attr]
    assert "remove_db" in payload


@pytest.mark.asyncio
async def test_load_active_databases(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    server = _make_registry_server(unit_pool)

    await server.call_tool(
        "register_database",
        {
            "namespace": "active_db",
            "mcp_url": "https://active.example.com/mcp",
            "routing_keywords": ["active_", "active_table"],
            "tool_prefix": "active",
        },
    )
    await server.call_tool(
        "register_database",
        {
            "namespace": "inactive_db",
            "mcp_url": "https://inactive.example.com/mcp",
            "routing_keywords": [],
        },
    )
    await server.call_tool("deregister_database", {"namespace": "inactive_db"})

    active = await load_active_databases(unit_pool)
    namespaces = [db.namespace for db in active]
    assert "active_db" in namespaces
    assert "inactive_db" not in namespaces

    active_db = next(db for db in active if db.namespace == "active_db")
    assert active_db.mcp_url == "https://active.example.com/mcp"
    assert active_db.routing_keywords == ["active_", "active_table"]
    assert active_db.tool_prefix == "active"
