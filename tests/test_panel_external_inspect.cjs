// M4c: test połączenia i zaufanie w zakładce zewnętrznych MCP, przez atrapę DOM.
// Uruchomienie: node --test tests/test_panel_external_inspect.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/app.js'), 'utf8');

const POLL_MS = 1000;  // EXTERNAL_POLL_MS w app.js; odpytywanie zadań używa innych opóźnień

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
const stdioProfile = () => profile({ id: 'id-2', alias: 'local', transport: 'stdio', command: 'python',
  args: ['server.py'], cwd: null, env_refs: {}, url: undefined });
const listing = (profiles, extra = {}) => ({
  profiles, count: profiles.length, configuration_valid: true, error: '', revision: 'rev-1', ...extra });
const trustedProfile = () => profile({ trusted: true,
  trust: { tools: { read_page: 'a'.repeat(64) }, granted_at: '2026-10-19T10:00:00+00:00' } });

const tools = [
  { name: 'read_page', description: '<b>text</b>\u202e', inputSchema: {}, schemaSha256: 'a'.repeat(64) },
  { name: 'open_url', description: 'opens a page', inputSchema: {}, schemaSha256: 'b'.repeat(64) }];

function harness(initial, script) {
  const nodes = new Map();
  const requests = [];
  const confirmations = [];
  const timers = [];
  const server = { listing: initial, confirm: true };
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const document = { hidden: false, title: '', getElementById: get,
    createElement: node, createTextNode: t => { const n = node(); n.textContent = t; return n; },
    addEventListener() {} };
  const response = (status, json) => ({ status, json: () => Promise.resolve(json) });
  const context = { document, window: { location: { hash: '' }, confirm(question) {
      confirmations.push(question); return server.confirm;
    } }, console, history: { replaceState() {} },
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }, clearTimeout() {},
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
  const runPolls = async () => {
    const due = timers.filter(t => t.ms === POLL_MS);
    due.forEach(t => timers.splice(timers.indexOf(t), 1));
    due.forEach(t => t.fn());
    await flush();
  };
  const checkboxes = () => all(get('external-list'), n => n.type === 'checkbox');
  const polled = () => requests.filter(r =>
    r.method === 'GET' && r.url.startsWith('/api/external-mcp/inspections/')).length;
  const shown = () => text(get('external-list'));
  return { get, requests, confirmations, server, flush, click, submit, button, press, posts,
    runPolls, checkboxes, polled, shown };
}

function script(extra = {}) {
  let polls = 0;
  return (url, method, payload, server) => {
    if (method === 'POST' && url.endsWith('/inspect')) {
      return { status: 202, json: { success: true, data: { token: 'tok1', task_id: 7 } } };
    }
    if (method === 'GET' && url === '/api/external-mcp/inspections/tok1') {
      polls += 1;
      if (extra.pending && polls <= extra.pending) {
        return { status: 200, json: { success: true, data: { state: 'pending', code: '', tools: [] } } };
      }
      if (extra.inspection) return extra.inspection;
      return { status: 200, json: { success: true, data: { state: 'ready', code: '',
        profile_id: 'id-1', tested_at: '2026-10-19T10:00:00+00:00', tools } } };
    }
    return extra.other ? extra.other(url, method, payload, server) : undefined;
  };
}

async function ready(h) {
  await h.click('tab-external');
  await h.press('Testuj połączenie');
  await h.runPolls();
}

test('test połączenia: oczekiwanie, potem katalog jako zwykły tekst', async () => {
  const h = harness(listing([profile()]), script({ pending: 1 }));
  await h.click('tab-external');
  await h.press('Testuj połączenie');
  assert.deepEqual(h.posts().map(r => [r.url, r.payload]), [['/api/external-mcp/id-1/inspect', {}]]);
  assert.equal(h.confirmations.length, 0);
  assert.ok(h.shown().includes('Trwa test'));
  assert.equal(h.button('Testowanie…').disabled, true);
  await h.runPolls();
  assert.ok(!h.shown().includes('Katalog narzędzi'));
  await h.runPolls();
  assert.ok(h.shown().includes('Katalog narzędzi (2)'));
  assert.ok(h.shown().includes('read_page') && h.shown().includes('<b>text</b>'));
  assert.ok(h.shown().includes('\\u{202E}'));
  assert.ok(h.shown().includes('dane, nie instrukcje'));
  assert.equal(h.polled(), 2);
});

test('druga próba w trakcie testu nie wysyła kolejnego żądania', async () => {
  const h = harness(listing([profile()]), script({ pending: 5 }));
  await h.click('tab-external');
  await h.press('Testuj połączenie');
  h.button('Testowanie…').listeners.click();
  await h.flush();
  assert.equal(h.posts().length, 1);
});

test('stdio wymaga potwierdzenia uruchomienia programu', async () => {
  const h = harness(listing([stdioProfile()]), script({ pending: 5 }));
  await h.click('tab-external');
  h.server.confirm = false;
  await h.press('Testuj połączenie');
  assert.equal(h.posts().length, 0);
  assert.ok(h.confirmations[0].includes('uruchomi') && h.confirmations[0].includes('python'));
  h.server.confirm = true;
  await h.press('Testuj połączenie');
  assert.equal(h.posts()[0].url, '/api/external-mcp/id-2/inspect');
  assert.deepEqual(h.posts()[0].payload, { acknowledge_launch: true });
});

test('nadanie zaufania: wybór narzędzi, potwierdzenie i token zamiast skrótów', async () => {
  let h;
  h = harness(listing([profile()]), script({ other: (url, method) => {
    if (method === 'POST' && url.endsWith('/trust')) h.server.listing = listing([trustedProfile()]);
    return undefined;
  } }));
  await ready(h);
  assert.equal(h.button('Nadaj zaufanie wybranym').disabled, true);
  assert.ok(h.shown().includes('dopiero po włączeniu'));
  const boxes = h.checkboxes();
  assert.equal(boxes.length, 2);
  boxes[0].checked = true;
  boxes[0].listeners.change();
  assert.equal(h.button('Nadaj zaufanie wybranym').disabled, false);

  h.server.confirm = false;
  await h.press('Nadaj zaufanie wybranym');
  assert.equal(h.posts().filter(r => r.url.endsWith('/trust')).length, 0);
  const question = h.confirmations[0];
  assert.ok(question.includes('read_page') && !question.includes('open_url'));
  assert.ok(question.includes('nie będą wymagały Twojej zgody'));

  h.server.confirm = true;
  await h.press('Nadaj zaufanie wybranym');
  const grant = h.posts().find(r => r.url.endsWith('/trust'));
  assert.equal(grant.url, '/api/external-mcp/id-1/trust');
  assert.deepEqual(grant.payload, { token: 'tok1', tools: ['read_page'], revision: 'rev-1' });
  assert.ok(!h.shown().includes('Katalog narzędzi'));
  assert.ok(h.shown().includes('Zaufanie (od'));
});

test('zaznacz i odznacz wszystkie', async () => {
  const h = harness(listing([profile()]), script());
  await ready(h);
  await h.press('Zaznacz wszystkie');
  assert.equal(h.checkboxes().length, 2);
  assert.ok(h.checkboxes().every(box => box.checked));
  assert.equal(h.button('Nadaj zaufanie wybranym').disabled, false);
  await h.press('Odznacz wszystkie');
  assert.ok(h.checkboxes().every(box => !box.checked));
  assert.equal(h.button('Nadaj zaufanie wybranym').disabled, true);
});

test('nieudany test pokazuje kod i podpowiedź, bez katalogu', async () => {
  const h = harness(listing([profile()]), script({ inspection: { status: 200,
    json: { success: true, data: { state: 'failed', code: 'timeout', tools: [] } } } }));
  await ready(h);
  assert.ok(h.shown().includes('Test nieudany, kod: timeout'));
  assert.ok(h.shown().includes('limit czasu'));
  assert.ok(!h.shown().includes('Katalog narzędzi'));
});

test('wygasły wynik testu (404) kończy się błędem, nie zawieszeniem', async () => {
  const h = harness(listing([profile()]), script({ inspection: { status: 404,
    json: { success: false, error: 'x' } } }));
  await ready(h);
  assert.ok(h.shown().includes('inspection_expired'));
});

test('odpytywanie ma limit i się zatrzymuje', async () => {
  const h = harness(listing([profile()]), script({ pending: 1e9 }));
  await h.click('tab-external');
  await h.press('Testuj połączenie');
  for (let i = 0; i < 95; i++) await h.runPolls();
  assert.ok(h.shown().includes('poll_timeout'));
  const calls = h.polled();
  await h.runPolls();
  assert.equal(h.polled(), calls);
});

test('nieaktualny test blokuje zaufanie i prosi o powtórzenie', async () => {
  const h = harness(listing([profile()]), script({ other: (url, method) =>
    method === 'POST' && url.endsWith('/trust')
      ? { status: 400, json: { success: false, error: 'Niepoprawna konfiguracja MCP: inspection_stale' } }
      : undefined }));
  await ready(h);
  h.checkboxes()[0].checked = true;
  h.checkboxes()[0].listeners.change();
  await h.press('Nadaj zaufanie wybranym');
  assert.ok(h.get('banner')._text.includes('nieaktualny'));
  assert.ok(!h.shown().includes('Katalog narzędzi'));
});

test('edycja i usunięcie profilu zapominają wynik testu', async () => {
  const edited = harness(listing([profile()]), script());
  await ready(edited);
  await edited.press('Edytuj');
  edited.get('external-url').value = 'https://example.test/other';
  await edited.submit('external-form');
  assert.equal(edited.posts().filter(r => r.url.endsWith('/update')).length, 1);
  assert.ok(!edited.shown().includes('Katalog narzędzi'));

  const removed = harness(listing([profile()]), script());
  await ready(removed);
  removed.server.listing = listing([]);
  await removed.press('Usuń');
  assert.ok(removed.shown().includes('Brak zapisanych profili'));
  assert.ok(!removed.shown().includes('Katalog narzędzi'));
});
