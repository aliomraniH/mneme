"""mneme FastAPI + FastMCP server entrypoint.

All five Phase-1 deliverables are wired here:
  (a) proxy passthrough  — mneme.mount(upstream_proxy) in lifespan
  (b) memory store       — AsyncConnectionPool + 0002 migration
  (c) audit middleware   — AuditMiddleware(pool_factory=_get_pool)
  (d) namespace router   — used inside AuditMiddleware
  (e) observability      — structlog JSON, /healthz with pool check

Build pattern:
  FastMCP server and middleware are constructed at *module scope* so the
  ASGI app can be mounted on FastAPI before the lifespan runs.
  The pool and upstream Client are *deferred* to the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from psycopg_pool import AsyncConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from agent_service.config import Settings, get_settings
from agent_service.memory.store import apply_pending_migrations, create_pool
from agent_service.middleware.audit import AuditMiddleware
from agent_service.middleware.session import (
    SessionMiddleware,
    idle_session_reaper,
    mark_sessions_shutdown,
)
from agent_service.middleware.timeout import TimeoutMiddleware
from agent_service.provision import register_provision_tools
from agent_service.proxy import build_mneme_server, mount_upstream

# ---------------------------------------------------------------------------
# Module-level: pool reference (set once in lifespan, never mutated after)
# ---------------------------------------------------------------------------
_pool: AsyncConnectionPool | None = None  # type: ignore[type-arg]


def _get_pool() -> AsyncConnectionPool:  # type: ignore[type-arg]
    if _pool is None:
        from agent_service.errors import PoolNotReadyError

        raise PoolNotReadyError("Connection pool not initialized yet")
    return _pool


# Namespace routing keyword overrides (populated from settings in lifespan).
# None means AuditMiddleware falls back to routing.py's built-in defaults.
_namespace_keywords: dict[str, list[str]] | None = None


def _get_namespace_keywords() -> dict[str, list[str]] | None:
    return _namespace_keywords


# ---------------------------------------------------------------------------
# Module-level: FastMCP server (proxy mounted in lifespan)
# ---------------------------------------------------------------------------
mneme = build_mneme_server()
mneme.add_middleware(TimeoutMiddleware(timeout_seconds=30.0))
mneme.add_middleware(SessionMiddleware(pool_factory=_get_pool))
mneme.add_middleware(
    AuditMiddleware(
        pool_factory=_get_pool,
        namespace_keywords_factory=_get_namespace_keywords,
    )
)

# Build the ASGI transport once at module scope so the lifespan object is
# stable.  path="/" places the MCP route at "/" within the sub-app, meaning
# it is reachable at the parent mount-point (/mcp) after the trailing-slash
# redirect (/mcp → /mcp/).
_mcp_http_app = mneme.http_app(path="/")


# ---------------------------------------------------------------------------
# Structlog configuration
# ---------------------------------------------------------------------------
def _configure_logging(log_level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _pool, _namespace_keywords

    settings: Settings = get_settings()
    _configure_logging(settings.log_level)

    log = structlog.get_logger(__name__)
    log.info("startup", **settings.as_log_safe())

    # Populate namespace routing keyword overrides from config (if any).
    # Empty dict means "use built-in defaults in routing.py".
    _namespace_keywords = settings.namespace_routing_keywords or None

    # Create pool and apply the 0002 migration (0001 already applied on Helium)
    _pool = await create_pool(settings.database_url_str())
    app.state.pool = _pool
    await apply_pending_migrations(_pool)

    # Mount one proxy per configured upstream database MCP server
    mount_upstream(mneme, settings)

    # Register native agent-owned tools (provision_database, list_database_regions)
    register_provision_tools(mneme, settings)

    # Start idle session reaper background task
    shutdown_event = asyncio.Event()
    reaper_task = asyncio.create_task(
        idle_session_reaper(
            pool_factory=_get_pool,
            idle_seconds=settings.session_idle_timeout_seconds,
            check_interval_seconds=settings.session_idle_check_interval_seconds,
            shutdown_event=shutdown_event,
        )
    )

    log.info("ready")

    # Run the FastMCP HTTP transport lifespan so the StreamableHTTP session
    # manager's anyio task group is initialised before the first request.
    async with _mcp_http_app.lifespan(app):
        try:
            yield
        finally:
            log.info("shutdown_started")
            shutdown_event.set()

            # Wait up to graceful_shutdown_timeout_seconds for in-flight calls
            try:
                await asyncio.wait_for(
                    reaper_task,
                    timeout=settings.graceful_shutdown_timeout_seconds,
                )
            except (TimeoutError, asyncio.CancelledError):
                reaper_task.cancel()

            # Mark all open sessions as shutdown
            if _pool is not None:
                await mark_sessions_shutdown(_pool)
                await _pool.close()
            _pool = None
            log.info("shutdown_complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="mneme",
    version="0.1.0",
    lifespan=_lifespan,
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)


# Mount the FastMCP ASGI app at /mcp (module scope — before lifespan runs).
# _mcp_http_app was built above with path="/" so its internal route is "/" and
# the effective external path is /mcp (after Starlette's trailing-slash redirect
# /mcp → /mcp/).
app.mount("/mcp", _mcp_http_app)


# ---------------------------------------------------------------------------
# FastAPI sidecar routes
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    pool: AsyncConnectionPool | None = getattr(request.app.state, "pool", None)  # type: ignore[type-arg]
    db_ok = False
    if pool is not None:
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
            db_ok = True
        except Exception:
            pass

    return {
        "status": "ok" if db_ok else "degraded",
        "service": "mneme",
        "version": app.version,
        "db": "ok" if db_ok else "error",
    }


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "mneme",
        "version": app.version,
        "phase": "1",
        "endpoints": ["/healthz", "/", "/mcp"],
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "agent_service.server:app",
        host=settings.mcp_server_host,
        port=settings.mcp_server_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
