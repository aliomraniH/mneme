"""Database registry — tools for managing upstream Postgres MCP connections.

Each entry in registered_database maps a namespace label to an upstream MCP
server URL, optional routing keywords, and metadata.  At mneme startup the
registry is merged with UPSTREAM_DB_MCP_SERVERS / NAMESPACE_ROUTING_KEYWORDS
env vars so new databases registered via these tools are picked up on next
restart without any code changes.

Tools exposed
-------------
register_database         Add or update a database entry.
list_registered_databases List all active (and recently deregistered) entries.
get_database_info         Details + episode statistics for one namespace.
update_database_config    Change URL, keywords, or description in place.
deregister_database       Soft-delete (mark inactive).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import structlog
from fastmcp import FastMCP
from psycopg_pool import AsyncConnectionPool

from agent_service.errors import MnemeError

log = structlog.get_logger(__name__)


class RegistryError(MnemeError):
    """Raised when a database registry operation fails."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class RegisteredDatabase:
    namespace: str
    mcp_url: str
    tool_prefix: str | None
    description: str | None
    routing_keywords: list[str]
    registered_at: datetime
    updated_at: datetime
    is_active: bool
    last_verified_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert datetime objects to ISO strings for JSON serialisation
        for key in ("registered_at", "updated_at", "last_verified_at"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
async def load_active_databases(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> list[RegisteredDatabase]:
    """Return all active entries from registered_database.

    Called from server.py lifespan to merge DB registry with env-var config.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT namespace, mcp_url, tool_prefix, description,
                   routing_keywords, registered_at, updated_at,
                   is_active, last_verified_at
            FROM   registered_database
            WHERE  is_active = TRUE
            ORDER  BY namespace
            """
        )
        rows = await cur.fetchall()
    return [
        RegisteredDatabase(
            namespace=r[0],
            mcp_url=r[1],
            tool_prefix=r[2],
            description=r[3],
            routing_keywords=r[4] if isinstance(r[4], list) else json.loads(r[4] or "[]"),
            registered_at=r[5],
            updated_at=r[6],
            is_active=r[7],
            last_verified_at=r[8],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
def register_db_registry_tools(
    mneme: FastMCP,  # type: ignore[type-arg]
    pool_factory: Callable[[], AsyncConnectionPool],  # type: ignore[type-arg]
) -> None:
    """Register database-management tools on the mneme FastMCP server."""

    @mneme.tool
    async def register_database(
        namespace: str,
        mcp_url: str,
        routing_keywords: list[str],
        description: str | None = None,
        tool_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Register a new upstream Postgres MCP server with mneme.

        Stores the entry in the registered_database table so it is loaded
        automatically on the next mneme restart.  Returns config snippets
        you should apply to UPSTREAM_DB_MCP_SERVERS and
        NAMESPACE_ROUTING_KEYWORDS in Replit Secrets.

        Args:
            namespace:        Short identifier for this database, e.g. "my_db".
                              Used as the db_namespace label in audit records.
            mcp_url:          URL of the upstream FastMCP server that exposes
                              this database's tools, e.g.
                              "https://my-db.replit.app/mcp".
            routing_keywords: List of keywords that appear in tool names or SQL
                              params when this database is being queried.
                              Mneme uses these to assign audit rows to the
                              correct namespace.  E.g. ["my_table", "mydb_"].
            description:      Human-readable description of the database.
            tool_prefix:      Tool-name prefix used by the upstream server
                              (e.g. "neon" → tools are neon_query, neon_stats).
                              Helps with routing; can be omitted.
        """
        pool = pool_factory()
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO registered_database
                    (namespace, mcp_url, tool_prefix, description,
                     routing_keywords, registered_at, updated_at, is_active)
                VALUES (%s, %s, %s, %s, %s::jsonb, now(), now(), TRUE)
                ON CONFLICT (namespace) DO UPDATE SET
                    mcp_url          = EXCLUDED.mcp_url,
                    tool_prefix      = EXCLUDED.tool_prefix,
                    description      = EXCLUDED.description,
                    routing_keywords = EXCLUDED.routing_keywords,
                    updated_at       = now(),
                    is_active        = TRUE
                """,
                (
                    namespace,
                    mcp_url,
                    tool_prefix,
                    description,
                    json.dumps(routing_keywords),
                ),
            )
            await conn.commit()

        log.info("database_registered", namespace=namespace, mcp_url=mcp_url)

        # Build the env-var values the operator needs to apply
        servers_hint = json.dumps({namespace: mcp_url})
        keywords_hint = json.dumps({namespace: routing_keywords})

        return {
            "status": "registered",
            "namespace": namespace,
            "mcp_url": mcp_url,
            "tool_prefix": tool_prefix or "",
            "routing_keywords": routing_keywords,
            "next_steps": "\n".join([
                "The database is stored in the registry and will be loaded on next restart.",
                "",
                "To activate immediately (without restart), also update Replit Secrets:",
                f"  UPSTREAM_DB_MCP_SERVERS — merge in: {servers_hint}",
                f"  NAMESPACE_ROUTING_KEYWORDS — merge in: {keywords_hint}",
                "",
                "Then restart mneme (Replit → Run) to mount the new upstream proxy.",
            ]),
        }

    @mneme.tool
    async def list_registered_databases() -> list[dict[str, Any]]:
        """List all databases registered with mneme.

        Returns both active and recently deregistered (is_active=False) entries
        so you have a full audit trail.
        """
        pool = pool_factory()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT namespace, mcp_url, tool_prefix, description,
                       routing_keywords, registered_at, updated_at,
                       is_active, last_verified_at
                FROM   registered_database
                ORDER  BY is_active DESC, namespace
                """
            )
            rows = await cur.fetchall()

        result = []
        for r in rows:
            kws = r[4] if isinstance(r[4], list) else json.loads(r[4] or "[]")
            result.append({
                "namespace": r[0],
                "mcp_url": r[1],
                "tool_prefix": r[2],
                "description": r[3],
                "routing_keywords": kws,
                "registered_at": r[5].isoformat() if r[5] else None,
                "updated_at": r[6].isoformat() if r[6] else None,
                "is_active": r[7],
                "last_verified_at": r[8].isoformat() if r[8] else None,
            })
        return result

    @mneme.tool
    async def get_database_info(namespace: str) -> dict[str, Any]:
        """Return registry entry and episode statistics for one namespace.

        Args:
            namespace: The namespace label, e.g. "saaz_demo" or "neon_purple_kite".
        """
        pool = pool_factory()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT namespace, mcp_url, tool_prefix, description,
                       routing_keywords, registered_at, updated_at,
                       is_active, last_verified_at
                FROM   registered_database
                WHERE  namespace = %s
                """,
                (namespace,),
            )
            row = await cur.fetchone()

            # Episode stats (may exist even without a registry entry)
            stats_cur = await conn.execute(
                """
                SELECT count(*)              AS total_calls,
                       count(*) FILTER (WHERE source = 'error') AS errors,
                       max(ts)               AS last_call,
                       avg(duration_ms)      AS avg_ms
                FROM   query_episode
                WHERE  db_namespace = %s
                """,
                (namespace,),
            )
            stats_row = await stats_cur.fetchone()

        info: dict[str, Any] = {"namespace": namespace}
        if row:
            kws = row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]")
            info.update({
                "mcp_url": row[1],
                "tool_prefix": row[2],
                "description": row[3],
                "routing_keywords": kws,
                "registered_at": row[5].isoformat() if row[5] else None,
                "updated_at": row[6].isoformat() if row[6] else None,
                "is_active": row[7],
                "last_verified_at": row[8].isoformat() if row[8] else None,
            })
        else:
            info["registered"] = False

        if stats_row:
            info["stats"] = {
                "total_calls": stats_row[0],
                "errors": stats_row[1],
                "last_call": stats_row[2].isoformat() if stats_row[2] else None,
                "avg_duration_ms": (
                    round(float(stats_row[3]), 1) if stats_row[3] else None
                ),
            }
        return info

    @mneme.tool
    async def update_database_config(
        namespace: str,
        mcp_url: str | None = None,
        routing_keywords: list[str] | None = None,
        description: str | None = None,
        tool_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Update the MCP URL, routing keywords, or description for a registered database.

        Only the fields you pass are updated; omit a field to leave it unchanged.

        Args:
            namespace:        The namespace label to update.
            mcp_url:          New upstream MCP server URL, if changing.
            routing_keywords: New routing keyword list, if changing.
            description:      New human-readable description, if changing.
            tool_prefix:      New tool prefix, if changing.
        """
        pool = pool_factory()

        # Build a dynamic SET clause for only the provided fields
        updates: list[str] = ["updated_at = now()"]
        values: list[Any] = []
        if mcp_url is not None:
            updates.append("mcp_url = %s")
            values.append(mcp_url)
        if routing_keywords is not None:
            updates.append("routing_keywords = %s::jsonb")
            values.append(json.dumps(routing_keywords))
        if description is not None:
            updates.append("description = %s")
            values.append(description)
        if tool_prefix is not None:
            updates.append("tool_prefix = %s")
            values.append(tool_prefix)

        if len(updates) == 1:
            return {"status": "no_change", "namespace": namespace}

        values.append(namespace)
        async with pool.connection() as conn:
            result = await conn.execute(
                f"UPDATE registered_database SET {', '.join(updates)} "  # noqa: S608
                "WHERE namespace = %s",
                values,
            )
            await conn.commit()
            if result.rowcount == 0:
                raise RegistryError(
                    f"Namespace {namespace!r} not found in registered_database."
                )

        log.info("database_config_updated", namespace=namespace)
        return {
            "status": "updated",
            "namespace": namespace,
            "note": "Restart mneme to apply routing keyword changes to the live proxy.",
        }

    @mneme.tool
    async def deregister_database(namespace: str) -> dict[str, str]:
        """Soft-delete a database from the registry (sets is_active = FALSE).

        The upstream proxy and audit history are unaffected; only future
        startups will skip mounting this namespace.

        Args:
            namespace: The namespace label to deregister.
        """
        pool = pool_factory()
        async with pool.connection() as conn:
            result = await conn.execute(
                "UPDATE registered_database "
                "SET is_active = FALSE, updated_at = now() "
                "WHERE namespace = %s",
                (namespace,),
            )
            await conn.commit()
            if result.rowcount == 0:
                raise RegistryError(
                    f"Namespace {namespace!r} not found in registered_database."
                )

        log.info("database_deregistered", namespace=namespace)
        return {
            "status": "deregistered",
            "namespace": namespace,
            "note": (
                f"Remove {namespace!r} from UPSTREAM_DB_MCP_SERVERS and "
                "NAMESPACE_ROUTING_KEYWORDS in Replit Secrets, then restart mneme."
            ),
        }
