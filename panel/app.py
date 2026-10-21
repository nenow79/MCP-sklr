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
from roots import RootConflict, RootError, RootNotFound
from server.skills_admin import SkillConflict, SkillNotFound
from external_mcp.profiles import ProfileConflict, ProfileError, ProfileNotFound
from server.knowledge_admin import KnowledgeConflict
from tasks.approval import ApprovalService
from tasks.manager import TaskManager, TaskNotFoundError, TaskStateError
from tasks.models import TaskStatus
from tasks.session_models import SessionConflict

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


def _root_error_response(exc: RootError) -> JSONResponse:
    if isinstance(exc, RootNotFound):
        return _err(str(exc), 404)
    if isinstance(exc, RootConflict):
        return _err(str(exc), 409)
    return _err(str(exc), 400)


def _opt_str(body: dict, key: str) -> Optional[str]:
    """Pole tekstowe opcjonalne: brak lub null = None; inny typ niż napis = ValueError."""
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Pole '{key}' musi być tekstem.")
    return value


def create_panel_app(manager: TaskManager, approval: ApprovalService, auth: PanelAuth,
                     static_dir: Optional[Path] = None, roots: Any = None,
                     skills: Any = None, knowledge: Any = None,
                     external_mcp: Any = None, work_sessions: Any = None) -> Starlette:
    """`roots` (RootsRegistry) jest opcjonalne: bez niego endpointy /api/roots nie istnieją (404)."""
    static_path = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
    if work_sessions is not None:
        work_sessions.attach_manager(manager)

    async def work_session_status(request):
        try:
            return _ok(await run_in_threadpool(work_sessions.status))
        except Exception:
            return _err("Nie można odczytać stanu sesji pracy.", 500)

    async def work_session_checkpoint(request):
        try:
            response = _ok(await run_in_threadpool(work_sessions.checkpoint))
        except Exception:
            response = _err("Nie można odczytać checkpointu sesji pracy.", 500)
        # Free-form RAM content must not become a browser/proxy cache entry.
        response.headers["Cache-Control"] = "no-store"
        return response

    async def work_session_start(request):
        body = await _json_body(request)
        if body is None or set(body) != {"roots"}:
            return _err("Oczekiwano obiektu z polem roots.", 400)
        try:
            return _ok(await run_in_threadpool(work_sessions.start, body["roots"]), 201)
        except SessionConflict:
            return _err("Sesja jest już przypięta lub jej stan się zmienił.", 409)
        except ValueError:
            return _err("Nieprawidłowe lub niedostępne foldery sesji.", 400)
        except Exception:
            return _err("Nie udało się rozpocząć sesji pracy. Sprawdź jej stan.", 500)

    async def work_session_pause(request):
        body = await _json_body(request)
        if (body is None or set(body) != {"session_id", "revision"}
                or not isinstance(body["session_id"], str)
                or type(body["revision"]) is not int or body["revision"] < 1):
            return _err("Oczekiwano session_id i dodatniej rewizji całkowitej.", 400)
        try:
            return _ok(await run_in_threadpool(work_sessions.pause,
                                              body["session_id"], body["revision"]))
        except SessionConflict:
            return _err("Sesja lub rewizja się zmieniła. Odśwież stan przed kolejną decyzją.", 409)
        except ValueError:
            return _err("Nieprawidłowe żądanie pauzy.", 400)
        except Exception:
            return _err("Nie udało się zapisać pauzy. Sprawdź stan blokady wykonania.", 500)

    def work_session_identity(body):
        from tasks.session_models import session_id
        session_id(body['session_id'])
        if type(body['revision']) is not int or body['revision'] < 1:
            raise ValueError('Invalid revision.')

    async def work_session_read(request):
        try:
            action = request.url.path.rsplit('/', 1)[-1]
            if action == 'resume-preview':
                query = request.query_params
                if set(query) != {'session_id', 'revision'} or len(query.multi_items()) != 2:
                    raise ValueError('Invalid preview query.')
                body = {'session_id': query['session_id'], 'revision': int(query['revision'])}
                work_session_identity(body)
                data = await run_in_threadpool(work_sessions.resume_preview,
                                              body['session_id'], body['revision'])
            elif action == 'resume-plan':
                data = await run_in_threadpool(work_sessions.resume_plan)
            else:
                data = await run_in_threadpool(work_sessions.saved_sessions)
            return _ok(data)
        except SessionConflict:
            return _err('Sesja lub rewizja się zmieniła. Odśwież stan.', 409)
        except (ValueError, KeyError):
            return _err('Nieprawidłowe żądanie odczytu sesji.', 400)
        except Exception:
            return _err('Nie można odczytać danych wznowienia. Sprawdź stan.', 500)

    async def work_session_resume_action(request):
        body = await _json_body(request)
        recover = request.url.path.endswith('/recover')
        fields = {'session_id', 'revision', 'confirmed'}
        if not recover:
            fields |= {'supplement', 'cancel_count', 'allow_missing_checkpoint'}
        if body is None or set(body) != fields:
            return _err('Nieprawidłowe pola decyzji panelowej.', 400)
        try:
            work_session_identity(body)
            if body['confirmed'] is not True:
                raise ValueError('Explicit confirmation required.')
            if recover:
                data = await run_in_threadpool(work_sessions.recover,
                    body['session_id'], body['revision'], body['confirmed'])
            else:
                if (type(body['supplement']) is not str
                        or type(body['cancel_count']) is not int or body['cancel_count'] < 0
                        or type(body['allow_missing_checkpoint']) is not bool):
                    raise ValueError('Invalid preparation fields.')
                data = await run_in_threadpool(work_sessions.prepare_resume,
                    body['session_id'], body['revision'], body['supplement'],
                    body['cancel_count'], body['allow_missing_checkpoint'], body['confirmed'])
            return _ok(data)
        except SessionConflict:
            return _err('Sesja, rewizja, liczba zadań lub checkpoint się zmieniły. Odśwież stan.', 409)
        except ValueError:
            return _err('Nieprawidłowa decyzja lub odrzucona treść. Niczego nie ponawiaj automatycznie.', 400)
        except Exception:
            return _err('Przygotowanie lub odzyskanie nie powiodło się. Sprawdź stan przed nową decyzją.', 500)

    async def knowledge_list(request):
        try:
            return _ok(await run_in_threadpool(knowledge.list))
        except Exception:
            return _err("Nie można odczytać konfiguracji wiedzy.", 500)

    async def knowledge_change(request):
        body = await _json_body(request)
        if body is None:
            return _err("Oczekiwano obiektu JSON.", 400)
        try:
            if "key" not in request.path_params:
                result = await run_in_threadpool(knowledge.add, body.get("bundle"), body.get("revision"))
            else:
                result = await run_in_threadpool(knowledge.change, request.path_params["key"],
                                                request.path_params["action"], body.get("enabled"), body.get("revision"))
            return _ok(result)
        except KnowledgeConflict as exc:
            return _err(str(exc), 409)
        except ValueError as exc:
            return _err(str(exc), 400)
        except Exception:
            return _err("Nie udało się zapisać konfiguracji wiedzy.", 500)

    async def knowledge_policy_change(request):
        body = await _json_body(request)
        if body is None:
            return _err("Oczekiwano obiektu JSON.", 400)
        try:
            return _ok(await run_in_threadpool(knowledge.set_project_policy,
                                               body.get("policy"), body.get("revision")))
        except KnowledgeConflict as exc:
            return _err(str(exc), 409)
        except ValueError as exc:
            return _err(str(exc), 400)
        except Exception:
            return _err("Nie udało się zapisać polityki projektu.", 500)

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
        data = task.to_dict()
        if task.kind == "external_mcp":
            preview = await run_in_threadpool(manager.get_approval_preview, task_id)
            if preview is not None:
                data["approval_preview"] = preview  # panel-only detached RAM, never TaskStore
        return _ok(data)

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

    async def list_roots(request: Request) -> JSONResponse:
        entries = await run_in_threadpool(roots.list_status)
        return _ok({"count": len(entries), "roots": entries})

    async def add_root(request: Request) -> JSONResponse:
        body = await _json_body(request)
        if body is None:
            return _err("Oczekiwano obiektu JSON.", 400)
        try:
            path = _opt_str(body, "path")
            mode = _opt_str(body, "mode")
            alias = _opt_str(body, "alias")
            description = _opt_str(body, "description") or ""
            make_default = body.get("default", False)
            if not isinstance(make_default, bool):
                raise ValueError("Pole 'default' musi być true lub false.")
            if not path:
                raise ValueError("Podaj ścieżkę do folderu.")
            root = await run_in_threadpool(roots.add, path, mode, alias or None, description,
                                           make_default)
        except RootError as exc:
            return _root_error_response(exc)
        except ValueError as exc:
            return _err(str(exc), 400)
        return _ok(root.to_dict(), 201)

    async def update_root(request: Request) -> JSONResponse:
        body = await _json_body(request)
        if body is None:
            return _err("Oczekiwano obiektu JSON.", 400)
        try:
            mode = _opt_str(body, "mode")
            description = _opt_str(body, "description")
            new_alias = _opt_str(body, "new_alias")
            root = await run_in_threadpool(
                lambda: roots.update(request.path_params["alias"], mode=mode,
                                     description=description, new_alias=new_alias or None))
        except RootError as exc:
            return _root_error_response(exc)
        except ValueError as exc:
            return _err(str(exc), 400)
        return _ok(root.to_dict())

    async def delete_root(request: Request) -> JSONResponse:
        try:
            root = await run_in_threadpool(roots.remove, request.path_params["alias"])
        except RootError as exc:
            return _root_error_response(exc)
        return _ok(root.to_dict())

    async def default_root(request: Request) -> JSONResponse:
        try:
            root = await run_in_threadpool(roots.set_default, request.path_params["alias"])
        except RootError as exc:
            return _root_error_response(exc)
        return _ok(root.to_dict())

    async def list_skills(request: Request) -> JSONResponse:
        try:
            data = await run_in_threadpool(skills.list)
        except Exception:
            return _err("Nie udało się pobrać listy skilli.", 500)
        return _ok(data)

    async def set_skill_enabled(request: Request) -> JSONResponse:
        body = await _json_body(request)
        if body is None or set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
            return _err("Oczekiwano obiektu z jednym polem 'enabled': true lub false.", 400)
        try:
            data = await run_in_threadpool(
                skills.set_enabled, request.path_params["name"], body["enabled"])
        except SkillNotFound:
            return _err("Nie znaleziono skilla.", 404)
        except ValueError as exc:
            return _err(str(exc), 400)
        except Exception:
            return _err("Nie udało się zapisać dostępności skilla.", 500)
        return _ok(data)

    async def skill_trust_action(request: Request) -> JSONResponse:
        body = await _json_body(request)
        granting = request.url.path.endswith("/trust")
        required = {"path", "hash"} if granting else {"path"}
        if body is None or set(body) != required or not all(
                isinstance(body[key], str) and body[key] for key in required):
            return _err("Niepoprawne dane wersji skilla.", 400)
        try:
            name = request.path_params["name"]
            if granting:
                data = await run_in_threadpool(skills.trust_version, name, body["path"], body["hash"])
            else:
                data = await run_in_threadpool(skills.revoke_trust, name, body["path"])
        except SkillNotFound:
            return _err("Nie znaleziono skilla.", 404)
        except SkillConflict as exc:
            return _err(str(exc), 409)
        except ValueError:
            return _err("Niepoprawne dane lub konfiguracja zaufania.", 400)
        except Exception:
            return _err("Nie udało się zapisać zaufania skilla.", 500)
        return _ok(data)

    def _external_error(exc: Exception) -> JSONResponse:
        # ProfileError messages hold a machine code only, never a submitted value.
        if isinstance(exc, ProfileConflict):
            return _err(str(exc), 409)
        if isinstance(exc, ProfileNotFound):
            return _err(str(exc), 404)
        if isinstance(exc, ProfileError):
            return _err(str(exc), 400)
        return _err("Nie udało się wykonać operacji na zewnętrznym MCP.", 500)

    async def external_list(request: Request) -> JSONResponse:
        try:
            return _ok(await run_in_threadpool(external_mcp.list))
        except Exception:
            return _err("Nie można odczytać profili zewnętrznych MCP.", 500)

    async def external_add(request: Request) -> JSONResponse:
        body = await _json_body(request)
        if body is None:
            return _err("Oczekiwano obiektu JSON.", 400)
        try:
            return _ok(await run_in_threadpool(
                external_mcp.add, body.get("profile"), body.get("revision")))
        except Exception as exc:
            return _external_error(exc)

    async def external_action(request: Request) -> JSONResponse:
        action = request.path_params["action"]
        profile_id = request.path_params["profile_id"]
        if action not in ("update", "enabled", "delete", "trust", "revoke-trust", "inspect"):
            return _err("Nieznana operacja.", 404)
        body = await _json_body(request)
        if body is None:
            return _err("Oczekiwano obiektu JSON.", 400)
        revision = body.get("revision")
        try:
            if action == "update":
                data = await run_in_threadpool(
                    external_mcp.update, profile_id, body.get("profile"), revision)
            elif action == "enabled":
                data = await run_in_threadpool(
                    external_mcp.set_enabled, profile_id, body.get("enabled"), revision)
            elif action == "delete":
                data = await run_in_threadpool(external_mcp.remove, profile_id, revision)
            elif action == "revoke-trust":
                data = await run_in_threadpool(external_mcp.revoke_trust, profile_id, revision)
            elif action == "trust":
                data = await run_in_threadpool(
                    external_mcp.grant_trust, profile_id, body.get("token"),
                    body.get("tools"), revision)
            else:
                data = await run_in_threadpool(
                    external_mcp.start_inspection, profile_id,
                    body.get("acknowledge_launch") is True)
                return _ok(data, 202)
            return _ok(data)
        except Exception as exc:
            return _external_error(exc)

    async def external_inspection(request: Request) -> JSONResponse:
        try:
            return _ok(await run_in_threadpool(
                external_mcp.get_inspection, request.path_params["token"]))
        except Exception as exc:
            return _external_error(exc)

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
    if roots is not None:
        routes[-1:-1] = [
            Route("/api/roots", list_roots, methods=["GET"]),
            Route("/api/roots", add_root, methods=["POST"]),
            Route("/api/roots/{alias}/update", update_root, methods=["POST"]),
            Route("/api/roots/{alias}/delete", delete_root, methods=["POST"]),
            Route("/api/roots/{alias}/default", default_root, methods=["POST"]),
        ]
    if skills is not None:
        routes[-1:-1] = [
            Route("/api/skills", list_skills, methods=["GET"]),
            Route("/api/skills/{name}/enabled", set_skill_enabled, methods=["POST"]),
            Route("/api/skills/{name}/trust", skill_trust_action, methods=["POST"]),
            Route("/api/skills/{name}/revoke-trust", skill_trust_action, methods=["POST"]),
        ]
    if knowledge is not None:
        routes.extend([Route("/api/knowledge", knowledge_list, methods=["GET"]),
                       Route("/api/knowledge", knowledge_change, methods=["POST"]),
                       Route("/api/knowledge/policy", knowledge_policy_change, methods=["POST"]),
                       Route("/api/knowledge/{key}/{action}", knowledge_change, methods=["POST"])])
    if external_mcp is not None:
        routes.extend([
            Route("/api/external-mcp", external_list, methods=["GET"]),
            Route("/api/external-mcp", external_add, methods=["POST"]),
            Route("/api/external-mcp/inspections/{token}", external_inspection, methods=["GET"]),
            Route("/api/external-mcp/{profile_id}/{action}", external_action, methods=["POST"]),
        ])
    if work_sessions is not None:
        routes.extend([
            Route("/api/work-session", work_session_status, methods=["GET"]),
            Route("/api/work-session/checkpoint", work_session_checkpoint, methods=["GET"]),
            Route("/api/work-session/start", work_session_start, methods=["POST"]),
            Route("/api/work-session/pause", work_session_pause, methods=["POST"]),
            Route("/api/work-session/resume-preview", work_session_read, methods=["GET"]),
            Route("/api/work-session/resume-plan", work_session_read, methods=["GET"]),
            Route("/api/work-session/saved", work_session_read, methods=["GET"]),
            Route("/api/work-session/prepare-resume", work_session_resume_action, methods=["POST"]),
            Route("/api/work-session/recover", work_session_resume_action, methods=["POST"]),
        ])
    if static_path.is_dir():
        routes.append(Mount("/static", app=StaticFiles(directory=str(static_path)), name="static"))

    return Starlette(routes=routes, middleware=[Middleware(SecurityMiddleware, auth=auth)])
