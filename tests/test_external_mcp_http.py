"""HTTP guards tested against in-memory httpx2 transports; no network access."""
import ssl

import anyio
import httpx2 as httpx
import pytest

from external_mcp.http_transport import (
    ResponseBudget, make_http_client, origin, validate_response,
)
from external_mcp.limits import ExternalError
from external_mcp.runtime_config import RuntimeConfig


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    # example.test does not resolve; give it a public address so only the HTTP guards are tested.
    async def resolver(host, port):
        return ['93.184.216.34']
    monkeypatch.setattr('external_mcp.address_guard.default_resolver', resolver)


def runtime():
    return RuntimeConfig(transport='http', url='https://example.test/mcp',
                         tls=ssl.create_default_context(),
                         headers={'Authorization': 'Bearer private'})


def test_origin_default_ports_and_trailing_dot():
    assert origin('https://EXAMPLE.test./mcp') == origin('https://example.test:443/other')
    assert origin('https://example.test') != origin('http://example.test')


def test_response_and_session_limits():
    budget = ResponseBudget(response_limit=10, session_limit=15)
    assert budget.consume(0, 10) == 10
    assert budget.consume(0, 5) == 5
    with pytest.raises(ExternalError, match='http_message_limit'):
        budget.consume(5, 1)
    with pytest.raises(ExternalError, match='http_message_limit'):
        ResponseBudget(response_limit=10).consume(8, 3)


@pytest.mark.parametrize('status, headers, code', [
    (302, {'location': 'https://other.test'}, 'http_redirect_blocked'),
    (200, {'content-encoding': 'gzip'}, 'http_compression_blocked'),
    (200, {'content-length': '99999999'}, 'http_message_limit'),
    (200, {'content-length': '-1'}, 'http_message_limit'),
    (200, {'content-length': 'bad'}, 'http_length_invalid'),
])
def test_rejected_response_headers(status, headers, code):
    with pytest.raises(ExternalError, match=code):
        validate_response(status, headers)


def test_identity_encoding_and_small_length_allowed():
    validate_response(200, {'content-encoding': 'identity', 'content-length': '10'})


def test_client_has_no_env_redirects_and_accepts_same_origin():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    async def use():
        async with make_http_client(runtime(), inner_transport=httpx.MockTransport(respond)) as client:
            response = await client.get('https://example.test/messages')
            assert response.json() == {'ok': True}
            assert not client.follow_redirects
    anyio.run(use)
    assert requests[0].headers['accept-encoding'] == 'identity'
    assert requests[0].headers['authorization'] == 'Bearer private'


@pytest.mark.parametrize('url', [
    'https://other.test/messages', 'http://example.test/messages',
    'https://example.test:8443/messages', 'https://example.test/messages?token=x',
])
def test_unsafe_endpoint_never_reaches_inner_transport(url):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200)
    async def use():
        async with make_http_client(runtime(), inner_transport=httpx.MockTransport(respond)) as client:
            await client.get(url)
    with pytest.raises(ValueError):
        anyio.run(use)
    assert requests == []


def test_redirect_does_not_forward_credentials():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={'location': 'https://other.test/private'})
    async def use():
        async with make_http_client(runtime(), inner_transport=httpx.MockTransport(respond)) as client:
            await client.post(runtime().url, json={})
    with pytest.raises(ExternalError, match='http_redirect_blocked'):
        anyio.run(use)
    assert len(requests) == 1


def test_transport_errors_are_safe_and_not_retried():
    attempts = []
    def fail(request):
        attempts.append(request)
        raise RuntimeError('PRIVATE_TOKEN')
    async def use():
        async with make_http_client(runtime(), inner_transport=httpx.MockTransport(fail)) as client:
            await client.post(runtime().url, json={})
    with pytest.raises(ExternalError) as raised:
        anyio.run(use)
    assert 'PRIVATE_TOKEN' not in str(raised.value)
    assert len(attempts) == 1


def test_streaming_body_bounded_and_closed(monkeypatch):
    monkeypatch.setattr('external_mcp.http_transport.ResponseBudget',
                        lambda: ResponseBudget(response_limit=10, session_limit=20))
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'123456'
            yield b'789012'
        async def aclose(self):
            self.closed = True
    stream = Stream()
    def respond(request):
        return httpx.Response(200, stream=stream)
    async def use():
        async with make_http_client(runtime(), inner_transport=httpx.MockTransport(respond)) as client:
            await client.get(runtime().url)
    with pytest.raises(ExternalError, match='http_message_limit'):
        anyio.run(use)
    assert stream.closed


def sse_runtime():
    row = runtime()
    row.transport = 'sse'
    return row


def test_sse_message_endpoint_may_carry_query_on_same_origin_only():
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={})
    async def use(url):
        async with make_http_client(sse_runtime(), inner_transport=httpx.MockTransport(respond)) as client:
            await client.post(url, json={})
    anyio.run(use, 'https://example.test/messages?session_id=abc')
    assert requests == ['https://example.test/messages?session_id=abc']
    with pytest.raises(ExternalError):
        anyio.run(use, 'https://other.test/messages?session_id=abc')
    assert len(requests) == 1


def test_http_transport_still_rejects_query_and_reports_field_code():
    async def use():
        async with make_http_client(runtime(), inner_transport=httpx.MockTransport(
                lambda request: httpx.Response(200))) as client:
            await client.post('https://example.test/messages?session_id=abc', json={})
    with pytest.raises(ExternalError) as raised:
        anyio.run(use)
    assert raised.value.code == 'url'
