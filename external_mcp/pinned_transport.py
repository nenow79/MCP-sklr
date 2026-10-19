"""Connection-level destination pinning for httpx2/httpcore2 2.13.1.

URL/Host/TLS SNI remain the original hostname. Only TCP gets a checked numeric
address. AnyIOBackend is an explicit INTERNAL dependency of httpcore2: upgrades
require compatibility tests. There is no unguarded fallback or request retry.
This does not cover every SSRF vector (e.g. NAT64 or unknown local interfaces).
"""
import ipaddress

import anyio
import httpx2 as httpx
import httpcore2
from httpcore2._backends.anyio import AnyIOBackend

from external_mcp.address_guard import check_destination
from external_mcp.limits import CONNECT_TIMEOUT_S, ExternalError


def normalized_host(host):
    return host.lower().rstrip('.')


class PinnedBackend:
    """One backend per configured origin; test injection never exposed as API."""

    def __init__(self, host, port, *, blocked_ports=(), resolver=None,
                 own_addresses=None, inner=None):
        self.host, self.port = normalized_host(host), port
        self.blocked_ports = tuple(blocked_ports)
        self.resolver, self.own_addresses = resolver, own_addresses
        self.inner = inner if inner is not None else AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        if normalized_host(host) != self.host or port != self.port:
            raise ExternalError('http_endpoint_change_blocked')
        if local_address is not None:
            raise ExternalError('http_local_address_blocked')
        # One deadline includes DNS, policy and all TCP candidates. Only failed
        # TCP establishment can try another checked IP; never TLS/HTTP.
        limit = CONNECT_TIMEOUT_S if timeout is None else min(timeout, CONNECT_TIMEOUT_S)
        try:
            with anyio.fail_after(limit) as scope:
                addresses = await check_destination(
                    host, port, blocked_ports=self.blocked_ports,
                    resolver=self.resolver, own_addresses=self.own_addresses,
                )
                # Normalize the entire checked set before opening any socket.
                try:
                    candidates = list(dict.fromkeys(str(ipaddress.ip_address(value))
                                                    for value in addresses))
                except ValueError:
                    raise ExternalError('address_invalid') from None
                for index, address in enumerate(candidates):
                    remaining = max(0.0, scope.deadline - anyio.current_time())
                    if remaining <= 0:
                        raise ExternalError('http_connect_timeout')
                    try:
                        return await self.inner.connect_tcp(
                            host=address, port=port, timeout=remaining,
                            local_address=None, socket_options=socket_options,
                        )
                    except (httpcore2.ConnectError, httpcore2.ConnectTimeout):
                        if index == len(candidates) - 1:
                            raise
                raise ExternalError('http_transport_failed')
        except TimeoutError:
            raise ExternalError('http_connect_timeout') from None

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise ExternalError('http_unix_socket_blocked')

    async def sleep(self, seconds):
        await anyio.sleep(seconds)


class CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class PinnedHTTPTransport(httpx.AsyncBaseTransport):
    """Owned core pool using its network_backend parameter, not HTTPX's _pool.

    Core exceptions intentionally propagate to the outer safe-code boundary.
    No exception text is translated into user-visible messages here.
    """

    def __init__(self, runtime, host, port, *, blocked_ports=(), resolver=None,
                 own_addresses=None, network_backend=None):
        backend = PinnedBackend(
            host, port, blocked_ports=blocked_ports, resolver=resolver,
            own_addresses=own_addresses, inner=network_backend,
        )
        self.pool = httpcore2.AsyncConnectionPool(
            ssl_context=runtime.tls, network_backend=backend,
            max_connections=10, max_keepalive_connections=10,
            keepalive_expiry=5.0, http1=True, http2=False, retries=0,
        )

    async def handle_async_request(self, request):
        # TLS must use the origin, never a caller-provided SNI override.
        extensions = dict(request.extensions)
        extensions.pop('sni_hostname', None)
        response = await self.pool.handle_async_request(httpcore2.Request(
            method=request.method,
            url=httpcore2.URL(
                scheme=request.url.raw_scheme, host=request.url.raw_host,
                port=request.url.port, target=request.url.raw_path,
            ),
            headers=request.headers.raw, content=request.stream,
            extensions=extensions,
        ))
        return httpx.Response(
            status_code=response.status, headers=response.headers,
            stream=CoreResponseStream(response.stream), extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()
