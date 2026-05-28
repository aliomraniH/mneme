from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

import mcp.types as mt
import structlog
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext, Middleware
from fastmcp.tools.base import ToolResult
from psycopg_pool import AsyncConnectionPool

from agent_service.advisors.cache_stale import CacheStaleAdvisor
from agent_service.advisors.schema_drift import SchemaDriftAdvisor
from agent_service.models import Advisory
from agent_service.routing import route_to_namespace

log = structlog.get_logger(__name__)

# Native mneme tools — skip advisory injection for these.
_MNEME_NATIVE_TOOLS: frozenset[str] = frozenset({
    "register_database",
    "list_registered_databases",
    "get_database_info",
    "update_database_config",
    "deregister_database",
    "provision_database",
    "list_database_regions",
    "get_query_history",
    "get_schema_summary",
    "refresh_schema",
    "get_advisories",
})


class AdvisoryMiddleware(Middleware):
    """Runs lightweight advisors after each upstream tool call.

    Injects a non-empty ``advisories`` list into the tool response meta when
    signals are detected (schema drift, stale cache). Native mneme tools and
    any failures are silently skipped — advisory errors must never block a
    tool call.

    Only SchemaDriftAdvisor and CacheStaleAdvisor run here (they are O(1)
    index reads). ConflictAdvisor is reserved for on-demand get_advisories
    calls because it does a heavier GROUP BY aggregate.
    """

    def __init__(
        self,
        pool_factory: Callable[[], AsyncConnectionPool],  # type: ignore[type-arg]
        namespace_keywords_factory: (
            Callable[[], dict[str, list[str]] | None] | None
        ) = None,
    ) -> None:
        self._pool_factory = pool_factory
        self._namespace_keywords_factory = namespace_keywords_factory
        self._schema_drift = SchemaDriftAdvisor()
        self._cache_stale = CacheStaleAdvisor()

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)

        tool_name: str = context.message.name
        if tool_name in _MNEME_NATIVE_TOOLS:
            return result

        with suppress(Exception):
            advisories = await self._collect_advisories(
                tool_name, context.message.arguments or {}
            )
            if advisories:
                existing_meta: dict[str, Any] = dict(result.meta or {})
                existing_meta["advisories"] = [a.model_dump() for a in advisories]
                result = result.model_copy(update={"meta": existing_meta})
                log.info(
                    "advisories_injected",
                    tool_name=tool_name,
                    count=len(advisories),
                    kinds=[a.kind for a in advisories],
                )

        return result

    async def _collect_advisories(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> list[Advisory]:
        kws = (
            self._namespace_keywords_factory()
            if self._namespace_keywords_factory is not None
            else None
        )
        db_namespace = route_to_namespace(tool_name, params, namespace_keywords=kws)
        pool = self._pool_factory()

        advisories: list[Advisory] = []
        for advisor in (self._schema_drift, self._cache_stale):
            with suppress(Exception):
                advisories.extend(await advisor.advise(pool, db_namespace))
        return advisories
