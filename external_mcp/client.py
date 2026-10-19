"""Short-lived MCP client sessions for sklr: timeout, cancellation, no call retry.

Synchronous entry points are meant for TaskManager worker threads. Nothing here is
imported by the server yet and importing never connects. Upstream exception text is
never propagated. Call arguments are bounded and validated against the current
JSON Schema in an owned, time-bounded worker before dispatch.
"""
import hashlib
import json
import threading
from contextlib import asynccontextmanager

import anyio

from external_mcp.limits import (
    MAX_TOOLS, ExternalError, arguments_view, catalog_view, result_view, timeout_seconds,
)
from external_mcp.runtime_config import prepare_runtime
from external_mcp.schema_validation import validate_arguments

MAX_CATALOG_PAGES = 10
CANCEL_POLL_S = 0.05


def schema_digest(schema):
    encoded = json.dumps(schema, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _leaves(exc):
    inner = getattr(exc, 'exceptions', None)
    if inner is None:
        return [exc]
    return [leaf for item in inner for leaf in _leaves(item)]


@asynccontextmanager
async def _default_transport(runtime, blocked_ports):
    if runtime.transport == 'stdio':
        from external_mcp.stdio_transport import bounded_stdio_client
        async with bounded_stdio_client(runtime) as streams:
            yield streams
    else:
        from external_mcp.http_transport import bounded_http_client
        async with bounded_http_client(runtime, blocked_ports=blocked_ports) as streams:
            yield streams


def _default_session(streams, timeout):
    # Defaults reject sampling/elicitation/roots callbacks and claimed/input-required results.
    from mcp import ClientSession
    return ClientSession(streams[0], streams[1], read_timeout_seconds=timeout)


async def _catalog(session):
    from mcp import types
    tools, cursor, seen = [], None, set()
    for _ in range(MAX_CATALOG_PAGES):
        params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
        page = await (session.list_tools(params=params) if params else session.list_tools())
        tools.extend(page.tools)
        if len(tools) > MAX_TOOLS:
            raise ExternalError('tools_limit')
        cursor = getattr(page, 'next_cursor', None)
        if not cursor:
            records = catalog_view(tools)
            for record in records:
                record['schemaSha256'] = schema_digest(record['inputSchema'])
            return records
        if not isinstance(cursor, str) or cursor in seen:
            raise ExternalError('catalog_pages')
        seen.add(cursor)
    raise ExternalError('catalog_pages')


async def _guarded(operation, timeout, cancel_event, state):
    outcome = {}
    async with anyio.create_task_group() as group:
        async def watch():
            while not cancel_event.is_set():
                await anyio.sleep(CANCEL_POLL_S)
            outcome['cancelled'] = True
            group.cancel_scope.cancel()

        async def run():
            try:
                with anyio.fail_after(timeout):
                    outcome['value'] = await operation()
            except TimeoutError:
                outcome['timeout'] = True
            except Exception as exc:
                outcome['error'] = exc
            finally:
                group.cancel_scope.cancel()

        group.start_soon(watch)
        group.start_soon(run)
    return outcome


def _execute(profile, operation_factory, timeout, cancel_event, environ, blocked_ports,
             transport, session_factory, uncertain_phase):
    timeout = timeout_seconds(timeout)
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    state = {'phase': 'connecting'}
    runtime = prepare_runtime(profile, environ=environ, blocked_ports=blocked_ports)

    async def operation():
        async with (transport or _default_transport)(runtime, blocked_ports) as streams:
            async with (session_factory or _default_session)(streams, timeout) as session:
                await session.initialize()
                return await operation_factory(session, state)

    try:
        outcome = anyio.run(_guarded, operation, timeout, cancel_event, state)
    except BaseException:
        raise ExternalError('client_failed') from None
    finally:
        runtime.clear()
    uncertain = uncertain_phase and state['phase'] == 'calling'
    if outcome.get('cancelled') or cancel_event.is_set() and 'value' not in outcome:
        raise ExternalError('call_cancelled_uncertain' if uncertain else 'cancelled')
    if outcome.get('timeout'):
        raise ExternalError('call_timeout_uncertain' if uncertain else 'timeout')
    if 'error' in outcome:
        for leaf in _leaves(outcome['error']):
            if isinstance(leaf, ExternalError) and not uncertain:
                raise ExternalError(leaf.code) from None
        code = next((leaf.code for leaf in _leaves(outcome['error'])
                     if isinstance(leaf, ExternalError)), 'client_failed')
        error = ExternalError('call_failed_uncertain' if uncertain else code)
        error.cause_code = code
        raise error from None
    if 'value' not in outcome:
        raise ExternalError('client_failed')
    return outcome['value']


def fetch_catalog(profile, *, timeout=60, cancel_event=None, environ=None, blocked_ports=(),
                  transport=None, session_factory=None):
    """Complete paginated catalog; oversize/invalid data is an error, never truncated."""
    async def operation(session, state):
        return {'tools': await _catalog(session)}
    return _execute(profile, operation, timeout, cancel_event, environ, blocked_ports,
                    transport, session_factory, False)


def call_tool(profile, tool_name, arguments, *, expected_schema_sha256=None, timeout=60,
              cancel_event=None, environ=None, blocked_ports=(), transport=None,
              session_factory=None, before_dispatch=None):
    """One tools/call attempt; internal synchronous guard after schema validation.

    before_dispatch(name, schema_sha256) returns None or raises ExternalError;
    never waits for approval/network and is not accepted from model arguments.
    Failures after dispatch are explicitly outcome-uncertain.
    """
    if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 256:
        raise ExternalError('tool_name')
    arguments = arguments_view(arguments)
    if before_dispatch is not None and not callable(before_dispatch):
        raise ExternalError('dispatch_guard')
    if expected_schema_sha256 is not None and not (
            isinstance(expected_schema_sha256, str) and len(expected_schema_sha256) == 64):
        raise ExternalError('schema_digest')
    limit = timeout_seconds(timeout)

    async def operation(session, state):
        catalog = await _catalog(session)
        tool = next((item for item in catalog if item['name'] == tool_name), None)
        if tool is None:
            raise ExternalError('tool_not_found')
        if expected_schema_sha256 is not None and tool['schemaSha256'] != expected_schema_sha256:
            raise ExternalError('schema_changed')
        await validate_arguments(tool['inputSchema'], arguments)
        if cancel_event is not None and cancel_event.is_set():
            raise ExternalError('cancelled')
        if before_dispatch is not None:
            if before_dispatch(tool_name, tool['schemaSha256']) is not None:
                raise ExternalError('dispatch_guard')
        if cancel_event is not None and cancel_event.is_set():
            raise ExternalError('cancelled')
        state['phase'] = 'calling'
        result = await session.call_tool(tool_name, arguments, read_timeout_seconds=limit)
        return {'result': result_view(result), 'schemaSha256': tool['schemaSha256']}
    return _execute(profile, operation, limit, cancel_event, environ, blocked_ports,
                    transport, session_factory, True)
