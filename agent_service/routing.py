from __future__ import annotations

import json
from typing import Any

# Keywords that identify the saaz Persian-songwriter demo dataset.
_SAAZ_KEYWORDS: frozenset[str] = frozenset({"artist", "song", "persian", "jazz", "saaz", "genre"})

# Keywords that route to a dedicated Pinecone namespace.
# Deliberately excludes "vector" alone — pgvector uses that word too.
_PINECONE_KEYWORDS: frozenset[str] = frozenset({"pinecone", "embedding_index"})

# Keywords that route to the main Postgres namespace.
_PG_KEYWORDS: frozenset[str] = frozenset({"postgres", "pg_", "sql"})


def route_to_namespace(tool_name: str, params: dict[str, Any]) -> str:
    """Keyword-based namespace routing for Phase 1.

    Priority: saaz_demo > pinecone_main > pg_main > default.
    Phase 2 will swap the body for a small LLM router on ambiguous cases
    without touching this signature.
    """
    blob = (tool_name + " " + _safe_dumps(params)).lower()

    if any(kw in blob for kw in _SAAZ_KEYWORDS):
        return "saaz_demo"
    if any(kw in blob for kw in _PINECONE_KEYWORDS):
        return "pinecone_main"
    if any(kw in blob for kw in _PG_KEYWORDS):
        return "pg_main"
    return "default"


def _safe_dumps(params: dict[str, Any]) -> str:
    try:
        return json.dumps(params, default=str)
    except Exception:
        return repr(params)
