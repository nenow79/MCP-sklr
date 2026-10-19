"""Real MCP SDK + local FastMCP stdio server. Launches only sys.executable on a tmp script.

No network and no external server. Starts child Python processes, so the user runs it.
"""
import sys
import textwrap

import pytest

from external_mcp.client import call_tool, fetch_catalog
from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile

SERVER = textwrap.dedent('''
    import os
    import time
    from fastmcp import FastMCP

    mcp = FastMCP("local-test")

    @mcp.tool
    def echo(text: str) -> str:
        """Echo text."""
        return text

    @mcp.tool
    def has_env(name: str) -> bool:
        """Report only whether a variable exists in this process."""
        return name in os.environ

    @mcp.tool
    def slow() -> str:
        """Sleep long enough to exercise client timeout."""
        time.sleep(30)
        return "late"

    if __name__ == "__main__":
        mcp.run()
''')


@pytest.fixture
def profile(tmp_path):
    script = tmp_path / 'server with spaces.py'
    script.write_text(SERVER, encoding='utf-8')
    row = validate_profile({'alias': 'local', 'transport': 'stdio', 'command': sys.executable,
                            'args': [str(script)], 'cwd': str(tmp_path)})
    row.update(id='11111111-1111-4111-8111-111111111111',
               revision='22222222-2222-4222-8222-222222222222')
    return row


def text_of(response):
    return response['result']['content'][0]['text']


def test_real_sdk_catalog_and_call(profile):
    catalog = fetch_catalog(profile, timeout=30)
    tools = {tool['name']: tool for tool in catalog['tools']}
    assert {'echo', 'has_env', 'slow'} <= set(tools)
    assert tools['echo']['inputSchema']['properties']['text']['type'] == 'string'
    response = call_tool(profile, 'echo', {'text': 'zażółć'}, timeout=30,
                         expected_schema_sha256=tools['echo']['schemaSha256'])
    assert response['result']['isError'] is False
    assert text_of(response) == 'zażółć'


def test_real_stdio_does_not_inherit_unlisted_parent_variables(profile, monkeypatch):
    monkeypatch.setenv('SKLR_PARENT_ONLY', 'must-not-be-inherited')
    response = call_tool(profile, 'has_env', {'name': 'SKLR_PARENT_ONLY'}, timeout=30)
    assert text_of(response).lower() == 'false'


def test_real_call_timeout_is_uncertain_and_sanitized(profile):
    with pytest.raises(ExternalError) as raised:
        # The timeout covers process start, initialize and catalog too. It must be long
        # enough to reach tools/call, otherwise the correct code is plain 'timeout'.
        call_tool(profile, 'slow', {}, timeout=10)
    assert raised.value.code == 'call_timeout_uncertain'
    assert 'late' not in str(raised.value)
