"""Embedding client with provider auto-detection and graceful degradation.

mneme's ranking is two-tier:

  * If an embedding provider is configured (OpenAI or Voyage), warm_up /
    thread_refresh / log_context_summary use semantic cosine similarity over
    pgvector.
  * If no provider key is set, ``embed()`` returns None and callers fall back
    to rule-based ranking (frequency + recency). The whole system still works;
    it just loses semantic relevance.

Note on Anthropic: Anthropic does not expose an embeddings endpoint. The
``vector(1536)`` schema and ``text-embedding-3-small`` default target OpenAI
(1536-dim native). Voyage (Anthropic's recommended partner) is also supported
but most models are not 1536-dim — set EMBEDDING_DIMENSIONS to match if you
switch providers, and reindex.
"""

from __future__ import annotations

from collections import OrderedDict

import httpx
import structlog

from agent_service.config import Settings

log = structlog.get_logger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/embeddings"
_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class EmbeddingClient:
    """Async embedding client. Never raises — returns None on any failure.

    A small in-memory LRU cache avoids re-embedding identical text within a
    process lifetime (warm_up and thread_refresh often re-embed the same goal).
    """

    def __init__(self, settings: Settings, cache_size: int = 512) -> None:
        self._settings = settings
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_size = cache_size

        # Provider precedence: OpenAI (native 1536) → Voyage → none.
        self._provider: str | None
        self._api_key: str | None
        if settings.openai_api_key is not None:
            self._provider = "openai"
            self._api_key = settings.openai_api_key.get_secret_value()
        elif settings.voyage_api_key is not None:
            self._provider = "voyage"
            self._api_key = settings.voyage_api_key.get_secret_value()
        else:
            self._provider = None
            self._api_key = None

    @property
    def enabled(self) -> bool:
        """True when a provider is configured and semantic ranking is available."""
        return self._provider is not None

    @property
    def provider(self) -> str:
        return self._provider or "none"

    async def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector for ``text``, or None if unavailable.

        Failures (no provider, network error, bad response) all return None so
        callers degrade to rule-based ranking instead of crashing.
        """
        if not self.enabled or not text.strip():
            return None

        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        try:
            vector = await self._call_provider(text)
        except Exception as exc:
            log.warning("embedding_failed", provider=self.provider, error=str(exc))
            return None

        if vector is not None:
            self._cache[text] = vector
            self._cache.move_to_end(text)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return vector

    async def _call_provider(self, text: str) -> list[float] | None:
        if self._provider == "openai":
            url, model = _OPENAI_URL, self._settings.embedding_model
        elif self._provider == "voyage":
            url, model = _VOYAGE_URL, self._settings.embedding_model
        else:
            return None

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"input": text, "model": model},
            )
            resp.raise_for_status()
            data = resp.json()

        embedding: list[float] = data["data"][0]["embedding"]
        expected = self._settings.embedding_dimensions
        if len(embedding) != expected:
            log.warning(
                "embedding_dimension_mismatch",
                provider=self.provider,
                got=len(embedding),
                expected=expected,
            )
            return None
        return embedding
