from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    tool_name: str
    params: dict[str, Any]
    db_namespace: str
    thread_id: str | None = None
    session_id: str | None = None


class Episode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    db_namespace: str
    thread_id: str | None = None
    tool_name: str
    user_query: str | None = None
    tool_params: dict[str, Any]
    result_summary: dict[str, Any] | None = None
    row_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    source: Literal["ok", "error"] = "ok"
    audit_id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Caller metadata (from 0002_sessions.sql)
    session_id: str | None = None
    client_name: str | None = None
    client_version: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    truncated: bool = False


class Advisory(BaseModel):
    """Placeholder for Phase 2. Defined now so middleware signatures are stable."""

    kind: Literal["cache_stale", "potential_conflict", "schema_drift", "middleware_error"]
    db_namespace: str
    message: str
    confidence: float
    requires_approval: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class McpSession(BaseModel):
    session_id: str
    client_name: str | None = None
    client_version: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    end_reason: Literal["explicit", "idle_timeout", "shutdown"] | None = None
    total_calls: int = 0
    total_errors: int = 0


@dataclass(slots=True)
class Event:
    """Forward-compat envelope. At Phase 3 this swaps to Redis Streams / NATS."""

    type: Literal["tool_call", "tool_result", "memory_write", "advisory"]
    db_namespace: str
    payload: dict[str, Any]
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


def truncate_result_summary(result: Any, max_bytes: int = 4096) -> tuple[dict[str, Any], bool]:
    """Serialize result to a JSON-safe summary, capping at max_bytes.

    Returns (summary_dict, was_truncated).
    The caller still receives the full result; only the audit row is capped.
    """
    try:
        serialized = json.dumps(result, default=str)
    except Exception:
        serialized = repr(result)

    if len(serialized.encode()) <= max_bytes:
        try:
            parsed = json.loads(serialized)
            if isinstance(parsed, dict):
                return parsed, False
            return {"value": parsed}, False
        except Exception:
            return {"raw": serialized}, False

    truncated_str = serialized.encode()[:max_bytes].decode("utf-8", errors="replace")
    return {"truncated_payload": truncated_str}, True
