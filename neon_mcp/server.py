"""Lightweight FastMCP server exposing the Neon (neon-purple-kite) Postgres database.

Tools exposed:
  neon_list_tables      — list public tables
  neon_describe_table   — columns + types for a table
  neon_query            — run a read-only SELECT
  neon_stats            — row counts for all public tables

Run standalone:
  uvicorn neon_mcp.server:app --host 0.0.0.0 --port 3000
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncGenerator
from typing import Any

import psycopg
import structlog
from fastapi import FastAPI
from fastmcp import FastMCP
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)

_pool: AsyncConnectionPool | None = None
_MAX_ROWS = 500
_MAX_BYTES = 4096


# ── FastMCP server (module scope) ─────────────────────────────────────────────

mcp = FastMCP("neon-db", instructions="Read-only access to the neon-purple-kite Postgres database.")
_mcp_http_app = mcp.http_app(path="/")


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_pool() -> AsyncConnectionPool:  # type: ignore[return]
    if _pool is None:
        raise RuntimeError("Pool not initialised")
    return _pool


def _is_select(sql: str) -> bool:
    stripped = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", "", stripped)
    first = stripped.strip().split()[0].upper() if stripped.strip() else ""
    return first in ("SELECT", "WITH", "EXPLAIN")


def _truncate(rows: list[dict[str, Any]], max_bytes: int = _MAX_BYTES) -> tuple[list[dict[str, Any]], bool]:
    import json
    out: list[dict[str, Any]] = []
    total = 2
    for row in rows:
        chunk = len(json.dumps(row, default=str))
        if total + chunk > max_bytes:
            return out, True
        out.append(row)
        total += chunk + 1
    return out, False


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool
async def neon_list_tables() -> list[str]:
    """List all user tables in the public schema."""
    async with _get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [r[0] for r in await cur.fetchall()]


@mcp.tool
async def neon_describe_table(table_name: str) -> list[dict[str, str]]:
    """Return column names, data types, and nullability for a table.

    Args:
        table_name: Name of the table to describe (must be in the public schema).
    """
    async with _get_pool().connection() as conn:
        cur = await conn.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        rows = await cur.fetchall()
    if not rows:
        raise ValueError(f"Table {table_name!r} not found in public schema.")
    return [
        {"column": r[0], "type": r[1], "nullable": r[2], "default": r[3] or ""}
        for r in rows
    ]


@mcp.tool
async def neon_query(sql: str, limit: int = 100) -> dict[str, Any]:
    """Run a read-only SELECT query and return results.

    Args:
        sql:   A SELECT (or WITH…SELECT) statement. DML is rejected.
        limit: Maximum rows to return (default 100, max 500).
    """
    if not _is_select(sql):
        raise ValueError("Only SELECT (and WITH … SELECT) statements are allowed.")
    limit = min(max(1, limit), _MAX_ROWS)
    safe_sql = f"SELECT * FROM ({sql}) _q LIMIT {limit}"
    async with _get_pool().connection() as conn:
        await conn.set_autocommit(True)
        async with conn.cursor() as cur:
            await cur.execute(safe_sql)
            cols = [d.name for d in cur.description or []]
            raw_rows = await cur.fetchall()

    rows = [dict(zip(cols, r)) for r in raw_rows]
    truncated_rows, was_truncated = _truncate(rows)
    return {
        "row_count": len(rows),
        "truncated": was_truncated,
        "rows": truncated_rows,
    }


@mcp.tool
async def neon_stats() -> dict[str, int]:
    """Return approximate row counts for every table in the public schema."""
    async with _get_pool().connection() as conn:
        cur = await conn.execute(
            """
            SELECT relname, reltuples::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY relname
            """
        )
        return {r[0]: r[1] for r in await cur.fetchall()}


# ── FastAPI lifespan (wraps FastMCP's own lifespan) ───────────────────────────

async def _lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None, None]:
    global _pool
    db_url = os.environ["DATABASE_URL_NEON_PURPLE_KITE"]
    _pool = AsyncConnectionPool(db_url, min_size=1, max_size=4, open=False)
    await _pool.open()
    log.info("neon_mcp_pool_ready")
    async with _mcp_http_app.router.lifespan_context(fastapi_app):
        yield
    await _pool.close()
    log.info("neon_mcp_pool_closed")


app = FastAPI(lifespan=_lifespan)
app.mount("/mcp", _mcp_http_app)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    if _pool is None:
        return {"status": "starting"}
    try:
        async with _get_pool().connection() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
