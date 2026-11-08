"""L4 local metadata and opt-in form, without main bootstrap or external IO."""
import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from external_mcp_persistent_manual_helpers import ManualRig
from server.external_mcp_admin import ExternalMCPAdmin

STATIC = Path(__file__).resolve().parents[1] / 'panel' / 'static'


@pytest.mark.parametrize('selected', [True, False])
@pytest.mark.parametrize('state,reason', [('busy', ''), ('closing', 'pause'),
                                        ('broken', 'persistent_cleanup_failed')])
def test_panel_and_model_runtime_metadata_use_only_current_owner(tmp_path, selected, state, reason):
    rig = ManualRig(tmp_path / 'metadata', selected=selected)
    owners = []
    def snapshot(*, owner, profile_id):
        owners.append(owner)
        assert profile_id == rig.profile['id']
        assert rig.manager._cond._is_owned() and rig.controller._lock._is_owned()
        assert not rig.config.lock._is_owned() and not admin._lock._is_owned()
        return {'state': state, 'runtime_generation': 'current-generation', 'reason': reason,
                'upstream_payload': 'must-not-be-exposed'}
    rig.supervisor.snapshot = snapshot
    try:
        admin = ExternalMCPAdmin(rig.registry, rig.manager, runtime=rig.tools)
        for row in (admin.list()['profiles'][0], rig.tools.list_profiles()['data']['profiles'][0]):
            assert row['stdio_mode'] == 'persistent'
            assert row['runtime'] == ({'state': state,
                'runtime_generation': 'current-generation', 'reason': reason} if selected else
                {'state': 'absent', 'runtime_generation': None, 'reason': ''})
        assert owners == ([rig.session.id] * 2 if selected else [])
        assert not rig.supervisor.discoveries and not rig.legacy.calls
        assert not rig.tools.catalogs
    finally:
        rig.close()


def js_run(body, names=None):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js unavailable: panel JavaScript behavior not verified')
    source = (STATIC / 'app.js').read_text()
    names = names or ['externalTransportChanged', 'parseRefs', 'parseArgs', 'optionalText',
             'buildExternalProfile', 'resetExternalForm', 'startExternalEdit']
    functions = '\n'.join(re.search(r'  function ' + name + r'\([^\n]*\).*?\n  }',
                                    source, re.S).group() for name in names)
    script = '''const assert = require('node:assert/strict');
const fields = {}; const $ = id => fields[id] ||= {value:'', checked:false};
const EXTERNAL_TEXT_FIELDS = []; let externalEditing = null;
const refsToLines = map => Object.entries(map || {}).map(([k,v]) => k+'='+v).join('\\n');
''' + functions + '\n' + body
    result = subprocess.run([node], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_panel_stdio_mode_opt_in_survives_edit_and_is_excluded_from_http():
    html = (STATIC / 'index.html').read_text()
    assert 'id="external-stdio-mode"' in html
    js_run('''
resetExternalForm(); assert.equal($('external-stdio-mode').value, 'one_shot');
startExternalEdit({id:'fake', alias:'fake', transport:'stdio', stdio_mode:'persistent'});
assert.equal($('external-stdio-mode').value, 'persistent');
assert.equal(buildExternalProfile().profile.stdio_mode, 'persistent');
$('external-transport').value = 'http';
assert.equal(Object.hasOwn(buildExternalProfile().profile, 'stdio_mode'), false);
startExternalEdit({id:'old', alias:'old', transport:'stdio'});
assert.equal(buildExternalProfile().profile.stdio_mode, 'one_shot');
''')


def test_panel_distinguishes_live_runtime_admin_history_and_manual_trust():
    js_run('''
const visibleText = String, fmtTime = String, externalInspect = {};
const document = {createTextNode: text => ({text})};
function el(tag, props, children) {
  return {text: (props || {}).text || '', children: children || [],
          appendChild(child) {this.children.push(child);}};
}
const externalInspectionView = () => null;
function text(node) {return node.text + ' ' + (node.children || []).map(text).join(' ');}
const profile = {id:'fake', alias:'fake', transport:'stdio', command:'fake',
  stdio_mode:'persistent', enabled:true, trusted:true, trust:{tools:{preview:'digest'}},
  runtime:{state:'ready', runtime_generation:'working-generation', reason:''},
  last_test:{state:'failed', code:'timeout', at:'old'}};
const rendered = text(externalCard(profile));
assert.ok(rendered.includes('working-generation'), rendered);
assert.ok(rendered.includes('Runtime sesji pracy: ready'), rendered);
assert.ok(rendered.includes('Test administracyjny jednorazowy'), rendered);
assert.ok(rendered.includes('timeout'), rendered);
assert.ok(rendered.includes('Każde wywołanie persistent wymaga osobnej zgody'), rendered);
assert.ok(!rendered.includes('nie wymagają zgody'), rendered);
''', ['externalTarget', 'externalReferences', 'externalCard'])


def test_main_panel_uses_shared_runtime_adapter():
    tree = ast.parse((STATIC.parents[1] / 'main.py').read_text(encoding='utf-8-sig'))
    admins = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == 'ExternalMCPAdmin']
    assert len(admins) == 1
    assert any(kw.arg == 'runtime' and isinstance(kw.value, ast.Name)
               and kw.value.id == 'EXTERNAL_MCP_TOOLS' for kw in admins[0].keywords)
