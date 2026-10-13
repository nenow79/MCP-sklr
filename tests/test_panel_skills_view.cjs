const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/app.js'), 'utf8');

function node() {
  return {
    _text: '', childNodes: [], listeners: {}, hidden: false, disabled: false, checked: false,
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
const record = (extra = {}) => ({ name: 'demo', description: 'opis', source: 'central',
  path: 'folder', warnings: [], enabled: true, shadowed: false, ...extra });

function harness(records, post, confirmResult = true) {
  const nodes = new Map();
  const requests = [];
  const confirmations = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const document = { hidden: false, title: '', getElementById: get,
    createElement: node, createTextNode: t => { const n = node(); n.textContent = t; return n; },
    addEventListener() {} };
  const response = (status, json) => ({ status, json: () => Promise.resolve(json) });
  const context = { document, window: { location: { hash: '' }, confirm(question) {
      confirmations.push(question); return confirmResult;
    } }, console,
    history: { replaceState() {} }, setTimeout() { return 1; }, clearTimeout() {},
    fetch(url, opts) {
      requests.push({ url, opts });
      if (url === '/api/skills') return Promise.resolve(response(200, {
        success: true, data: { skills: records, dev_dirs: ['dev-folder'] } }));
      if (opts.method === 'POST') {
        const payload = JSON.parse(opts.body);
        if (url.endsWith('/trust') || url.endsWith('/revoke-trust')) {
          return (post ? post(payload) : Promise.resolve({ success: true, data: {} }))
            .then(json => {
              if (json.success) records[0].trust_status = url.endsWith('/trust') ? 'trusted' : 'untrusted';
              return response(json.success ? 200 : 409, json);
            });
        }
        const wanted = payload.enabled;
        return (post ? post(wanted) : Promise.resolve({ success: true, data: { enabled: wanted } }))
          .then(json => {
            if (json.success) records[0].enabled = json.data.enabled;
            return response(json.success ? 200 : 500, json);
          });
      }
      return Promise.resolve(response(200, { success: true, data: { tasks: [] } }));
    }
  };
  vm.runInNewContext(source, context);
  const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
  const checkbox = index => all(get('skills-list'), n => n.type === 'checkbox')[index];
  const click = async id => { get(id).listeners.click(); await flush(); };
  return { get, requests, confirmations, flush, checkbox, click };
}

test('trzecia zakładka, źródła, kopia przesłonięta i katalogi dev', async () => {
  const h = harness([record(), record({ source: 'dev', enabled: false, shadowed: true }),
    record({ name: 'local', source: 'project' })]);
  await h.click('tab-skills');
  assert.equal(h.get('skills-view').hidden, false);
  assert.equal(h.get('tasks-view').hidden, true);
  assert.equal(h.get('roots-view').hidden, true);
  assert.equal(h.checkbox(1).disabled, true);
  assert.ok(text(h.get('skills-list')).includes('Przesłonięty'));
  for (const s of ['central', 'dev', 'project']) assert.ok(text(h.get('skills-list')).includes(s));
  assert.ok(text(h.get('skills-dev-dirs')).includes('dev-folder'));
  await h.click('tab-tasks');
  assert.equal(h.get('skills-view').hidden, true);
  assert.equal(h.get('tasks-view').hidden, false);
});

test('zapis POST i stan potwierdzony przez API', async () => {
  const h = harness([record()]);
  await h.click('tab-skills');
  const box = h.checkbox(0);
  box.checked = false;
  box.listeners.change();
  assert.equal(box.disabled, true);
  assert.equal(h.get('skills-refresh').disabled, true);
  await h.flush();
  assert.equal(h.checkbox(0).checked, false);
  assert.equal(h.checkbox(0).disabled, false);
  const post = h.requests.find(r => r.opts.method === 'POST');
  assert.equal(post.url, '/api/skills/demo/enabled');
  assert.deepEqual(JSON.parse(post.opts.body), { enabled: false });
});

test('błąd zapisu przywraca stan, kopia nie wysyła POST', async () => {
  const h = harness([record(), record({ shadowed: true, enabled: false })],
    async () => ({ success: false, error: 'Błąd' }));
  await h.click('tab-skills');
  h.checkbox(1).listeners.change();
  assert.equal(h.requests.filter(r => r.opts.method === 'POST').length, 0);
  h.checkbox(0).checked = false;
  h.checkbox(0).listeners.change();
  await h.flush();
  assert.equal(h.checkbox(0).checked, true);
  assert.ok(text(h.get('banner')).includes('Błąd'));
});

test('druga zmiana jest blokowana podczas zapisu', async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const h = harness([record(), record({ name: 'second' })], () => pending);
  await h.click('tab-skills');
  h.checkbox(0).checked = false;
  h.checkbox(0).listeners.change();
  h.checkbox(1).checked = false;
  h.checkbox(1).listeners.change();
  assert.equal(h.checkbox(1).checked, true);
  assert.equal(h.requests.filter(r => r.opts.method === 'POST').length, 1);
  resolve({ success: true, data: { enabled: false } });
  await h.flush();
});

const trustRecord = (extra = {}) => record({ trust_status: 'untrusted',
  current_hash: 'a'.repeat(64), trusted_at: null, declares_no_confirm: false,
  can_skip_approval: false, ...extra });
const button = (h, label) => all(h.get('skills-list'), n => n.type === 'button' && n._text === label)[0];

test('nadanie i cofnięcie zaufania: potwierdzenie, wersja i odświeżenie', async () => {
  const h = harness([trustRecord()]);
  await h.click('tab-skills');
  const grant = button(h, 'Ufaj bieżącej wersji');
  grant.listeners.click();
  assert.equal(grant.disabled, true);
  await h.flush();
  assert.ok(h.confirmations[0].includes('folder'));
  assert.ok(h.confirmations[0].includes('a'.repeat(64)));
  assert.ok(h.confirmations[0].includes('nie jest sandbox'));
  const request = h.requests.find(r => r.url.endsWith('/trust'));
  assert.deepEqual(JSON.parse(request.opts.body), { path: 'folder', hash: 'a'.repeat(64) });
  assert.ok(text(h.get('skills-list')).includes('Zaufany — hash zgodny'));
  assert.ok(text(h.get('skills-list')).includes('Każde uruchomienie nadal wymaga zgody'));
  button(h, 'Cofnij zaufanie').listeners.click();
  await h.flush();
  const revoke = h.requests.find(r => r.url.endsWith('/revoke-trust'));
  assert.deepEqual(JSON.parse(revoke.opts.body), { path: 'folder' });
  assert.ok(button(h, 'Ufaj bieżącej wersji'));
});

test('anulowanie potwierdzenia nie wysyła POST', async () => {
  const h = harness([trustRecord()], null, false);
  await h.click('tab-skills');
  button(h, 'Ufaj bieżącej wersji').listeners.click();
  await h.flush();
  assert.equal(h.requests.filter(r => r.opts.method === 'POST').length, 0);
});

test('dev/project/kopia i błąd hasha bez nadawania zaufania', async () => {
  const h = harness([trustRecord({ source: 'dev' }), trustRecord({ source: 'project' }),
    trustRecord({ shadowed: true }), trustRecord({ current_hash: null, trust_status: 'hash_error' })]);
  await h.click('tab-skills');
  assert.equal(button(h, 'Ufaj bieżącej wersji'), undefined);
  assert.equal(button(h, 'Cofnij zaufanie'), undefined);
  assert.ok(text(h.get('skills-list')).includes('Błąd odczytu hasha'));
});

test('konflikt trust zachowuje stan i pokazuje błąd', async () => {
  const h = harness([trustRecord({ trust_status: 'changed' })],
    async () => ({ success: false, error: 'Wersja zmieniona' }));
  await h.click('tab-skills');
  assert.ok(button(h, 'Cofnij zaufanie'));
  button(h, 'Ufaj bieżącej wersji').listeners.click();
  await h.flush();
  assert.ok(text(h.get('banner')).includes('Wersja zmieniona'));
  assert.ok(text(h.get('skills-list')).includes('Zmieniony od zaufania'));
});

test('control/bidi widoczne, tekst HTML pozostaje tekstem', async () => {
  const h = harness([record({ name: 'a\u202Eb', description: '<img>\u0001',
    path: 'x\u202Ey', warnings: ['w\u202Ez'] })]);
  await h.click('tab-skills');
  const result = text(h.get('skills-list'));
  const bs = String.fromCharCode(92);
  assert.ok(result.includes('a' + bs + 'u{202E}b'));
  assert.ok(result.includes('<img>'));
  assert.ok(!result.includes('\u202E'));
  assert.ok(result.includes('w' + bs + 'u{202E}z'));
});
