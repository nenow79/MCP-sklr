"""USER-run M2f acceptance: real local sockets/AnyIO/core, no external network.

Controlled resolver is injected, not a real authoritative DNS server. It changes
its answer on subsequent connection attempts. The OS resolver is monitored and
forbidden to resolve the original hostname. Tests HTTP wire requests including
an SSE-style query, not the full SSE protocol or TLS certificate verification.
"""
import ipaddress
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest
from httpcore2._backends.anyio import AnyIOBackend

from external_mcp.http_transport import make_http_client
from external_mcp.limits import ExternalError
from external_mcp.runtime_config import RuntimeConfig

HOST = 'rebinding.example.test'


@pytest.fixture
def endpoint():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            requests.append((self.path, self.headers.get('Host')))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            # Each request forces a new TCP connection and a fresh policy check.
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), 'local test server did not stop'


@pytest.fixture
def observe_network(monkeypatch):
    tcp_hosts, os_hosts = [], []
    original_tcp = AnyIOBackend.connect_tcp
    original_dns = socket.getaddrinfo
    async def connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        tcp_hosts.append(host)
        # The REAL backend continues below; no fake socket or stream.
        assert ipaddress.ip_address(host), 'TCP must receive a numeric IP'
        return await original_tcp(self, host, port, timeout=timeout,
                                  local_address=local_address, socket_options=socket_options)
    def resolve(host, *args, **kwargs):
        value = host.decode('ascii') if isinstance(host, bytes) else host
        os_hosts.append(value)
        if isinstance(value, str) and value.lower().rstrip('.') == HOST:
            raise AssertionError('unsafe second resolution of original hostname')
        return original_dns(host, *args, **kwargs)
    monkeypatch.setattr(AnyIOBackend, 'connect_tcp', connect)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    return tcp_hosts, os_hosts


@pytest.mark.parametrize('transport, path', [
    ('http', '/mcp'), ('sse', '/messages?session_id=acceptance'),
])
def test_checked_ip_real_connection_and_rebound_next_connection_blocked(
        endpoint, observe_network, transport, path):
    port, requests = endpoint
    tcp_hosts, os_hosts = observe_network
    resolutions = []
    async def resolver(host, requested_port):
        resolutions.append((host, requested_port))
        return ['127.0.0.1'] if len(resolutions) == 1 else ['169.254.169.254']
    runtime = RuntimeConfig(transport=transport, url=f'http://{HOST}:{port}/mcp')
    async def use():
        async with make_http_client(runtime, resolver=resolver) as client:
            first = await client.get(f'http://{HOST}:{port}{path}')
            assert first.json() == {'ok': True}
            with pytest.raises(ExternalError) as raised:
                await client.get(f'http://{HOST}:{port}{path}')
            assert raised.value.code == 'destination_blocked'
    anyio.run(use)
    assert resolutions == [(HOST, port), (HOST, port)]
    assert tcp_hosts == ['127.0.0.1']
    assert HOST not in os_hosts
    assert requests == [(path, f'{HOST}:{port}')]


def test_one_unsafe_address_blocks_all_real_connections(endpoint, observe_network):
    port, requests = endpoint
    tcp_hosts, os_hosts = observe_network
    async def resolver(host, requested_port):
        return ['127.0.0.1', '169.254.169.254']
    runtime = RuntimeConfig(transport='http', url=f'http://{HOST}:{port}/mcp')
    async def use():
        async with make_http_client(runtime, resolver=resolver) as client:
            with pytest.raises(ExternalError) as raised:
                await client.get(runtime.url)
            assert raised.value.code == 'destination_blocked'
    anyio.run(use)
    assert tcp_hosts == [] and requests == []
    assert HOST not in os_hosts
