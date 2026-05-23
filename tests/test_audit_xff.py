"""Tests for Task 10: stop trusting raw XFF; use req.client.host with optional
trusted-hop count.

With trusted_proxy_hops=0 (default), client_ip must be req.client.host.
With trusted_proxy_hops=1, client_ip must be XFF[-2] (the second-from-right).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_service.middleware.audit import AuditMiddleware
from agent_service.middleware.session import _resolve_client_ip


# ---------------------------------------------------------------------------
# Unit tests for _resolve_client_ip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "xff,peer_host,hops,expected",
    [
        # hops=0: always peer host
        (None,              "10.0.0.1", 0, "10.0.0.1"),
        ("1.2.3.4",         "10.0.0.1", 0, "10.0.0.1"),
        ("1.2.3.4, 5.6.7.8","10.0.0.1", 0, "10.0.0.1"),
        # hops=1: XFF[-2] = second-from-right
        ("1.2.3.4, 5.6.7.8","10.0.0.1", 1, "1.2.3.4"),
        # hops=1 but XFF has only 1 entry: fall back to peer host
        ("1.2.3.4",         "10.0.0.1", 1, "10.0.0.1"),
        # hops=1, XFF is None: fall back to peer host
        (None,              "10.0.0.1", 1, "10.0.0.1"),
        # hops=2: XFF[-3]
        ("a, b, c",         "10.0.0.1", 2, "a"),
        # hops=2 but not enough entries: fall back
        ("a, b",            "10.0.0.1", 2, "10.0.0.1"),
    ],
)
def test_resolve_client_ip(
    xff: str | None,
    peer_host: str,
    hops: int,
    expected: str,
) -> None:
    result = _resolve_client_ip(xff=xff, peer_host=peer_host, trusted_proxy_hops=hops)
    assert result == expected


# ---------------------------------------------------------------------------
# Integration test: AuditMiddleware records the correct client_ip
# ---------------------------------------------------------------------------

def _make_fake_req(
    xff: str | None = "1.2.3.4, 5.6.7.8",
    peer_host: str = "10.0.0.1",
    mcp_session_id: str | None = None,
) -> Any:
    fake_req = MagicMock()
    fake_req.client.host = peer_host

    def headers_get(key: str, default: Any = None) -> Any:
        m = {
            "x-forwarded-for": xff,
            "user-agent": "pytest",
            "mcp-session-id": mcp_session_id,
        }
        return m.get(key, default)

    fake_req.headers.get = headers_get
    return fake_req


def _pool_factory_noop() -> Any:
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    return pool


@pytest.mark.asyncio
async def test_audit_middleware_hops0_uses_peer_host() -> None:
    """With trusted_proxy_hops=0, AuditMiddleware stores req.client.host."""
    from agent_service.config import Settings

    captured: list[Any] = []

    async def fake_write_episode(pool: Any, episode: Any) -> Any:
        captured.append(episode)
        return episode.id

    fake_settings = MagicMock(spec=Settings)
    fake_settings.trusted_proxy_hops = 0

    fake_req = _make_fake_req(xff="1.2.3.4, 5.6.7.8", peer_host="10.0.0.1")
    pool = _pool_factory_noop()

    upstream: Any = MagicMock()
    upstream.tool = lambda f: f

    from fastmcp import FastMCP
    from fastmcp.server import create_proxy

    fmcp: FastMCP = FastMCP("fake")  # type: ignore[type-arg]

    @fmcp.tool
    def ping() -> str:
        return "pong"

    proxy = create_proxy(fmcp, name="proxy")
    parent: FastMCP = FastMCP("parent")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)
    middleware = AuditMiddleware(
        pool_factory=lambda: pool,
        trusted_proxy_hops=0,
    )
    parent.add_middleware(middleware)

    with (
        patch("fastmcp.server.dependencies.get_http_request", return_value=fake_req),
        patch(
            "agent_service.middleware.audit.write_episode",
            side_effect=fake_write_episode,
        ),
    ):
        await parent.call_tool("ping", {})

    assert len(captured) == 1
    assert captured[0].client_ip == "10.0.0.1"


@pytest.mark.asyncio
async def test_audit_middleware_hops1_uses_xff_penultimate() -> None:
    """With trusted_proxy_hops=1, AuditMiddleware stores XFF[-2]."""
    captured: list[Any] = []

    async def fake_write_episode(pool: Any, episode: Any) -> Any:
        captured.append(episode)
        return episode.id

    fake_req = _make_fake_req(xff="1.2.3.4, 5.6.7.8", peer_host="10.0.0.1")
    pool = _pool_factory_noop()

    from fastmcp import FastMCP
    from fastmcp.server import create_proxy

    fmcp: FastMCP = FastMCP("fake2")  # type: ignore[type-arg]

    @fmcp.tool
    def ping2() -> str:
        return "pong"

    proxy = create_proxy(fmcp, name="proxy")
    parent: FastMCP = FastMCP("parent2")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)
    middleware = AuditMiddleware(
        pool_factory=lambda: pool,
        trusted_proxy_hops=1,
    )
    parent.add_middleware(middleware)

    with (
        patch("fastmcp.server.dependencies.get_http_request", return_value=fake_req),
        patch(
            "agent_service.middleware.audit.write_episode",
            side_effect=fake_write_episode,
        ),
    ):
        await parent.call_tool("ping2", {})

    assert len(captured) == 1
    assert captured[0].client_ip == "1.2.3.4"
