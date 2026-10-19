"""HTTP/SSE transports for MCP SDK 2.x using httpx2, without connection at import.

All HTTP responses are bounded before the SDK decodes JSON/SSE. Compression is
rejected to avoid post-decompression expansion. No redirect, proxy environment,
HTTP retry or endpoint-origin change is permitted. New TCP connections use
checked numeric addresses through PinnedHTTPTransport, keeping URL/Host/SNI.
This is not a network sandbox or complete SSRF protection.
"""
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from external_mcp.address_guard import check_destination
from external_mcp.limits import CONNECT_TIMEOUT_S, DEFAULT_TIMEOUT_S, ExternalError
from external_mcp.profiles import ProfileError, validate_url

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024
MAX_SSE_EVENT_BYTES = 2 * 1024 * 1024


def origin(url):
    parsed = urlsplit(str(url))
    return (parsed.scheme.lower(), parsed.hostname.lower().rstrip('.'),
            parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80))


class ResponseBudget:
    """Response/session byte counters. One budget per short-lived client session."""
    def __init__(self, response_limit=MAX_RESPONSE_BYTES, session_limit=MAX_SESSION_BYTES):
        self.response_limit, self.session_limit = response_limit, session_limit
        self.total = 0

    def consume(self, response_size, chunk_size):
        size = response_size + chunk_size
        self.total += chunk_size
        if size > self.response_limit or self.total > self.session_limit:
            raise ExternalError('http_message_limit')
        return size


def validate_response(status, headers):
    if 300 <= status < 400:
        raise ExternalError('http_redirect_blocked')
    encoding = headers.get('content-encoding', '').strip().lower()
    if encoding not in ('', 'identity'):
        raise ExternalError('http_compression_blocked')
    length = headers.get('content-length')
    if length is not None:
        try:
            size = int(length)
        except (TypeError, ValueError):
            raise ExternalError('http_length_invalid') from None
        if size < 0 or size > MAX_RESPONSE_BYTES:
            raise ExternalError('http_message_limit')


def make_http_client(runtime, *, blocked_ports=(), inner_transport=None,
                     resolver=None, own_addresses=None):
    """Owned httpx2 client; optional mock transport is strictly an internal test hook.

    Request bodies are produced by SDK; foreign endpoints and unsafe URL params
    are rejected before HTTP transport execution. The credentials stay in RAM.
    """
    import httpx2 as httpx

    validate_url(runtime.url, blocked_ports)
    expected = origin(runtime.url)
    budget = ResponseBudget()

    class BoundedStream(httpx.AsyncByteStream):
        def __init__(self, stream):
            self.stream = stream

        async def __aiter__(self):
            size = 0
            try:
                async for chunk in self.stream:
                    size = budget.consume(size, len(chunk))
                    yield chunk
            finally:
                await self.stream.aclose()

        async def aclose(self):
            await self.stream.aclose()

    class GuardTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            if inner_transport is not None:
                self.inner = inner_transport  # internal in-memory test hook only
            else:
                from external_mcp.pinned_transport import PinnedHTTPTransport
                self.inner = PinnedHTTPTransport(
                    runtime, expected[1], expected[2], blocked_ports=blocked_ports,
                    resolver=resolver, own_addresses=own_addresses,
                )

        async def handle_async_request(self, request):
            try:
                validate_url(str(request.url), blocked_ports,
                             allow_query=runtime.transport == 'sse')
                if origin(request.url) != expected:
                    raise ExternalError('http_endpoint_change_blocked')
                # Check at actual TCP creation in production. Pooled connections
                # remain bound to their already checked numeric destination.
                if inner_transport is not None:
                    _, host, port = origin(request.url)
                    await check_destination(host, port, blocked_ports=blocked_ports,
                                            resolver=resolver, own_addresses=own_addresses)
                request.headers['accept-encoding'] = 'identity'
                response = await self.inner.handle_async_request(request)
                try:
                    validate_response(response.status_code, response.headers)
                except BaseException:
                    await response.aclose()
                    raise
                return httpx.Response(
                    status_code=response.status_code, headers=response.headers,
                    stream=BoundedStream(response.stream), extensions=response.extensions,
                )
            except ExternalError:
                raise
            except ProfileError as exc:
                raise ExternalError(exc.code) from None  # field code only, never a value
            except Exception:
                raise ExternalError('http_transport_failed') from None

        async def aclose(self):
            await self.inner.aclose()

    return httpx.AsyncClient(
        transport=GuardTransport(), headers=dict(runtime.headers), trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(DEFAULT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


@asynccontextmanager
async def bounded_http_client(runtime, *, blocked_ports=()):
    """Yield SDK transport streams; caller owns operation timeout and cancellation.

    Legacy SSE endpoint notifications must stay on the configured origin. The
    SDK may reconnect discovery/event reads; this wrapper does not retry calls.
    A complete-session byte cap also bounds SSE events even if its parser has no
    dedicated max-event option. Large/long-lived sessions fail rather than grow.
    """
    if runtime.transport not in ('http', 'sse'):
        raise ExternalError('http_transport_type')
    async with make_http_client(runtime, blocked_ports=blocked_ports) as client:
        if runtime.transport == 'http':
            from mcp.client.streamable_http import streamable_http_client
            async with streamable_http_client(
                runtime.url, http_client=client, terminate_on_close=True,
                max_sse_event_size=MAX_SSE_EVENT_BYTES,
            ) as streams:
                yield streams
        else:
            from mcp.client.sse import sse_client

            def factory(headers=None, timeout=None, auth=None):
                # The SDK owns factory-created client context. Use a fresh guarded
                # client instead of double-entering the outer client above.
                # Do not accept an SDK-provided Auth that could start login flows.
                if auth is not None:
                    raise ExternalError('http_auth_flow_blocked')
                owned = make_http_client(runtime, blocked_ports=blocked_ports)
                if headers:
                    # SDK protocol headers are preserved, configured credentials win.
                    owned.headers.update(headers)
                    owned.headers.update(runtime.headers)
                return owned

            async with sse_client(
                runtime.url, headers=dict(runtime.headers), timeout=CONNECT_TIMEOUT_S,
                sse_read_timeout=DEFAULT_TIMEOUT_S, httpx_client_factory=factory,
            ) as streams:
                yield streams
