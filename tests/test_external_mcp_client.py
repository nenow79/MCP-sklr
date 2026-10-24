"""Client orchestration with fake transport/session: no network, SDK session or process."""
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import anyio
import pytest

from external_mcp.client import call_tool, fetch_catalog, schema_digest
from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile

SCHEMA = {'type': 'object', 'properties': {'q': {'type': 'string'}}}
TOOL = {'name': 'lookup', 'description': 'data', 'inputSchema': SCHEMA}


@pytest.fixture(autouse=True)
def schema_worker_without_process(monkeypatch):
    # Keep orchestration tests process-free while validating simple schemas with
    # the real worker policy. Dedicated tests cover the actual launcher.
    from external_mcp.schema_worker import validate_payload
    async def validate(schema, arguments):
        code = validate_payload(schema, arguments)
        if code != 'ok':
            raise ExternalError(code)
    monkeypatch.setattr('external_mcp.client.validate_arguments', validate)


def profile():
    row = validate_profile({'alias': 'remote', 'transport': 'http', 'url': 'https://example.test/mcp'})
    row.update(id=str(uuid4()), revision=str(uuid4()))
    return row


class Transport:
    def __init__(self):
        self.opened = self.closed = 0
    @asynccontextmanager
    async def __call__(self, runtime, blocked_ports):
        self.opened += 1
        try:
            yield ('read', 'write')
        finally:
            self.closed += 1


class Session:
    pages = None
    init_hang = False
    call_hang = False
    call_error = None
    def __init__(self, tools=None):
        self.tools, self.calls, self.lists = tools or [TOOL], [], []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return None
    async def initialize(self):
        if self.init_hang:
            await anyio.sleep_forever()
    async def list_tools(self, params=None):
        self.lists.append(params)
        pages = self.pages or [SimpleNamespace(tools=self.tools, next_cursor=None)]
        return pages[min(len(self.lists) - 1, len(pages) - 1)]
    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        self.calls.append((name, arguments, read_timeout_seconds))
        if self.call_error:
            raise self.call_error
        if self.call_hang:
            await anyio.sleep_forever()
        return {'content': [{'type': 'text', 'text': 'ok'}], 'isError': False}


def factory(session):
    return lambda streams, timeout: session


def test_catalog_pagination_and_digest():
    session = Session()
    session.pages = [SimpleNamespace(tools=[TOOL], next_cursor='next'),
                     SimpleNamespace(tools=[{**TOOL, 'name': 'second'}], next_cursor=None)]
    transport = Transport()
    result = fetch_catalog(profile(), transport=transport, session_factory=factory(session))
    assert [tool['name'] for tool in result['tools']] == ['lookup', 'second']
    assert result['tools'][0]['schemaSha256'] == schema_digest(SCHEMA)
    assert transport.opened == transport.closed == 1


def test_repeated_cursor_and_page_limit_are_rejected():
    session = Session()
    session.pages = [SimpleNamespace(tools=[TOOL], next_cursor='same')]
    with pytest.raises(ExternalError, match='catalog_pages'):
        fetch_catalog(profile(), transport=Transport(), session_factory=factory(session))


def test_call_once_with_explicit_arguments_and_typed_result():
    session = Session()
    result = call_tool(profile(), 'lookup', {'q': 'x'}, expected_schema_sha256=schema_digest(SCHEMA),
                       transport=Transport(), session_factory=factory(session), timeout=5)
    assert session.calls == [('lookup', {'q': 'x'}, 5.0)]
    assert result['result']['content'][0]['text'] == 'ok'


@pytest.mark.parametrize('name, expected, code', [
    ('missing', None, 'tool_not_found'), ('lookup', '0' * 64, 'schema_changed'),
])
def test_no_call_when_tool_missing_or_schema_changed(name, expected, code):
    session = Session()
    with pytest.raises(ExternalError, match=code):
        call_tool(profile(), name, {}, expected_schema_sha256=expected,
                  transport=Transport(), session_factory=factory(session))
    assert session.calls == []


@pytest.mark.parametrize('schema, arguments, code', [
    (SCHEMA, {'q': 3}, 'arguments_schema'),
    ({'type': 'object', 'required': ['q']}, {}, 'arguments_schema'),
    ({'type': 'object', 'additionalProperties': False}, {'extra': 'PRIVATE'}, 'arguments_schema'),
    ({'$ref': 'https://example.test/PRIVATE'}, {}, 'schema_reference_blocked'),
    ({'$schema': 'http://json-schema.org/draft-07/schema#'}, {}, 'schema_dialect'),
    ({'type': 'PRIVATE_BAD_TYPE'}, {}, 'schema_invalid'),
])
def test_schema_rejection_precedes_dispatch_and_closes_transport(schema, arguments, code):
    session = Session(tools=[{**TOOL, 'inputSchema': schema}])
    transport = Transport()
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', arguments, transport=transport,
                  session_factory=factory(session))
    assert raised.value.code == code
    assert 'PRIVATE' not in str(raised.value)
    assert session.calls == []
    assert transport.opened == transport.closed == 1


def test_schema_worker_timeout_is_not_uncertain(monkeypatch):
    async def timeout(schema, arguments):
        raise ExternalError('schema_validation_timeout')
    monkeypatch.setattr('external_mcp.client.validate_arguments', timeout)
    session, transport = Session(), Transport()
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', {}, transport=transport, session_factory=factory(session))
    assert raised.value.code == 'schema_validation_timeout'
    assert session.calls == [] and transport.closed == 1


def test_schema_changed_is_rejected_before_validation(monkeypatch):
    reached = []
    async def validate(schema, arguments):
        reached.append(True)
    monkeypatch.setattr('external_mcp.client.validate_arguments', validate)
    session = Session()
    with pytest.raises(ExternalError, match='schema_changed'):
        call_tool(profile(), 'lookup', {}, expected_schema_sha256='0' * 64,
                  transport=Transport(), session_factory=factory(session))
    assert reached == [] and session.calls == []


def test_dispatch_guard_runs_after_validation_before_call(monkeypatch):
    order, session = [], Session()
    async def validate(schema, arguments):
        order.append('validate')
    monkeypatch.setattr('external_mcp.client.validate_arguments', validate)
    def guard(name, digest):
        assert session.calls == []
        assert (name, digest) == ('lookup', schema_digest(SCHEMA))
        order.append('guard')
    call_tool(profile(), 'lookup', {}, transport=Transport(),
              session_factory=factory(session), before_dispatch=guard)
    assert order == ['validate', 'guard'] and len(session.calls) == 1


@pytest.mark.parametrize('code', ['approval_required', 'profile_changed', 'trust_revoked'])
def test_dispatch_guard_rejection_is_not_uncertain(code):
    session, transport = Session(), Transport()
    def guard(name, digest):
        raise ExternalError(code)
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', {}, transport=transport,
                  session_factory=factory(session), before_dispatch=guard)
    assert raised.value.code == code
    assert session.calls == [] and transport.closed == 1


def test_cancel_after_validation_blocks_guard_and_dispatch(monkeypatch):
    event, guards, session = threading.Event(), [], Session()
    async def validate(schema, arguments):
        event.set()
    monkeypatch.setattr('external_mcp.client.validate_arguments', validate)
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', {}, transport=Transport(), cancel_event=event,
                  session_factory=factory(session), before_dispatch=lambda *args: guards.append(True))
    assert raised.value.code == 'cancelled'
    assert guards == [] and session.calls == []


def test_call_failure_is_uncertain_sanitized_and_not_retried():
    session = Session()
    session.call_error = RuntimeError('PRIVATE_TOKEN')
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', {}, transport=Transport(), session_factory=factory(session))
    assert raised.value.code == 'call_failed_uncertain'
    assert 'PRIVATE_TOKEN' not in str(raised.value)
    assert len(session.calls) == 1


def test_precall_timeout_is_not_marked_uncertain():
    session = Session()
    session.init_hang = True
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', {}, timeout=1, transport=Transport(), session_factory=factory(session))
    assert raised.value.code == 'timeout'
    assert session.calls == []


def test_call_timeout_is_uncertain_and_transport_closed():
    session, transport = Session(), Transport()
    session.call_hang = True
    with pytest.raises(ExternalError) as raised:
        call_tool(profile(), 'lookup', {}, timeout=1, transport=transport, session_factory=factory(session))
    assert raised.value.code == 'call_timeout_uncertain'
    assert transport.closed == 1 and len(session.calls) == 1


def test_thread_cancel_during_call_is_uncertain():
    entered, event = threading.Event(), threading.Event()

    class EnteredSession(Session):
        async def call_tool(self, *args, **kwargs):
            entered.set()
            return await super().call_tool(*args, **kwargs)

    session, transport = EnteredSession(), Transport()
    session.call_hang = True

    def cancel_after_dispatch():
        # Bariera nie wygasa podczas przygotowania runtime (np. TLS Windows).
        # finally poniżej zwalnia ją również po błędzie przed dispatch.
        entered.wait()
        event.set()

    canceller = threading.Thread(target=cancel_after_dispatch)
    canceller.start()
    try:
        with pytest.raises(ExternalError) as raised:
            call_tool(profile(), 'lookup', {}, timeout=5, cancel_event=event,
                      transport=transport, session_factory=factory(session))
    finally:
        entered.set()  # sprzątanie także po błędzie przed dispatch
        canceller.join(5)
    assert not canceller.is_alive()
    assert raised.value.code == 'call_cancelled_uncertain'
    assert transport.opened == transport.closed == 1
    assert len(session.calls) == 1


@pytest.mark.parametrize('name, arguments', [('', {}), ('lookup', []), ('x' * 257, {})])
def test_invalid_inputs_do_not_open_transport(name, arguments):
    transport = Transport()
    with pytest.raises(ExternalError):
        call_tool(profile(), name, arguments, transport=transport, session_factory=factory(Session()))
    assert transport.opened == 0


def test_secrets_are_cleared_after_operation():
    captured = []
    class Capturing(Transport):
        @asynccontextmanager
        async def __call__(self, runtime, blocked_ports):
            captured.append(runtime)
            async with super().__call__(runtime, blocked_ports) as streams:
                yield streams
    row = validate_profile({'alias': 'remote', 'transport': 'http',
                            'url': 'https://example.test/mcp', 'bearer_env': 'REMOTE_TOKEN'})
    row.update(id=str(uuid4()), revision=str(uuid4()))
    fetch_catalog(row, environ={'REMOTE_TOKEN': 'private'}, transport=Capturing(),
                  session_factory=factory(Session()))
    assert captured[0].headers == {}
