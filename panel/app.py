#!/usr/bin/env python3
"""Aplikacja Starlette panelu zatwierdzeń (API + statyczny frontend).

Zależności (TaskManager, ApprovalService, PanelAuth) są wstrzykiwane, więc całość
da się testować przez TestClient. Model zagrożeń: ADR 0002 (token -> ciasteczko
HttpOnly, kontrola Host i Origin, brak CORS).
"""
from pathlib import Path
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from panel.auth import COOKIE_NAME, PanelAuth
from tasks.approval import ApprovalService
from tasks.manager import TaskManager, TaskNotFoundError, TaskStateError
from tasks.models import TaskStatus

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent / "static"
PANEL_DECISIONS = ("allow", "trust", "deny")  # "timeout" tylko wewnętrznie (wygaśnięcie)
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
SESSION_PATH = "/api/session"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _ok(data: Any = None, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"success": True, "data": data}, status_code=status_code)


def _err(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Host, Origin, sesja (dla /api/*) oraz nagłówki bezpieczeństwa."""

    def __init__(self, app, auth: PanelAuth):
        super().__init__(app)
        self._auth = auth

    async def dispatch(self, request: Request, call_next):
        denied = self._check(request)
        response = denied if denied is not None else await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def _check(self, request: Request) -> Optional[JSONResponse]:
        auth = self._auth
        if not auth.is_allowed_host(request.headers.get("host")):
            return _err("Niedozwolony nagłówek Host.", 403)
        origin = request.headers.get("origin")
        if request.method == "POST" and origin is None:
            return _err("Brak nagłówka Origin.", 403)
        if not auth.is_allowed_origin(origin):
            return _err("Niedozwolony nagłówek Origin.", 403)
        path = request.url.path
        if path.startswith("/api/") and path != SESSION_PATH:
            if not auth.is_valid_session(request.cookies.get(COOKIE_NAME)):
                return _err("Brak ważnej sesji panelu.", 401)
        return None


async def _json_body(request: Request) -> Optional[dict]:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _parse_task_id(request: Request) -> Optional[int]:
    raw = request.path_params.get("task_id", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def create_panel_app(manager: TaskManager, approval: ApprovalService, auth: PanelAuth,
                     static_dir: Optional[Path] = None) -> Starlette:
    static_path = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR

    async def create_session(request: Request) -> JSONResponse:
        body = await _json_body(request)
        token = body.get("token") if body else None
        session_id = auth.exchange_token(token)
        if session_id is None:
            return _err("Nieprawidłowy token.", 401)
        response = _ok({"authenticated": True})
        response.set_cookie(COOKIE_NAME, session_id, httponly=True, samesite="strict", path="/")
        return response

    async def list_tasks(request: Request) -> JSONResponse:
        status = None
        raw_status = request.query_params.get("status")
        if raw_status:
            try:
                status = TaskStatus(raw_status)
            except ValueError:
                return _err(f"Nieznany status '{raw_status}'.", 400)
        try:
            limit = int(request.query_params.get("limit", DEFAULT_LIMIT))
        except ValueError:
            return _err("Parametr limit musi być liczbą całkowitą.", 400)
        limit = max(1, min(limit, MAX_LIMIT))
        tasks = await run_in_threadpool(manager.list, status, limit)
        return _ok({"count": len(tasks), "tasks": [t.to_summary() for t in tasks]})

    async def get_task(request: Request) -> JSONResponse:
        task_id = _parse_task_id(request)
        task = await run_in_threadpool(manager.get, task_id) if task_id else None
        if task is None:
            return _err("Nie ma takiego zadania.", 404)
        return _ok(task.to_dict())

    async def decide(request: Request) -> JSONResponse:
        task_id = _parse_task_id(request)
        if task_id is None:
            return _err("Nie ma takiego zadania.", 404)
        body = await _json_body(request)
        decision = body.get("decision") if body else None
        if decision not in PANEL_DECISIONS:
            return _err(f"Decyzja musi być jedną z: {list(PANEL_DECISIONS)}.", 400)
        try:
            task = await run_in_threadpool(approval.resolve, task_id, decision)
        except TaskNotFoundError:
            return _err("Nie ma takiego zadania.", 404)
        except TaskStateError as exc:
            return _err(str(exc), 409)
        return _ok(task.to_dict())

    async def cancel(request: Request) -> JSONResponse:
        task_id = _parse_task_id(request)
        if task_id is None:
            return _err("Nie ma takiego zadania.", 404)
        try:
            task = await run_in_threadpool(manager.cancel, task_id)
        except TaskNotFoundError:
            return _err("Nie ma takiego zadania.", 404)
        return _ok(task.to_dict())

    async def index(request: Request):
        index_file = static_path / "index.html"
        if not index_file.is_file():
            return _err("Brak pliku index.html panelu.", 404)
        return FileResponse(index_file)

    routes = [
        Route(SESSION_PATH, create_session, methods=["POST"]),
        Route("/api/tasks", list_tasks, methods=["GET"]),
        Route("/api/tasks/{task_id}", get_task, methods=["GET"]),
        Route("/api/tasks/{task_id}/decision", decide, methods=["POST"]),
        Route("/api/tasks/{task_id}/cancel", cancel, methods=["POST"]),
        Route("/", index, methods=["GET"]),
    ]
    if static_path.is_dir():
        routes.append(Mount("/static", app=StaticFiles(directory=str(static_path)), name="static"))

    return Starlette(routes=routes, middleware=[Middleware(SecurityMiddleware, auth=auth)])
