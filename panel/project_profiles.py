"""Authenticated panel routes for named projects, not model-facing tools."""
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from roots import RootError
from server.project_profiles import ProfileConflict
from tasks.session_models import SessionConflict


def project_profile_routes(service):
    def error(text, code):
        return JSONResponse({'success': False, 'error': text}, status_code=code)

    async def endpoint(request):
        try:
            if request.method == 'GET':
                data = await run_in_threadpool(service.list)
            else:
                try:
                    body = await request.json()
                except Exception:
                    return error('Oczekiwano obiektu JSON.', 400)
                data = await run_in_threadpool(service.change, request.path_params['action'], body)
            return JSONResponse({'success': True, 'data': data})
        except (SessionConflict, ProfileConflict) as exc:
            return error(str(exc), 409)
        except (RootError, ValueError) as exc:
            return error(str(exc), 400)
        except Exception:
            return error('Niepewny wynik operacji projektu. Odśwież stan; nie ponawiaj automatycznie.', 500)

    return [Route('/api/project-profiles', endpoint, methods=['GET']),
            Route('/api/project-profiles/{action}', endpoint, methods=['POST'])]
