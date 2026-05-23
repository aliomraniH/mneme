from __future__ import annotations

import json
from typing import Any

# Built-in fallback keywords — only generic infrastructure patterns.
# These are intentionally minimal: no project- or dataset-specific names here.
#
# Real keywords come from two sources (higher priority first):
#   1. NAMESPACE_ROUTING_KEYWORDS env var (JSON mapping set in Replit Secrets)
#   2. registered_database.routing_keywords loaded from Helium at startup
#   3. These defaults (last resort)
#
# "embedding_index" avoids bare "vector" — pgvector uses that term too.
_DEFAULT_NAMESPACE_KEYWORDS: dict[str, list[str]] = {
    "pinecone_main": ["pinecone", "embedding_index"],
    "pg_main": ["postgres", "pg_", "sql"],
}


def route_to_namespace(
    tool_name: str,
    params: dict[str, Any],
    *,
    namespace_keywords: dict[str, list[str]] | None = None,
) -> str:
    """Return the db_namespace for a tool call.

    Routing uses a two-pass strategy so that tool-name prefixes always win
    over bare keyword matching, preventing false matches when multiple
    namespaces share common vocabulary (e.g. "genre" appearing in both a
    music DB and a book DB).

    Pass 1 — prefix wins (Task 1 fix):
        By convention every namespace's keyword list contains its tool-name
        prefix (e.g. "lib_", "saaz_", "neon_").  When the tool name starts
        with such a prefix we return that namespace immediately without
        scanning the rest of the keyword lists.  This is the primary, most
        specific signal and is evaluated first.

    Pass 2 — substring scan (fallback):
        If no prefix matched, scan the combined tool_name + serialised params
        blob for any keyword.  First match wins (iteration order of the dict).

    Falls back to "default" when nothing matches.

    Phase 2 will add an LLM fallback for ambiguous cases without changing
    this signature.

    Bug fixed (Task 1): previously the code fell through to Pass 2 even when
    a prefix matched, causing a longer keyword list in a later namespace to
    override the correct prefix match.  Now Pass 1 returns immediately.
    """
    keywords = namespace_keywords if namespace_keywords else _DEFAULT_NAMESPACE_KEYWORDS
    blob = (tool_name + " " + _safe_dumps(params)).lower()

    # Pass 1: tool-name prefix wins — authoritative, evaluated before any
    # substring scan.  e.g. "lib_query" → prefix "lib_" → namespace "lib".
    if "_" in tool_name:
        prefix = tool_name.split("_", 1)[0] + "_"
        for ns, kws in keywords.items():
            if prefix in kws:
                return ns  # return immediately; do NOT fall through to Pass 2

    # Pass 2: substring scan over tool_name + serialised params (fallback).
    # Used when no namespace claims the prefix (e.g. generic "list_tables").
    for ns, kws in keywords.items():
        if any(kw in blob for kw in kws):
            return ns

    return "default"


def _safe_dumps(params: dict[str, Any]) -> str:
    try:
        return json.dumps(params, default=str)
    except Exception:
        return repr(params)
