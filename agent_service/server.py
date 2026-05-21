"""mneme FastAPI + FastMCP server entrypoint.

Phase 0 scaffold: boots a FastAPI app exposing `/healthz` and a status endpoint.
The FastMCP proxy and middleware described in `docs/ARCHITECTURE.md` will be
wired in here during Phase 1 (see `START_HERE.md`).

This module is intentionally tolerant of missing configuration so the import
can boot in the Replit environment before secrets are provided.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI

app = FastAPI(title="mneme", version="0.0.1")


def _config_status() -> dict[str, Any]:
    return {
        "database_url": bool(os.environ.get("DATABASE_URL")),
        "upstream_db_mcp_url": bool(os.environ.get("UPSTREAM_DB_MCP_URL")),
        "anthropic_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "langsmith_api_key": bool(os.environ.get("LANGSMITH_API_KEY")),
    }


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "mneme", "version": app.version}


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "mneme",
        "version": app.version,
        "status": "scaffold",
        "message": (
            "mneme scaffold is running. Phase 1 implementation pending — see "
            "START_HERE.md and docs/ARCHITECTURE.md."
        ),
        "config": _config_status(),
        "endpoints": ["/healthz", "/"],
    }


def main() -> None:
    """Console-script entrypoint (see [project.scripts] in pyproject.toml)."""
    import uvicorn

    host = os.environ.get("MCP_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_SERVER_PORT", "5000"))
    uvicorn.run("agent_service.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
