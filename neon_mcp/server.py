"""Entry-point for the neon-purple-kite database instance.

This module is a thin configuration shim over db_mcp.server.
All logic lives in db_mcp/server.py — nothing is hardcoded here.

The three DB_MCP_* env vars below are set via .replit [userenv.shared] so
they apply to the process that runs this server.  You can override them in
Replit Secrets or by prefixing the uvicorn command.

  DB_MCP_TOOL_PREFIX        = neon
  DB_MCP_NAME               = neon-purple-kite
  DB_MCP_DATABASE_URL_ENV   = DATABASE_URL_NEON_PURPLE_KITE

To deploy a second database on a different port, create a new entry-point
file that sets its own defaults and re-exports db_mcp.server.app.
"""

from __future__ import annotations

import os

# Set instance defaults before importing db_mcp (reads env at import time).
# os.environ.setdefault does NOT overwrite values already set in the process,
# so Replit Secrets / userenv.shared always win.
os.environ.setdefault("DB_MCP_TOOL_PREFIX", "neon")
os.environ.setdefault("DB_MCP_NAME", "neon-purple-kite")
os.environ.setdefault("DB_MCP_DATABASE_URL_ENV", "DATABASE_URL_NEON_PURPLE_KITE")

from db_mcp.server import app  # noqa: E402  # re-export for uvicorn

__all__ = ["app"]
