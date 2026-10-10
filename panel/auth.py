#!/usr/bin/env python3
"""Uwierzytelnianie panelu: token startowy, sesje, kontrola Host i Origin.

Bez zależności od Starlette. Token żyje tylko w pamięci procesu (ADR 0002).
Warstwa HTTP (panel/app.py) decyduje, kiedy wymagać obecności nagłówka Origin.
"""
import secrets
import threading
from typing import Optional, Set

COOKIE_NAME = "vibecoder_panel"
DEFAULT_PORT = 8102
_LOOPBACK_NAMES = ("127.0.0.1", "localhost")


class PanelAuth:
    def __init__(self, port: int = DEFAULT_PORT):
        self.port = int(port)
        self._token = secrets.token_urlsafe(32)
        self._sessions: Set[str] = set()
        self._lock = threading.Lock()
        self._allowed_hosts = {f"{name}:{self.port}" for name in _LOOPBACK_NAMES}
        self._allowed_origins = {f"http://{host}" for host in self._allowed_hosts}

    @property
    def token(self) -> str:
        return self._token

    def exchange_token(self, token: Optional[str]) -> Optional[str]:
        """Wymienia poprawny token na nowy identyfikator sesji; zły token = None."""
        if not isinstance(token, str) or not token:
            return None
        if not secrets.compare_digest(token.encode("utf-8"), self._token.encode("utf-8")):
            return None
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions.add(session_id)
        return session_id

    def is_valid_session(self, session_id: Optional[str]) -> bool:
        if not isinstance(session_id, str) or not session_id:
            return False
        candidate = session_id.encode("utf-8")
        with self._lock:
            sessions = list(self._sessions)
        found = False
        for known in sessions:  # bez wczesnego wyjścia: stały czas względem liczby sesji
            if secrets.compare_digest(candidate, known.encode("utf-8")):
                found = True
        return found

    def is_allowed_host(self, host_header: Optional[str]) -> bool:
        if not isinstance(host_header, str):
            return False
        return host_header.strip().lower() in self._allowed_hosts

    def is_allowed_origin(self, origin_header: Optional[str]) -> bool:
        """Brak nagłówka (None) = True; wymóg jego obecności (POST) leży po stronie warstwy HTTP."""
        if origin_header is None:
            return True
        return origin_header.strip().lower() in self._allowed_origins
