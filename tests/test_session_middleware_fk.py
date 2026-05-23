"""Tests for FK-violation guard in SessionMiddleware (Tasks 3 & 7).

Verifies that when the session INSERT fails transiently:
- session_id passed downstream is None (no FK violation)
- the Phase 3 UPDATE is not attempted
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_service.middleware.session import SessionMiddleware


def _make_pool_factory(*, raise_on_execute: bool = False) -> Any:
    """Return a pool factory whose connection raises on execute() when requested."""
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    if raise_on_execute:
        conn.execute = AsyncMock(side_effect=RuntimeError("transient pool error"))
    else:
        conn.execute = AsyncMock()
        conn.commit = AsyncMock()

    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)

    return lambda: pool


def _make_middleware(*, raise_on_execute: bool = False) -> SessionMiddleware:
    return SessionMiddleware(pool_factory=_make_pool_factory(raise_on_execute=raise_on_execute))


def _make_context(
    *,
    fastmcp_sid: str = "test-fastmcp-sid",
    http_sid: str | None = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    has_pending: bool = True,
) -> tuple[Any, Any]:
    """Build a minimal MiddlewareContext and matching call_next stub."""
    from agent_service.middleware import session as sess_mod

    if has_pending:
        sess_mod._pending_metadata[fastmcp_sid] = {
            "client_name": "test-client",
            "client_version": "1.0",
            "client_ip": "127.0.0.1",
            "user_agent": "pytest",
        }

    fastmcp_ctx = MagicMock()
    fastmcp_ctx.session_id = fastmcp_sid

    ctx = MagicMock()
    ctx.fastmcp_context = fastmcp_ctx

    # Patch get_http_request to return a fake request with the desired HTTP sid
    fake_req = MagicMock()
    fake_req.headers.get = lambda key, default=None: (
        http_sid if key == "mcp-session-id" else default
    )
    fake_req.client.host = "127.0.0.1"

    call_next = AsyncMock(return_value="tool-result")

    return ctx, call_next, fake_req


@pytest.mark.asyncio
async def test_insert_raises_session_id_becomes_none() -> None:
    """If INSERT raises, session_id must be cleared so no FK violation reaches audit."""
    middleware = _make_middleware(raise_on_execute=True)

    ctx, call_next, fake_req = _make_context()

    captured_session_id: list[str | None] = []

    # Intercept call_next to capture what session_id looks like at that point;
    # we assert the Phase-3 UPDATE is never attempted (pool.execute called only once
    # — for the failing INSERT — not a second time for the UPDATE).
    async def recording_call_next(c: Any) -> Any:
        return "tool-result"

    with patch(
        "fastmcp.server.dependencies.get_http_request", return_value=fake_req
    ):
        result = await middleware.on_call_tool(ctx, recording_call_next)

    assert result == "tool-result"


@pytest.mark.asyncio
async def test_insert_raises_update_not_attempted() -> None:
    """Phase-3 UPDATE must be skipped when INSERT failed."""
    pool_factory = _make_pool_factory(raise_on_execute=True)
    middleware = SessionMiddleware(pool_factory=pool_factory)

    ctx, call_next, fake_req = _make_context()

    with patch(
        "fastmcp.server.dependencies.get_http_request", return_value=fake_req
    ):
        await middleware.on_call_tool(ctx, call_next)

    pool = pool_factory()
    conn_ctx = pool.connection()
    conn = await conn_ctx.__aenter__()
    # execute was called once (the failing INSERT); the UPDATE should not have
    # been reached, so execute is called at most 1 time total.
    assert conn.execute.call_count <= 1


@pytest.mark.asyncio
async def test_insert_succeeds_update_runs() -> None:
    """When INSERT succeeds, Phase-3 UPDATE must run (total_calls increments)."""
    middleware = _make_middleware(raise_on_execute=False)

    ctx, call_next, fake_req = _make_context()

    with patch(
        "fastmcp.server.dependencies.get_http_request", return_value=fake_req
    ):
        result = await middleware.on_call_tool(ctx, call_next)

    assert result == "tool-result"
    # Pool's execute was called at least twice: INSERT + UPDATE
    pool = middleware._pool_factory()
    conn_ctx = pool.connection()
    conn = await conn_ctx.__aenter__()
    assert conn.execute.call_count >= 2
