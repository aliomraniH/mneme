"""Tests for Tasks 5 & 6: persistent _session_client_info cache.

Verifies:
- After on_initialize captures clientInfo, a subsequent on_call_tool writes
  an entry to _session_client_info keyed by the canonical session_id.
- get_session_client_info returns the correct client_name and client_version.
- idle_session_reaper evicts entries for sessions older than idle_seconds.
- mark_sessions_shutdown clears the entire cache.
- A reconnect (existing http_sid) does not duplicate an existing cache key.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_service.middleware import session as sess_mod
from agent_service.middleware.session import (
    SessionMiddleware,
    get_session_client_info,
    idle_session_reaper,
    mark_sessions_shutdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool_factory(*, raise_on_execute: bool = False) -> Any:
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    if raise_on_execute:
        conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
    else:
        conn.execute = AsyncMock()
        conn.commit = AsyncMock()
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    return lambda: pool


def _make_call_tool_context(
    *,
    fastmcp_sid: str = "fmcp-sid-001",
    http_sid: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    has_pending: bool = True,
) -> tuple[Any, Any, Any]:
    if has_pending:
        sess_mod._pending_metadata[fastmcp_sid] = {
            "client_name": "claude-code",
            "client_version": "1.0",
            "client_ip": "127.0.0.1",
            "user_agent": "pytest",
        }
    fastmcp_ctx = MagicMock()
    fastmcp_ctx.session_id = fastmcp_sid
    ctx = MagicMock()
    ctx.fastmcp_context = fastmcp_ctx
    fake_req = MagicMock()
    fake_req.headers.get = lambda key, default=None: (
        http_sid if key == "mcp-session-id" else default
    )
    fake_req.client.host = "127.0.0.1"
    call_next = AsyncMock(return_value="result")
    return ctx, call_next, fake_req


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_info_cached_after_first_tool_call() -> None:
    """_session_client_info must be populated after the first successful INSERT."""
    sess_mod._session_client_info.clear()
    sess_mod._pending_metadata.clear()

    middleware = SessionMiddleware(pool_factory=_make_pool_factory())
    http_sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    ctx, call_next, fake_req = _make_call_tool_context(
        fastmcp_sid="fmcp-001", http_sid=http_sid
    )

    with patch("fastmcp.server.dependencies.get_http_request", return_value=fake_req):
        await middleware.on_call_tool(ctx, call_next)

    info = get_session_client_info(http_sid)
    assert info is not None
    assert info["client_name"] == "claude-code"
    assert info["client_version"] == "1.0"


@pytest.mark.asyncio
async def test_get_session_client_info_returns_none_for_unknown() -> None:
    assert get_session_client_info("nonexistent-session-id") is None


@pytest.mark.asyncio
async def test_idle_reaper_evicts_stale_cache_entries() -> None:
    """idle_session_reaper must evict entries older than idle_seconds."""
    import asyncio

    sess_mod._session_client_info.clear()

    # Plant a stale entry (inserted 2 seconds ago, idle threshold = 1 second)
    stale_sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    sess_mod._session_client_info[stale_sid] = {
        "client_name": "old-client",
        "client_version": "0.1",
        "inserted_at": time.monotonic() - 2.0,
    }

    # Pool returns no reaped DB rows (the DB update finds nothing to reap)
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    cur.fetchall = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=cur)
    conn.commit = AsyncMock()
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)

    # Use a shutdown event that is set immediately after the first sleep
    shutdown_ev = asyncio.Event()

    sleep_calls = 0

    async def fake_sleep(_: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        # Signal shutdown after the first sleep so the loop exits after one cycle
        shutdown_ev.set()

    with patch("agent_service.middleware.session.asyncio.sleep", side_effect=fake_sleep):
        await asyncio.wait_for(
            idle_session_reaper(
                pool_factory=lambda: pool,
                idle_seconds=1,
                check_interval_seconds=0,
                shutdown_event=shutdown_ev,
            ),
            timeout=5.0,
        )

    # The stale entry should have been evicted by the TTL sweep
    assert get_session_client_info(stale_sid) is None


@pytest.mark.asyncio
async def test_mark_sessions_shutdown_clears_cache() -> None:
    """mark_sessions_shutdown must clear _session_client_info entirely."""
    sess_mod._session_client_info.clear()
    sess_mod._session_client_info["some-sid"] = {
        "client_name": "x", "client_version": "1", "inserted_at": time.monotonic()
    }

    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    pool = AsyncMock()
    pool.connection = MagicMock(return_value=conn)

    await mark_sessions_shutdown(pool)

    assert sess_mod._session_client_info == {}


@pytest.mark.asyncio
async def test_reconnect_does_not_duplicate_cache_entry() -> None:
    """A reconnect (existing_http_sid in on_initialize) must not overwrite a live entry."""
    sess_mod._session_client_info.clear()
    existing_sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    sess_mod._session_client_info[existing_sid] = {
        "client_name": "original",
        "client_version": "2.0",
        "inserted_at": time.monotonic(),
    }

    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)

    middleware = SessionMiddleware(pool_factory=lambda: pool)

    fake_req = MagicMock()
    fake_req.headers.get = lambda key, default=None: (
        existing_sid if key == "mcp-session-id" else default
    )
    fake_req.client.host = "127.0.0.1"

    fake_client_info = MagicMock()
    fake_client_info.name = "reconnecting-client"
    fake_client_info.version = "3.0"

    ctx = MagicMock()
    ctx.fastmcp_context = MagicMock()
    ctx.fastmcp_context.session_id = "fmcp-reconnect"
    ctx.message.params.clientInfo = fake_client_info
    call_next = AsyncMock(return_value=None)

    with patch("fastmcp.server.dependencies.get_http_request", return_value=fake_req):
        await middleware.on_initialize(ctx, call_next)

    # Original entry must not be overwritten
    info = get_session_client_info(existing_sid)
    assert info is not None
    assert info["client_name"] == "original"
