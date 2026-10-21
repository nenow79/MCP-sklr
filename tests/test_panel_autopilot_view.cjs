// Delegation UI with a DOM stub; no network or browser process.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/autopilot.js'), 'utf8');

function node() {
  return { value: '', _text: '', children: [], listeners: {}, selectedOptions: [], disabled: false,
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    get textContent() { return this._text; },
    set textContent(value) { this._text = String(value); this.children = []; }
  };
}
function harness(options = {}) {
  const nodes = new Map(), requests = [], windowEvents = {};
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  get('ap-kind').value = 'files'; get('ap-scope-kind').value = 'tree'; get('ap-scope').value = 'docs';
  get('ap-goal').value = 'Approved goal'; get('ap-plan').value = 'Approved plan';
  get('ap-minutes').value = '30'; get('ap-operations').value = '100';
  const state = { session_id: 's', revision: 2, decision_revision: 1, session_state: 'paused', running_actions: 0,
    resume_pending: false, state: 'disabled', running: 0, used: 0, reserved: 0,
    remaining_s: 0, remaining_operations: 0, unresolved: [], ...options.state };
  const context = {
    document: { getElementById: get, createElement: node,
      createTextNode(text) { const item = node(); item.textContent = text; return item; },
      querySelectorAll() { return get('ap-tools').children.map(label => label.children[0]).filter(input => input.checked); } },
    window: { confirm() { return options.confirm !== false; }, addEventListener(name, cb) { windowEvents[name] = cb; } },
    fetch: async (url, init) => {
      const body = init.body ? JSON.parse(init.body) : undefined;
      requests.push({ url, body, method: init.method });
      if (options.failAction && url === '/api/autopilot/revoke') { throw new Error('Connection lost'); }
      let data = state;
      if (url.endsWith('/resources')) { data = [{ key: 'root:app', kind: 'root', name: 'app', path: '<private>', mode: 'rw' }]; }
      if (url.endsWith('/preview')) { data = { preview_id: 'preview-once', ...body }; }
      return { status: 200, ok: true, json: async () => ({ success: true, data }) };
    }
  };
  vm.runInNewContext(source, context);
  const flush = async () => { for (let index = 0; index < 40; index++) await Promise.resolve(); };
  const click = async id => { get(id).listeners.click(); await flush(); };
  return { get, requests, windowEvents, state, click, flush };
}

test('explicit resources/tools, preview required and editing invalidates it', async () => {
  const h = harness(); await h.click('tab-session');
  assert.equal(h.get('ap-editor').disabled, false);
  assert.equal(h.get('ap-approve').disabled, true);
  assert(h.get('ap-tools').children.every(label => !label.children[0].checked));
  h.get('ap-resource').selectedOptions = [{ value: 'root:app' }];
  h.get('ap-tools').children[0].children[0].checked = true;
  await h.click('ap-add'); await h.click('ap-preview');
  const preview = h.requests.find(row => row.url.endsWith('/preview'));
  assert.equal(preview.body.policy.rules[0].root, 'root:app');
  assert.equal(preview.body.limits.duration_s, 1800);
  assert.equal(h.get('ap-approve').disabled, false);
  h.get('ap-editor').listeners.input();
  assert.equal(h.get('ap-approve').disabled, true);
  assert.equal(h.get('ap-preview-content').textContent, '');
});

test('revoke does not retry when response is uncertain and keeps explanation', async () => {
  const h = harness({ failAction: true, state: { contract_id: 'contract', session_state: 'active' } });
  await h.click('tab-session'); await h.click('ap-revoke');
  assert.equal(h.requests.filter(row => row.url.endsWith('/revoke')).length, 1);
  assert(h.get('ap-error').textContent.includes('Connection lost'));
  assert.equal(h.get('ap-editor').disabled, true);
});

test('pending resume prevents new approval; pagehide clears private content', async () => {
  const h = harness({ state: { resume_pending: true } }); await h.click('tab-session');
  assert.equal(h.get('ap-editor').disabled, true);
  assert.equal(h.get('ap-reconfirm').disabled, true);
  h.windowEvents.pagehide();
  assert.equal(h.get('ap-plan').value, '');
  assert.equal(h.get('ap-preview-content').textContent, '');
  assert.equal(h.requests.filter(row => row.method === 'POST').length, 0);
});

test('cancelled confirmation cannot send a decision', async () => {
  const h = harness({ confirm: false, state: { contract_id: 'contract' } });
  await h.click('tab-session'); await h.click('ap-revoke');
  assert.equal(h.requests.filter(row => row.method === 'POST').length, 0);
});
