"""Entry-point for the library-demo database instance.

This module is a thin configuration shim over db_mcp.server.
All logic lives in db_mcp/server.py — nothing is hardcoded here.

The three DB_MCP_* env vars below are set via os.environ.setdefault so
Replit Secrets / userenv.shared always win.

  DB_MCP_TOOL_PREFIX        = lib
  DB_MCP_NAME               = library-demo
  DB_MCP_DATABASE_URL_ENV   = DATABASE_URL_NEON_PURPLE_KITE

To deploy another database, copy this file, change the three defaults,
and create a new workflow pointing at the copy.
"""

from __future__ import annotations

import os

os.environ.setdefault("DB_MCP_TOOL_PREFIX", "lib")
os.environ.setdefault("DB_MCP_NAME", "library-demo")
os.environ.setdefault("DB_MCP_DATABASE_URL_ENV", "DATABASE_URL_NEON_PURPLE_KITE")

from db_mcp.server import app  # re-export for uvicorn

__all__ = ["app"]
