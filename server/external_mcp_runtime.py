"""Lazy production composition of persistent stdio; no import-time resources."""
from contextlib import asynccontextmanager

from external_mcp.persistent_supervisor import PersistentSupervisor
from external_mcp.stdio_transport import bounded_stdio_client
from server.external_mcp_tools import ExternalMCPTools

# A call can drain for its remaining 300 s, followed by cleanup and thread exit.
PERSISTENT_SHUTDOWN_BUDGET_S = 315


@asynccontextmanager
async def _stdio_transport(runtime, blocked_ports):
    async with bounded_stdio_client(runtime) as streams:
        yield streams


def _session(streams, timeout):
    from mcp import ClientSession
    return ClientSession(streams[0], streams[1], read_timeout_seconds=timeout)


def create_external_mcp_tools(registry, manager, *, panel_available):
    """Build the shared adapter without starting threads, processes or discovery."""
    supervisor = PersistentSupervisor(transport=_stdio_transport, session_factory=_session)
    return ExternalMCPTools(registry, manager, panel_available=panel_available,
                            persistent_supervisor=supervisor)
