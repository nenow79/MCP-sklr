"""M4b: static checks for the external MCP tab. No browser, no server."""
import re
from pathlib import Path

from panel.app import DEFAULT_STATIC_DIR

STATIC = Path(DEFAULT_STATIC_DIR)


def read(name):
    return (STATIC / name).read_text(encoding='utf-8')


def test_tab_view_and_form_exist():
    html = read('index.html')
    for element_id in ('tab-external', 'external-view', 'external-list', 'external-form',
                       'external-alias', 'external-transport', 'external-url', 'external-command',
                       'external-save', 'external-cancel', 'external-error', 'external-state',
                       'external-net-fields', 'external-stdio-fields'):
        assert f'id="{element_id}"' in html, element_id


def test_every_external_id_used_by_js_exists_in_html():
    html, js = read('index.html'), read('app.js')
    ids = set(re.findall(r'"(external-[a-z-]+)"', js))
    assert {'external-alias', 'external-env-refs', 'external-headers', 'external-args'} <= ids
    for element_id in ids:
        assert f'id="{element_id}"' in html, element_id


def test_form_never_asks_for_secret_values_and_warns_about_launching():
    html = read('index.html')
    assert 'type="password"' not in html
    form = html.split('id="external-form"', 1)[1].split('</form>', 1)[0]
    assert 'nazwy zmiennych' in form.lower()
    assert 'Uruchomienie programu' in form


def test_js_calls_profile_endpoints_and_the_trust_flow():
    js = read('app.js')
    # Paths are built as "/api/external-mcp/" + id + "/" + action, so check the action names.
    assert '"/api/external-mcp"' in js and '"/api/external-mcp/"' in js
    for action in ('enabled', 'delete', 'revoke-trust', 'update'):
        assert f'externalPath(profile, "{action}")' in js or (
            action == 'update' and 'externalPath(current, "update")' in js), action
    # Trust is granted from a server-held inspection token; the browser never handles digests.
    assert 'externalPath(profile, "trust")' in js
    assert 'externalPath(profile, "inspect")' in js
    assert '"/api/external-mcp/inspections/"' in js
    assert 'schemaSha256' not in js
