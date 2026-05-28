"""Phase 2.5 unit tests — pure helpers + tool behavior with mocks.

No DATABASE_URL needed for the pure-helper and mock-pool tests. DB-backed
tests (project_memory round-trips) request unit_pool and are skipped without it.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from agent_service.config import Settings
from agent_service.embeddings import EmbeddingClient
from agent_service.warmup import (
    _estimate_tokens,
    _schema_digest,
    _wrap_untrusted,
    register_warmup_tools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://x:y@helium/db",
        "upstream_db_mcp_servers": {"saaz": "http://localhost:3000/mcp"},
    }
    base.update(overrides)
    return Settings(**base)


def _empty_pool() -> Any:
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    # Default None: no schema snapshot, no dedup match. Tests that exercise
    # write_cache_version supply their own session/HTTP context.
    cursor.fetchone = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock(return_value=cursor)
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    return pool


def _result_value(result: Any) -> Any:
    sc = result.structured_content
    if sc is not None:
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    for block in result.content:
        if hasattr(block, "text"):
            try:
                return json.loads(block.text)
            except (json.JSONDecodeError, ValueError):
                return block.text
    return None


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_scales_with_length() -> None:
    assert _estimate_tokens("") == 1
    assert _estimate_tokens("x" * 4) == 2
    assert _estimate_tokens("x" * 400) == 101


# ---------------------------------------------------------------------------
# _wrap_untrusted
# ---------------------------------------------------------------------------

def test_wrap_untrusted_markers_and_content() -> None:
    wrapped = _wrap_untrusted({"content": "hello", "key_findings": ["a"]})
    assert wrapped.startswith("<<<UNTRUSTED_DATA>>>")
    assert wrapped.rstrip().endswith("<<<END>>>")
    assert "hello" in wrapped


# ---------------------------------------------------------------------------
# _schema_digest
# ---------------------------------------------------------------------------

def test_schema_digest_none_returns_zero_tokens() -> None:
    digest, tokens = _schema_digest(None)
    assert digest is None
    assert tokens == 0


def test_schema_digest_compacts_columns_to_names() -> None:
    snapshot = {
        "captured_at": "2026-01-01T00:00:00+00:00",
        "schema_hash": "abcdef123456789",
        "tables": [
            {"name": "artist", "columns": [{"name": "id", "type": "uuid"}, {"name": "bio"}]},
            {"name": "song", "columns": []},
        ],
    }
    digest, tokens = _schema_digest(snapshot)
    assert digest is not None
    assert digest["tables"][0]["name"] == "artist"
    assert digest["tables"][0]["columns"] == ["id", "bio"]
    assert digest["schema_hash"] == "abcdef123456"  # truncated to 12
    assert tokens > 0


# ---------------------------------------------------------------------------
# EmbeddingClient provider detection
# ---------------------------------------------------------------------------

def test_embedding_client_disabled_without_keys() -> None:
    client = EmbeddingClient(_settings())
    assert client.enabled is False
    assert client.provider == "none"


def test_embedding_client_prefers_openai() -> None:
    client = EmbeddingClient(_settings(openai_api_key="sk-test", voyage_api_key="vo-test"))
    assert client.enabled is True
    assert client.provider == "openai"


def test_embedding_client_falls_back_to_voyage() -> None:
    client = EmbeddingClient(_settings(voyage_api_key="vo-test"))
    assert client.provider == "voyage"


@pytest.mark.asyncio
async def test_embed_returns_none_when_disabled() -> None:
    client = EmbeddingClient(_settings())
    assert await client.embed("anything") is None


@pytest.mark.asyncio
async def test_embed_returns_none_for_empty_text() -> None:
    client = EmbeddingClient(_settings(openai_api_key="sk-test"))
    assert await client.embed("   ") is None


# ---------------------------------------------------------------------------
# warm_up tool — ambiguous namespace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warm_up_ambiguous_db_returns_error() -> None:
    settings = _settings(
        upstream_db_mcp_servers={"saaz": "http://a/mcp", "neon": "http://b/mcp"}
    )
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: _empty_pool(), settings)

    result = await mneme.call_tool("warm_up", {"project_goal": "x"})
    data = _result_value(result)
    assert "error" in data
    assert set(data["available"]) == {"saaz", "neon"}


@pytest.mark.asyncio
async def test_warm_up_single_db_no_memory_returns_payload() -> None:
    settings = _settings()  # single saaz upstream, no embedding key
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: _empty_pool(), settings)

    result = await mneme.call_tool("warm_up", {"project_goal": "build a report"})
    data = _result_value(result)
    assert data["db"] == "saaz"
    assert data["ranking_mode"] == "rule_based"  # no embedding key
    assert data["memory_entries"] == []  # empty pool
    assert "note" in data


# ---------------------------------------------------------------------------
# thread_refresh — no session / no cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thread_refresh_without_session_errors() -> None:
    settings = _settings()
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: _empty_pool(), settings)

    # No HTTP context → _current_session_id returns None
    result = await mneme.call_tool("thread_refresh", {"thread_summary": "x"})
    data = _result_value(result)
    assert "error" in data


# ---------------------------------------------------------------------------
# log_context_summary / remember — ambiguous db
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_context_summary_ambiguous_db() -> None:
    settings = _settings(
        upstream_db_mcp_servers={"a": "http://a/mcp", "b": "http://b/mcp"}
    )
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: _empty_pool(), settings)

    result = await mneme.call_tool(
        "log_context_summary", {"summary": "learned X", "key_findings": ["a"]}
    )
    data = _result_value(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_log_context_summary_writes_with_single_db() -> None:
    settings = _settings()
    pool = _empty_pool()
    # write_memory dedup query → fetchone None (no existing), then INSERT
    pool.connection().__aenter__.return_value.execute.return_value.fetchone = AsyncMock(
        return_value=None
    )
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: pool, settings)

    result = await mneme.call_tool(
        "log_context_summary",
        {"summary": "genre is nullable", "key_findings": ["use COALESCE"]},
    )
    data = _result_value(result)
    assert data["logged"] is True
    assert data["findings_count"] == 1
    assert data["action"] in ("created", "merged_with_existing")


@pytest.mark.asyncio
async def test_remember_full_confidence_path() -> None:
    settings = _settings()
    pool = _empty_pool()
    pool.connection().__aenter__.return_value.execute.return_value.fetchone = AsyncMock(
        return_value=None
    )
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: pool, settings)

    result = await mneme.call_tool(
        "remember", {"note": "always filter campaign_id=42", "scope": "project"}
    )
    data = _result_value(result)
    assert data["remembered"] is True
    assert data["scope"] == "project"


@pytest.mark.asyncio
async def test_remember_invalid_scope_coerced_to_project() -> None:
    settings = _settings()
    pool = _empty_pool()
    pool.connection().__aenter__.return_value.execute.return_value.fetchone = AsyncMock(
        return_value=None
    )
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: pool, settings)

    result = await mneme.call_tool(
        "remember", {"note": "x", "scope": "bogus_scope"}
    )
    data = _result_value(result)
    assert data["scope"] == "project"


# ---------------------------------------------------------------------------
# get_project_memory — empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_project_memory_empty() -> None:
    settings = _settings()
    mneme: FastMCP = FastMCP("test")  # type: ignore[type-arg]
    register_warmup_tools(mneme, lambda: _empty_pool(), settings)

    result = await mneme.call_tool("get_project_memory", {})
    data = _result_value(result)
    assert data == []
