"""Testy panel/server.py: start/stop panelu w wątku, zajęty port, adres z tokenem."""
import socket

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from panel.server import PanelServer, build_panel_url


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_app():
    async def hello(request):
        return PlainTextResponse("pong")

    return Starlette(routes=[Route("/", hello)])


def http_get(port: int, path: str = "/") -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", "replace")


def test_build_panel_url():
    assert build_panel_url(8102, "abc") == "http://127.0.0.1:8102/#token=abc"
    assert build_panel_url("9000", "t-1_x") == "http://127.0.0.1:9000/#token=t-1_x"


def test_start_serve_and_stop():
    port = free_port()
    server = PanelServer(make_app(), port)
    try:
        assert server.start(timeout=10) is True
        assert server.running
        assert "pong" in http_get(port)
    finally:
        server.stop()
    assert not server.running


def test_start_on_busy_port_returns_false():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    server = PanelServer(make_app(), port)
    try:
        assert server.start(timeout=10) is False
        assert not server.running
    finally:
        server.stop()
        blocker.close()


def test_binds_only_to_loopback():
    port = free_port()
    server = PanelServer(make_app(), port)
    try:
        assert server.start(timeout=10)
        assert server._server.config.host == "127.0.0.1"
    finally:
        server.stop()


def test_start_twice_does_not_spawn_second_thread():
    port = free_port()
    server = PanelServer(make_app(), port)
    try:
        assert server.start(timeout=10)
        thread = server._thread
        assert server.start(timeout=10) is True
        assert server._thread is thread
    finally:
        server.stop()
