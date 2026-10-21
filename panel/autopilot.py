"""Panel-only routes; protected by the application's common security middleware."""
import json
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from autopilot.controller import AutopilotBlocked
from tasks.session_models import SessionConflict

IDENTITY = {'session_id', 'revision', 'decision_revision'}
ACTIONS = {'approve', 'reconfirm', 'revoke', 'manual', 'resolve', 'acknowledge-recovery'}


def autopilot_routes(admin):
    async def endpoint(request):
        try:
            if request.method == 'GET':
                call = admin.resources if request.url.path.endswith('/resources') else admin.status
                data = await run_in_threadpool(call)
            else:
                # Bound before parsing; full policy has a separate closed parser.
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 80000:
                        return JSONResponse({'success': False, 'error': 'Żądanie zbyt duże.'}, status_code=413)
                try:
                    body = json.loads(raw)
                except Exception:
                    raise ValueError('invalid_json') from None
                action = request.path_params['action']
                extra = ({'policy', 'goal', 'plan', 'limits'} if action == 'preview' else
                         {'confirmed'} | ({'preview_id'} if action == 'approve' else
                                          {'operation_id'} if action == 'resolve' else set()))
                if type(body) is not dict or set(body) != IDENTITY | extra:
                    raise ValueError('invalid_body')
                if action == 'preview':
                    data = await run_in_threadpool(admin.preview, body)
                elif action in ACTIONS:
                    data = await run_in_threadpool(admin.action, action, body)
                else:
                    return JSONResponse({'success': False, 'error': 'Nieznana decyzja.'}, status_code=404)
            return JSONResponse({'success': True, 'data': data})
        except (SessionConflict, AutopilotBlocked):
            return JSONResponse({'success': False, 'error': 'Stan lub uprawnienia zmieniły się. Odśwież i sprawdź decyzję.'}, status_code=409)
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            return JSONResponse({'success': False, 'error': 'Niepoprawna lub poufna delegacja. Sprawdź zakres i dostępność zasobów.'}, status_code=400)
        except Exception:
            return JSONResponse({'success': False, 'error': 'Decyzja niedostępna. Nie ponawiaj operacji o niepewnym skutku.'}, status_code=503)

    return [Route('/api/autopilot', endpoint, methods=['GET']),
            Route('/api/autopilot/resources', endpoint, methods=['GET']),
            Route('/api/autopilot/{action}', endpoint, methods=['POST'])]
