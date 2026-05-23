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

    Iterates over namespace_keywords (or built-in defaults when None/empty) in
    order; returns the first namespace whose keywords appear in the combined
    tool_name+params blob.  Falls back to "default" when nothing matches.

    Phase 2 will add an LLM fallback for ambiguous cases without changing
    this signature.
    """
    keywords = namespace_keywords if namespace_keywords else _DEFAULT_NAMESPACE_KEYWORDS
    blob = (tool_name + " " + _safe_dumps(params)).lower()

    # Pass 1: tool-name prefix wins over keyword matching.
    # By convention every namespace's keyword list includes its tool prefix
    # (e.g. "lib_", "saaz_", "neon_") as an explicit marker.  When the tool
    # name starts with such a prefix, honour that as authoritative — it
    # prevents false matches when keyword lists share terms (e.g. "genre").
    if "_" in tool_name:
        prefix = tool_name.split("_", 1)[0] + "_"
        for ns, kws in keywords.items():
            if prefix in kws:
                return ns

    # Pass 2: substring scan over tool_name + serialised params (fallback).
    for ns, kws in keywords.items():
        if any(kw in blob for kw in kws):
            return ns

    return "default"


def _safe_dumps(params: dict[str, Any]) -> str:
    try:
        return json.dumps(params, default=str)
    except Exception:
        return repr(params)
