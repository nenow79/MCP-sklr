#!/usr/bin/env python3
"""Uruchamianie panelu zatwierdzeń w wątku procesu serwera (drugi uvicorn.Server).

Bind wyłącznie 127.0.0.1 (ADR 0002). Błąd startu (np. zajęty port) nie rzuca wyjątku:
start() zwraca False, a wywołujący może przełączyć się na zapasowy interfejs zatwierdzeń.
"""
import logging
import threading
import time
from typing import Optional

import uvicorn

logger = logging.getLogger(__name__)

PANEL_HOST = "127.0.0.1"


def build_panel_url(port: int, token: str) -> str:
    return f"http://{PANEL_HOST}:{int(port)}/#token={token}"


class PanelServer:
    def __init__(self, app, port: int):
        self.port = int(port)
        config = uvicorn.Config(app, host=PANEL_HOST, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread: Optional[threading.Thread] = None
        self._failed = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._server.started

    def _serve(self) -> None:
        try:
            self._server.run()
        except BaseException:  # uvicorn przy zajętym porcie kończy przez SystemExit
            logger.exception("Panel zatwierdzeń zakończył się błędem")
        finally:
            self._failed.set()

    def start(self, timeout: float = 5.0) -> bool:
        """Uruchamia panel; True, gdy serwer nasłuchuje. Nie rzuca wyjątków."""
        if self._thread is not None:
            return self.running
        self._thread = threading.Thread(target=self._serve, daemon=True, name="panel-server")
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return True
            if self._failed.is_set() or not self._thread.is_alive():
                return False
            time.sleep(0.02)
        return self._server.started

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
