"""Generic read-only Postgres MCP server.

Configured entirely via environment variables — no database-specific names
are baked into this file.  Deploy one instance per database; each instance
exposes four tools prefixed with DB_MCP_TOOL_PREFIX.

Required environment variables
-------------------------------
DB_MCP_TOOL_PREFIX        Short identifier used as tool-name prefix.
                          E.g. "neon" → tools are neon_query, neon_list_tables …
DB_MCP_NAME               Human-readable server name (shown in tools/list).
                          E.g. "neon-purple-kite"
DB_MCP_DATABASE_URL_ENV   Name of the env var that holds the Postgres DSN.
                          E.g. "DATABASE_URL_NEON_PURPLE_KITE"

The DSN itself is read at startup from the env var named by
DB_MCP_DATABASE_URL_ENV, so credentials are never baked into config files.

Typical startup
---------------
  DB_MCP_TOOL_PREFIX=neon \\
  DB_MCP_NAME=neon-purple-kite \\
  DB_MCP_DATABASE_URL_ENV=DATABASE_URL_NEON_PURPLE_KITE \\
  uvicorn db_mcp.server:app --host 0.0.0.0 --port 3000
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastmcp import FastMCP
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration — read once at import time so the server name and tool prefix
# are baked into the module-level FastMCP instance.
# ---------------------------------------------------------------------------
_PREFIX = os.getenv("DB_MCP_TOOL_PREFIX", "db")
_NAME = os.getenv("DB_MCP_NAME", "generic-postgres")
_URL_ENV_KEY = os.getenv("DB_MCP_DATABASE_URL_ENV", "DATABASE_URL")

_MAX_ROWS = 500
_MAX_BYTES = 4096

# ---------------------------------------------------------------------------
# Module-level FastMCP server
# ---------------------------------------------------------------------------
mcp: FastMCP = FastMCP(
    _NAME,
    instructions=f"Read-only Postgres access to the {_NAME} database.",
)
_mcp_http_app = mcp.http_app(path="/")

# ---------------------------------------------------------------------------
# Connection pool — populated in lifespan
# ---------------------------------------------------------------------------
_pool: AsyncConnectionPool | None = None


def _get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError(f"{_NAME}: connection pool is not initialised")
    return _pool


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def _is_select(sql: str) -> bool:
    """Return True iff the SQL starts with SELECT / WITH / EXPLAIN."""
    stripped = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", "", stripped)
    first = stripped.strip().split()[0].upper() if stripped.strip() else ""
    return first in ("SELECT", "WITH", "EXPLAIN")


def _truncate_rows(
    rows: list[dict[str, Any]],
    max_bytes: int = _MAX_BYTES,
) -> tuple[list[dict[str, Any]], bool]:
    out: list[dict[str, Any]] = []
    total = 2  # opening/closing brackets
    for row in rows:
        chunk = len(json.dumps(row, default=str))
        if total + chunk > max_bytes:
            return out, True
        out.append(row)
        total += chunk + 1
    return out, False


# ---------------------------------------------------------------------------
# Tool implementations (prefix-agnostic internal functions)
# ---------------------------------------------------------------------------
async def _impl_list_tables() -> list[str]:
    async with _get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [r[0] for r in await cur.fetchall()]


async def _impl_describe_table(table_name: str) -> list[dict[str, str]]:
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


async def _impl_query(sql: str, limit: int = 100) -> dict[str, Any]:
    if not _is_select(sql):
        raise ValueError("Only SELECT (or WITH … SELECT) statements are allowed.")
    limit = min(max(1, limit), _MAX_ROWS)
    safe_sql = f"SELECT * FROM ({sql}) _q LIMIT {limit}"
    async with _get_pool().connection() as conn:
        await conn.set_autocommit(True)
        async with conn.cursor() as cur:
            await cur.execute(safe_sql)
            cols = [d.name for d in cur.description or []]
            raw_rows = await cur.fetchall()
    rows = [dict(zip(cols, r, strict=False)) for r in raw_rows]
    result_rows, was_truncated = _truncate_rows(rows)
    return {"row_count": len(rows), "truncated": was_truncated, "rows": result_rows}


async def _impl_stats() -> dict[str, int]:
    async with _get_pool().connection() as conn:
        cur = await conn.execute(
            """
            SELECT relname, reltuples::bigint
            FROM   pg_class c
            JOIN   pg_namespace n ON n.oid = c.relnamespace
            WHERE  n.nspname = 'public' AND c.relkind = 'r'
            ORDER  BY relname
            """
        )
        return {r[0]: r[1] for r in await cur.fetchall()}


# ---------------------------------------------------------------------------
# Register tools with the configured prefix
# Each implementation function is cloned with a prefixed __name__ so FastMCP
# stores and exposes the correct tool name.
# ---------------------------------------------------------------------------
def _prefixed(fn: Any, suffix: str, description: str) -> Any:
    """Return fn with __name__, __qualname__, and __doc__ rewritten."""
    fn.__name__ = f"{_PREFIX}_{suffix}"
    fn.__qualname__ = f"{_PREFIX}_{suffix}"
    fn.__doc__ = description
    return fn


mcp.tool(
    _prefixed(
        _impl_list_tables,
        "list_tables",
        f"List all user tables in the {_NAME} public schema.",
    )
)
mcp.tool(
    _prefixed(
        _impl_describe_table,
        "describe_table",
        f"Return column names, data types, and nullability for a table in {_NAME}.\n\n"
        "Args:\n    table_name: Table name (must be in the public schema).",
    )
)
mcp.tool(
    _prefixed(
        _impl_query,
        "query",
        f"Run a read-only SELECT query against {_NAME} and return results.\n\n"
        "Args:\n"
        "    sql:   A SELECT (or WITH … SELECT) statement.  DML is rejected.\n"
        "    limit: Maximum rows to return (default 100, max 500).",
    )
)
mcp.tool(
    _prefixed(
        _impl_stats,
        "stats",
        f"Return approximate row counts for every table in the {_NAME} public schema.",
    )
)

# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None, None]:
    global _pool
    db_url = os.environ[_URL_ENV_KEY]
    # max_idle=240 recycles connections every 4 min — before Neon's serverless
    # idle-connection killer fires at ~5 min.  TCP keepalive params prevent the
    # SSL link from being silently torn down while a connection is in the pool.
    _pool = AsyncConnectionPool(
        db_url,
        min_size=1,
        max_size=4,
        max_idle=240,
        reconnect_timeout=5,
        kwargs={
            "keepalives": 1,
            "keepalives_idle": 60,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
        open=False,
    )
    await _pool.open()
    log.info("db_mcp_pool_ready", name=_NAME, prefix=_PREFIX)
    async with _mcp_http_app.router.lifespan_context(fastapi_app):
        yield
    await _pool.close()
    log.info("db_mcp_pool_closed", name=_NAME)


app = FastAPI(lifespan=_lifespan)
app.mount("/mcp", _mcp_http_app)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    if _pool is None:
        return {"status": "starting", "name": _NAME}
    try:
        async with _get_pool().connection() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok", "name": _NAME, "prefix": _PREFIX}
    except Exception as exc:
        return {"status": "error", "name": _NAME, "detail": str(exc)}
