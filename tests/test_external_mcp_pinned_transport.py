"""M2f: real core pool with fake TCP streams; no DNS, sockets or processes."""
import ssl

import anyio
import httpcore2
import httpx2 as httpx
import pytest

from external_mcp.limits import ExternalError
from external_mcp.pinned_transport import PinnedBackend, PinnedHTTPTransport
from external_mcp.runtime_config import RuntimeConfig


class Stream:
    def __init__(self):
        self.closed = False
        self.writes = []
        self.sni = []
        self.sent = False

    async def read(self, max_bytes, timeout=None):
        if self.sent:
            return b''
        self.sent = True
        return b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}'

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.sni.append((ssl_context, server_hostname))
        return self

    def get_extra_info(self, info):
        return None


class Network:
    def __init__(self):
        self.calls = []
        self.streams = []

    async def connect_tcp(self, **kwargs):
        self.calls.append(kwargs)
        stream = Stream()
        self.streams.append(stream)
        return stream


def resolving(*addresses):
    async def resolve(host, port):
        return list(addresses)
    return resolve


def test_core_uses_checked_ip_but_original_host_and_tls_sni():
    network = Network()
    resolutions = []
    async def resolve(host, port):
        resolutions.append((host, port))
        return ['93.184.216.34'] if len(resolutions) == 1 else ['169.254.169.254']
    tls = ssl.create_default_context()
    runtime = RuntimeConfig(transport='http', url='https://example.test/mcp', tls=tls)
    async def use():
        transport = PinnedHTTPTransport(runtime, 'example.test', 443,
                                        resolver=resolve, network_backend=network)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            response = await client.get(runtime.url, extensions={'sni_hostname': 'evil.test'})
            assert response.json() == {}
    anyio.run(use)
    assert resolutions == [('example.test', 443)]
    assert len(network.calls) == 1
    assert network.calls[0]['host'] == '93.184.216.34'
    stream = network.streams[0]
    assert stream.sni == [(tls, 'example.test')]
    assert b'Host: example.test\r\n' in b''.join(stream.writes)
    assert stream.closed


@pytest.mark.parametrize('addresses, code', [
    (['93.184.216.34', '169.254.169.254'], 'destination_blocked'),
    (['not-an-ip'], 'address_invalid'),
    ([], 'dns_failed'),
])
def test_unsafe_resolution_never_connects(addresses, code):
    network = Network()
    backend = PinnedBackend('example.test', 443, resolver=resolving(*addresses), inner=network)
    with pytest.raises(ExternalError) as raised:
        anyio.run(lambda: backend.connect_tcp('example.test', 443))
    assert raised.value.code == code
    assert network.calls == []


def test_new_connection_rechecks_dns_and_blocks_rebound_address():
    calls = []
    async def resolve(host, port):
        calls.append(host)
        return ['93.184.216.34'] if len(calls) == 1 else ['169.254.169.254']
    network = Network()
    backend = PinnedBackend('example.test', 443, resolver=resolve, inner=network)
    async def use():
        first = await backend.connect_tcp('example.test', 443)
        await first.aclose()
        with pytest.raises(ExternalError, match='destination_blocked'):
            await backend.connect_tcp('example.test', 443)
    anyio.run(use)
    assert len(network.calls) == 1
    assert len(calls) == 2


def test_origin_and_unix_socket_rejected_before_network():
    network = Network()
    backend = PinnedBackend('example.test', 443, inner=network)
    async def use():
        for host, port in [('other.test', 443), ('example.test', 8101)]:
            with pytest.raises(ExternalError, match='http_endpoint_change_blocked'):
                await backend.connect_tcp(host, port)
        with pytest.raises(ExternalError, match='http_unix_socket_blocked'):
            await backend.connect_unix_socket('private.sock')
    anyio.run(use)
    assert network.calls == []


def test_ip_literal_does_not_use_resolver():
    async def forbidden(host, port):
        raise AssertionError('resolver must not run')
    network = Network()
    backend = PinnedBackend('127.0.0.1', 8112, resolver=forbidden, inner=network)
    anyio.run(lambda: backend.connect_tcp('127.0.0.1', 8112))
    assert network.calls[0]['host'] == '127.0.0.1'


def test_dns_and_tcp_share_timeout_and_cancellation_propagates():
    network = Network()
    async def slow(host, port):
        await anyio.sleep_forever()
    backend = PinnedBackend('example.test', 443, resolver=slow, inner=network)
    async def timeout():
        with pytest.raises(ExternalError, match='http_connect_timeout'):
            await backend.connect_tcp('example.test', 443, timeout=0.01)
    anyio.run(timeout)
    assert network.calls == []
    async def cancel():
        with anyio.move_on_after(0.01) as scope:
            await backend.connect_tcp('example.test', 443)
        assert scope.cancel_called
    anyio.run(cancel)


@pytest.mark.parametrize('transport, target', [
    ('http', 'https://example.test/mcp'),
    ('sse', 'https://example.test/messages?session_id=abc'),
])
def test_make_http_client_uses_pinned_core_in_production_path(monkeypatch, transport, target):
    from external_mcp.http_transport import make_http_client
    network = Network()
    monkeypatch.setattr('external_mcp.pinned_transport.AnyIOBackend', lambda: network)
    calls = []
    async def resolve(host, port):
        calls.append((host, port))
        return ['93.184.216.34'] if len(calls) == 1 else ['169.254.169.254']
    runtime = RuntimeConfig(transport=transport, url='https://example.test/mcp',
                            tls=ssl.create_default_context())
    async def use():
        # No inner_transport injection: execute the actual production wiring.
        async with make_http_client(runtime, resolver=resolve) as client:
            response = await client.post(target, json={})
            assert response.json() == {}
    anyio.run(use)
    assert calls == [('example.test', 443)]
    assert network.calls[0]['host'] == '93.184.216.34'
    assert len(network.calls) == 1
    assert network.streams[0].closed
    assert b'accept-encoding: identity' in b''.join(network.streams[0].writes).lower()


def test_ipv6_tcp_refused_then_checked_ipv4_succeeds():
    class V4Network(Network):
        async def connect_tcp(self, **kwargs):
            if kwargs['host'] == '::1':
                self.calls.append(kwargs)
                raise httpcore2.ConnectError('refused')
            return await super().connect_tcp(**kwargs)
    network, resolutions = V4Network(), []
    async def resolver(host, port):
        resolutions.append(host)
        return ['::1', '127.0.0.1']
    backend = PinnedBackend('localhost', 8112, resolver=resolver, inner=network)
    anyio.run(lambda: backend.connect_tcp('localhost', 8112))
    assert resolutions == ['localhost']
    assert [call['host'] for call in network.calls] == ['::1', '127.0.0.1']
    assert network.calls[1]['timeout'] <= network.calls[0]['timeout']


@pytest.mark.parametrize('phase', ['tls', 'write'])
def test_failure_after_tcp_does_not_try_another_address(phase):
    class FailedStream(Stream):
        def __init__(self):
            super().__init__()
            self.write_attempted = anyio.Event()

        async def start_tls(self, **kwargs):
            if phase == 'tls':
                raise httpcore2.ConnectError('PRIVATE_TLS')
            return self

        async def write(self, buffer, timeout=None):
            self.writes.append(buffer)
            self.write_attempted.set()
            raise httpcore2.WriteError('PRIVATE_WRITE')

        async def read(self, max_bytes, timeout=None):
            # A broken socket must not manufacture a successful HTTP response.
            # Concurrent core reader/writer may surface either failure first.
            await self.write_attempted.wait()
            raise httpcore2.ReadError('PRIVATE_READ')
    class FailedNetwork(Network):
        async def connect_tcp(self, **kwargs):
            self.calls.append(kwargs)
            stream = FailedStream()
            self.streams.append(stream)
            return stream
    network = FailedNetwork()
    runtime = RuntimeConfig(transport='http', url='https://example.test/mcp',
                            tls=ssl.create_default_context())
    async def use():
        transport = PinnedHTTPTransport(runtime, 'example.test', 443,
                        resolver=resolving('93.184.216.34', '93.184.216.35'), network_backend=network)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            await client.post(runtime.url, json={})
    expected = (httpcore2.ConnectError,) if phase == 'tls' else (
        httpcore2.WriteError, httpcore2.ReadError)
    with pytest.raises(expected):
        anyio.run(use)
    assert len(network.calls) == 1
    if phase == 'write':
        assert network.streams[0].writes, 'write failure was never exercised'
    else:
        assert network.streams[0].writes == []


def test_timeout_budget_includes_dns_and_tcp():
    class HangingNetwork(Network):
        async def connect_tcp(self, **kwargs):
            self.calls.append(kwargs)
            await anyio.sleep_forever()
    network = HangingNetwork()
    async def resolve(host, port):
        await anyio.sleep(0.02)
        return ['93.184.216.34']
    backend = PinnedBackend('example.test', 443, resolver=resolve, inner=network)
    async def use():
        with pytest.raises(ExternalError, match='http_connect_timeout'):
            await backend.connect_tcp('example.test', 443, timeout=0.2)
    anyio.run(use)
    assert len(network.calls) == 1
    assert 0 < network.calls[0]['timeout'] < 0.2


def test_failed_tcp_tries_only_checked_ips_without_hostname():
    class FailedNetwork(Network):
        async def connect_tcp(self, **kwargs):
            self.calls.append(kwargs)
            raise httpcore2.ConnectError('PRIVATE')
    network = FailedNetwork()
    backend = PinnedBackend('example.test', 443,
                            resolver=resolving('93.184.216.34', '93.184.216.35'), inner=network)
    with pytest.raises(httpcore2.ConnectError):
        anyio.run(lambda: backend.connect_tcp('example.test', 443))
    assert [call['host'] for call in network.calls] == ['93.184.216.34', '93.184.216.35']
