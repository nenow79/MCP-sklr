"""Real MCP SDK over loopback HTTP and SSE with a local FastMCP server (M2d).

Starts child Python processes listening on 127.0.0.1 (random port); no external network.
The user runs it. A failure here is informative: it may reveal an SDK mismatch that the
in-memory transport tests cannot see.
"""
import socket
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from external_mcp.client import call_tool, fetch_catalog
from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile

SERVER = textwrap.dedent('''
    import sys
    from fastmcp import FastMCP

    transport, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    mcp = FastMCP("http-test")

    @mcp.tool
    def echo(text: str) -> str:
        """Echo text."""
        return text

    @mcp.tool
    def big() -> str:
        """Return more than the 4 MiB response limit."""
        return "x" * (5 * 1024 * 1024)

    if __name__ == "__main__":
        mcp.run(transport=transport, host="127.0.0.1", port=port, path=path, show_banner=False)
''')
PATHS = {'http': '/mcp', 'sse': '/sse'}


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def wait_listening(process, port, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('test server exited before listening')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError('test server did not start listening')


def profile_for(transport, port):
    row = validate_profile({'alias': 'local', 'transport': transport,
                            'url': f'http://127.0.0.1:{port}{PATHS[transport]}'})
    row.update(id='11111111-1111-4111-8111-111111111111',
               revision='22222222-2222-4222-8222-222222222222')
    return row


@pytest.fixture(params=['http', 'sse'])
def server(request, tmp_path):
    script = tmp_path / 'http server.py'
    script.write_text(SERVER, encoding='utf-8')
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(script), request.param, str(port), PATHS[request.param]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_listening(process, port)
        yield profile_for(request.param, port), request.param
    finally:
        process.kill()
        process.wait(timeout=10)


def text_of(response):
    return response['result']['content'][0]['text']


def test_real_sdk_catalog_and_call_over_http_and_sse(server):
    row, _ = server
    catalog = fetch_catalog(row, timeout=30)
    tools = {tool['name']: tool for tool in catalog['tools']}
    assert {'echo', 'big'} <= set(tools)
    response = call_tool(row, 'echo', {'text': 'zażółć'}, timeout=30,
                         expected_schema_sha256=tools['echo']['schemaSha256'])
    assert response['result']['isError'] is False
    assert text_of(response) == 'zażółć'


def test_response_over_limit_is_refused(server):
    row, transport = server
    with pytest.raises(ExternalError) as raised:
        call_tool(row, 'big', {}, timeout=30)
    # The call was dispatched, so the outcome is reported as uncertain, never as success.
    assert raised.value.code in ('call_failed_uncertain', 'call_timeout_uncertain'), (
        transport, raised.value.code)
    assert 'xxxx' not in str(raised.value)


class Recorder(BaseHTTPRequestHandler):
    hits = None

    def do_GET(self):
        self.server.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *args):
        pass


class Redirector(Recorder):
    def do_GET(self):
        self.send_response(307)
        self.send_header('Location', f'http://127.0.0.1:{self.server.target}/stolen')
        self.send_header('Content-Length', '0')
        self.end_headers()

    do_POST = do_GET


def start(handler, **attributes):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    httpd.hits = []
    for key, value in attributes.items():
        setattr(httpd, key, value)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


@pytest.mark.parametrize('transport', ['http', 'sse'])
def test_redirect_is_not_followed(transport):
    target = start(Recorder)
    source = start(Redirector, target=target.server_address[1])
    try:
        row = profile_for(transport, source.server_address[1])
        with pytest.raises(ExternalError):
            fetch_catalog(row, timeout=10)
        assert target.hits == [], 'client followed a redirect to another endpoint'
    finally:
        for httpd in (source, target):
            httpd.shutdown()
            httpd.server_close()
