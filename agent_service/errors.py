from __future__ import annotations


class MnemeError(Exception):
    """Base class for all mneme errors."""


class UpstreamError(MnemeError):
    """A call to the upstream MCP server failed."""


class RoutingError(MnemeError):
    """Cannot determine db_namespace for a tool call."""


class MemoryWriteError(MnemeError):
    """Writing to a mneme memory table failed."""


class PoolNotReadyError(MnemeError):
    """The connection pool has not been initialized yet."""


class SessionError(MnemeError):
    """Session management operation failed."""


class ProvisionError(MnemeError):
    """Database provisioning at a cloud provider failed."""
