"""Destination policy with injected resolvers; no real DNS and no network."""
import ssl

import anyio
import httpx2 as httpx
import pytest

from external_mcp import address_guard
from external_mcp.address_guard import check_destination, classify
from external_mcp.http_transport import make_http_client
from external_mcp.limits import ExternalError
from external_mcp.runtime_config import RuntimeConfig

BLOCKED = (8101, 8102)


def resolving(*addresses):
    async def resolver(host, port):
        return list(addresses)
    return resolver


def check(host, port=443, resolver=None, **kwargs):
    return anyio.run(lambda: check_destination(
        host, port, blocked_ports=kwargs.pop('blocked_ports', BLOCKED), resolver=resolver, **kwargs))


@pytest.mark.parametrize('address', [
    '169.254.169.254', '169.254.1.1', 'fe80::1', 'fe80::1%eth0', '100.100.100.200',
    'fd00:ec2::254', '0.0.0.0', '::', '224.0.0.1', 'ff02::1', '240.0.0.1',
    '::ffff:169.254.169.254',
])
def test_always_blocked_addresses(address):
    with pytest.raises(ExternalError, match='destination_blocked'):
        check(address, 443)


@pytest.mark.parametrize('address', ['127.0.0.1', '::1', '::ffff:127.0.0.1'])
def test_loopback_blocked_only_on_sklr_and_panel_ports(address):
    for port in BLOCKED:
        with pytest.raises(ExternalError, match='self_or_panel_endpoint'):
            check(address, port, own_addresses=frozenset())
    assert check(address, 9000, own_addresses=frozenset())


def test_private_addresses_are_allowed_for_corporate_servers():
    assert check('10.1.2.3', 443) == ['10.1.2.3']
    assert check('192.168.1.20', 8080) == ['192.168.1.20']
    assert check('93.184.216.34', 443) == ['93.184.216.34']


def test_hostname_resolving_to_loopback_is_blocked_on_sklr_port():
    with pytest.raises(ExternalError, match='self_or_panel_endpoint'):
        check('alias.example.test', 8101, resolving('127.0.0.1'), own_addresses=frozenset())
    assert check('alias.example.test', 9000, resolving('127.0.0.1'), own_addresses=frozenset())


def test_every_returned_address_must_pass():
    with pytest.raises(ExternalError, match='destination_blocked'):
        check('mixed.example.test', 443, resolving('93.184.216.34', '169.254.169.254'))


def test_this_machine_address_is_blocked_on_sklr_port():
    own = frozenset({'192.168.1.20', '10.0.0.5'})
    with pytest.raises(ExternalError, match='self_or_panel_endpoint'):
        check('lan.example.test', 8101, resolving('192.168.1.20'), own_addresses=own)
    assert check('lan.example.test', 443, resolving('192.168.1.20'), own_addresses=own)


def test_resolution_failures_are_safe(monkeypatch):
    async def fail(host, port):
        raise OSError('PRIVATE_HOSTNAME')
    with pytest.raises(ExternalError) as raised:
        check('x.example.test', 443, fail)
    assert raised.value.code == 'dns_failed' and 'PRIVATE_HOSTNAME' not in str(raised.value)
    with pytest.raises(ExternalError, match='dns_failed'):
        check('x.example.test', 443, resolving())
    with pytest.raises(ExternalError, match='dns_failed'):
        check('x.example.test', 443, resolving(*['93.184.216.34'] * 33))
    monkeypatch.setattr(address_guard, 'CONNECT_TIMEOUT_S', 0.05)
    async def hang(host, port):
        await anyio.sleep_forever()
    with pytest.raises(ExternalError, match='dns_timeout'):
        check('x.example.test', 443, hang)


def test_invalid_resolved_address_is_rejected():
    assert classify('not-an-ip', 443) == 'address_invalid'


def runtime(url):
    return RuntimeConfig(transport='http', url=url, tls=ssl.create_default_context())


def test_client_blocks_alias_to_sklr_before_reaching_transport():
    reached = []
    def respond(request):
        reached.append(request)
        return httpx.Response(200)
    async def use():
        async with make_http_client(
                runtime('https://alias.example.test:8101/mcp'), blocked_ports=BLOCKED,
                inner_transport=httpx.MockTransport(respond),
                resolver=resolving('127.0.0.1'), own_addresses=frozenset()) as client:
            await client.get('https://alias.example.test:8101/mcp')
    with pytest.raises(ExternalError, match='self_or_panel_endpoint'):
        anyio.run(use)
    assert reached == []


def test_client_allows_public_alias():
    reached = []
    def respond(request):
        reached.append(request)
        return httpx.Response(200, json={})
    async def use():
        async with make_http_client(
                runtime('https://alias.example.test/mcp'), blocked_ports=BLOCKED,
                inner_transport=httpx.MockTransport(respond),
                resolver=resolving('93.184.216.34')) as client:
            await client.get('https://alias.example.test/mcp')
    anyio.run(use)
    assert len(reached) == 1
