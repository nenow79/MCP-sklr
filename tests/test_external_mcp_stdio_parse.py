"""Parse real JSON-RPC lines with the installed MCP SDK types; no process, no network."""
import pytest

from external_mcp.limits import ExternalError
from external_mcp.stdio_transport import parse_line


def test_parses_response_and_request_like_sdk():
    response = parse_line(b'{"jsonrpc":"2.0","id":1,"result":{}}')
    assert response is not None
    request = parse_line(b'{"jsonrpc":"2.0","id":2,"method":"ping"}')
    assert request is not None


@pytest.mark.parametrize('line', [b'not json', b'{}', b'[1]', b'\xff'])
def test_invalid_lines_are_safe_errors(line):
    with pytest.raises(ExternalError) as raised:
        parse_line(line)
    assert raised.value.code == 'stdio_invalid_json'
