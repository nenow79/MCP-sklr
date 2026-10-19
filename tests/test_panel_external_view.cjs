// M4b: the external MCP tab driven through a DOM stub. Run: node --test tests/test_panel_external_view.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/app.js'), 'utf8');

function node() {
  return {
    _text: '', value: '', childNodes: [], listeners: {}, hidden: false, disabled: false, checked: false,
    appendChild(child) { this.childNodes.push(child); return child; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    get textContent() { return this._text; },
    set textContent(value) { this._text = String(value); this.childNodes = []; }
  };
}
function all(root, predicate, out = []) {
  if (predicate(root)) out.push(root);
  root.childNodes.forEach(child => all(child, predicate, out));
  return out;
}
function text(root) { return all(root, n => !!n._text).map(n => n._text).join('\n'); }

const profile = (extra = {}) => ({
  id: 'id-1', alias: 'browser', description: '', enabled: false, transport: 'http',
  url: 'https://example.test/mcp', headers_env: {}, bearer_env: null, ca_file: null,
  revision: 'r1', env_missing: [], trusted: false, last_test: null, ...extra });
const listing = (profiles, extra = {}) => ({
  profiles, count: profiles.length, configuration_valid: true, error: '', revision: 'rev-1', ...extra });

function harness(initial, script) {
  const nodes = new Map();
  const requests = [];
  const confirmations = [];
  const server = { listing: initial, confirm: true };
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const document = { hidden: false, title: '', getElementById: get,
    createElement: node, createTextNode: t => { const n = node(); n.textContent = t; return n; },
    addEventListener() {} };
  const response = (status, json) => ({ status, json: () => Promise.resolve(json) });
  const context = { document, window: { location: { hash: '' }, confirm(question) {
      confirmations.push(question); return server.confirm;
    } }, console, history: { replaceState() {} }, setTimeout() { return 1; }, clearTimeout() {},
    fetch(url, opts) {
      const method = opts.method || 'GET';
      const payload = opts.body ? JSON.parse(opts.body) : undefined;
      requests.push({ url, method, payload });
      const scripted = script && script(url, method, payload, server);
      if (scripted) return Promise.resolve(scripted).then(s => response(s.status, s.json));
      if (url.startsWith('/api/external-mcp')) {
        return Promise.resolve(response(200, { success: true, data: server.listing }));
      }
      return Promise.resolve(response(200, { success: true, data: { tasks: [] } }));
    }
  };
  vm.runInNewContext(source, context);
  const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
  const click = async id => { get(id).listeners.click(); await flush(); };
  const submit = async id => { get(id).listeners.submit({ preventDefault() {} }); await flush(); };
  const button = label => all(get('external-list'), n => n.type === 'button' && n._text === label)[0];
  const press = async label => { button(label).listeners.click(); await flush(); };
  const posts = () => requests.filter(r => r.method === 'POST');
  const fill = values => { for (const [id, value] of Object.entries(values)) get(id).value = value; };
  return { get, requests, confirmations, server, flush, click, submit, button, press, posts, fill };
}

test('zakladka pokazuje profile jako zwykly tekst', async () => {
  const trust = { tools: { read_page: 'a'.repeat(64), list_tabs: 'b'.repeat(64) },
    granted_at: '2026-10-19T10:00:00+00:00' };
  const h = harness(listing([profile({ description: '<b>x</b>\u202e', trusted: true, trust })]));
  await h.click('tab-external');
  assert.equal(h.get('external-view').hidden, false);
  assert.equal(h.get('tasks-view').hidden, true);
  const shown = text(h.get('external-list'));
  assert.ok(shown.includes('<b>x</b>'));
  assert.ok(shown.includes('\\u{202E}'));
  assert.ok(shown.includes('zaufany') && shown.includes('read_page'));
  assert.ok(shown.includes('nie wymagają zgody'));
  assert.equal(h.posts().length, 0);
});

test('dodanie profilu http wysyla tylko nazwy zmiennych i rewizje', async () => {
  const h = harness(listing([]));
  await h.click('tab-external');
  h.fill({ 'external-alias': 'docs', 'external-description': '  dokumentacja ',
    'external-transport': 'http', 'external-url': ' https://docs.example.test/mcp ',
    'external-bearer': 'DOCS_TOKEN', 'external-headers': 'X-Api-Key=DOCS_KEY\n' });
  h.get('external-enabled').checked = true;
  await h.submit('external-form');
  assert.equal(h.posts().length, 1);
  assert.equal(h.posts()[0].url, '/api/external-mcp');
  assert.deepEqual(h.posts()[0].payload, { profile: { alias: 'docs', description: 'dokumentacja',
    enabled: true, transport: 'http', url: 'https://docs.example.test/mcp',
    headers_env: { 'X-Api-Key': 'DOCS_KEY' }, bearer_env: 'DOCS_TOKEN', ca_file: null },
  revision: 'rev-1' });
});

test('formularz stdio: argumenty w wierszach, zmienne i przelaczanie pol', async () => {
  const h = harness(listing([]));
  await h.click('tab-external');
  const select = h.get('external-transport');
  select.value = 'stdio';
  select.listeners.change();
  assert.equal(h.get('external-net-fields').hidden, true);
  assert.equal(h.get('external-stdio-fields').hidden, false);
  h.fill({ 'external-alias': 'local', 'external-command': 'C:/Program Files/py/python.exe',
    'external-args': 'server.py\n--name\nwith spaces\n\n', 'external-cwd': 'C:/My Servers',
    'external-env-refs': 'API_TOKEN=REMOTE_TOKEN' });
  await h.submit('external-form');
  assert.deepEqual(h.posts()[0].payload.profile, { alias: 'local', description: '', enabled: false,
    transport: 'stdio', command: 'C:/Program Files/py/python.exe',
    args: ['server.py', '--name', 'with spaces'], cwd: 'C:/My Servers',
    env_refs: { API_TOKEN: 'REMOTE_TOKEN' } });
});

test('niepoprawne albo powtorzone wiersze zmiennych nie wysylaja zadania', async () => {
  const h = harness(listing([]));
  await h.click('tab-external');
  h.fill({ 'external-alias': 'docs', 'external-url': 'https://docs.example.test/mcp',
    'external-headers': 'tylko_nazwa' });
  await h.submit('external-form');
  h.fill({ 'external-headers': 'A=B\nA=C' });
  await h.submit('external-form');
  assert.equal(h.posts().length, 0);
  assert.ok(text(h.get('external-error')).includes('NAZWA=ZMIENNA'));
});

test('wlaczanie i cofanie zaufania wysylaja rewizje', async () => {
  const trust = { tools: { read_page: 'a'.repeat(64) }, granted_at: '2026-10-19T10:00:00+00:00' };
  const h = harness(listing([profile({ trusted: true, trust })]));
  await h.click('tab-external');
  await h.press('Włącz');
  await h.press('Cofnij zaufanie');
  assert.deepEqual(h.posts().map(r => [r.url, r.payload]), [
    ['/api/external-mcp/id-1/enabled', { enabled: true, revision: 'rev-1' }],
    ['/api/external-mcp/id-1/revoke-trust', { revision: 'rev-1' }]]);
});

test('usuniecie wymaga potwierdzenia', async () => {
  const h = harness(listing([profile()]));
  await h.click('tab-external');
  h.server.confirm = false;
  await h.press('Usuń');
  assert.equal(h.posts().length, 0);
  assert.ok(h.confirmations[0].includes('browser'));
  h.server.confirm = true;
  await h.press('Usuń');
  assert.deepEqual(h.posts().map(r => [r.url, r.payload]), [
    ['/api/external-mcp/id-1/delete', { revision: 'rev-1' }]]);
});

test('edycja zaufanego profilu wymaga potwierdzenia utraty zaufania', async () => {
  const trust = { tools: { read_page: 'a'.repeat(64) }, granted_at: '2026-10-19T10:00:00+00:00' };
  const h = harness(listing([profile({ trusted: true, trust })]));
  await h.click('tab-external');
  await h.press('Edytuj');
  assert.equal(h.get('external-alias').value, 'browser');
  assert.equal(h.get('external-url').value, 'https://example.test/mcp');
  h.get('external-url').value = 'https://example.test/other';
  h.server.confirm = false;
  await h.submit('external-form');
  assert.equal(h.posts().length, 0);
  assert.ok(h.confirmations[0].includes('zeruje zaufanie'));
  h.server.confirm = true;
  await h.submit('external-form');
  assert.equal(h.posts().length, 1);
  assert.equal(h.posts()[0].url, '/api/external-mcp/id-1/update');
  assert.equal(h.posts()[0].payload.profile.url, 'https://example.test/other');
  assert.equal(h.posts()[0].payload.revision, 'rev-1');
});

test('konflikt rewizji odswieza widok', async () => {
  const h = harness(listing([profile()]), (url, method) =>
    method === 'POST' ? { status: 409, json: { success: false, error: 'x' } } : undefined);
  await h.click('tab-external');
  const gets = () => h.requests.filter(r => r.method === 'GET' && r.url === '/api/external-mcp').length;
  const before = gets();
  await h.press('Włącz');
  assert.ok(text(h.get('banner')).includes('zmieniła się'));
  assert.equal(gets(), before + 1);
});

test('uszkodzona konfiguracja blokuje zapis i pokazuje kod', async () => {
  const h = harness(listing([], { configuration_valid: false, error: 'section_format', revision: null }));
  await h.click('tab-external');
  assert.ok(text(h.get('external-state')).includes('section_format'));
  assert.equal(h.get('external-save').disabled, true);
  h.fill({ 'external-alias': 'docs', 'external-url': 'https://docs.example.test/mcp' });
  await h.submit('external-form');
  assert.equal(h.posts().length, 0);
});

test('wylaczona funkcja serwera pokazuje komunikat', async () => {
  const h = harness(listing([]), (url, method) =>
    url === '/api/external-mcp' && method === 'GET' ? { status: 404, json: {} } : undefined);
  await h.click('tab-external');
  assert.ok(text(h.get('banner')).includes('wyłączone'));
});

test('druga operacja jest blokowana, gdy poprzednia trwa', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness(listing([profile()]), (url, method) => method === 'POST' ? pending : undefined);
  await h.click('tab-external');
  h.button('Włącz').listeners.click();
  h.button('Włącz').listeners.click();
  assert.equal(h.posts().length, 1);
  release({ status: 200, json: { success: true, data: h.server.listing } });
  await h.flush();
});
