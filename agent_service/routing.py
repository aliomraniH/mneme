from __future__ import annotations

import json
from typing import Any

# Built-in defaults — used when no namespace_routing_keywords config is provided.
# Priority is determined by dict insertion order (first match wins).
# Override via MNEME_NAMESPACE_ROUTING_KEYWORDS env var (JSON mapping).
#
# "embedding_index" is used instead of bare "vector" — pgvector uses that term too.
_DEFAULT_NAMESPACE_KEYWORDS: dict[str, list[str]] = {
    "saaz_demo": ["artist", "song", "persian", "jazz", "saaz", "genre"],
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

    for ns, kws in keywords.items():
        if any(kw in blob for kw in kws):
            return ns
    return "default"


def _safe_dumps(params: dict[str, Any]) -> str:
    try:
        return json.dumps(params, default=str)
    except Exception:
        return repr(params)
