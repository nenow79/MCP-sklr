// P2d2: work-session tab driven through a DOM stub. Run: node --test tests/test_panel_session_view.cjs
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
    click() { if (!this.disabled && this.listeners.click) this.listeners.click(); },
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

const status = (extra = {}) => ({
  scope: 'instance', session_id: null, execution_blocked: true, running_actions: 0, state: null,
  reason: '', revision: null, checkpoint_state: null, roots: [], checkpoint_available: false,
  resume_available: false, switch_available: false, warning: 'x', ...extra });
const active = (extra = {}) => status({ session_id: 's1', execution_blocked: false, state: 'active',
  revision: 3, checkpoint_state: 'pending', roots: ['sklr'], running_actions: 2, ...extra });
const rootsPayload = () => ({ success: true, data: { count: 2, roots: [
  { alias: 'sklr', valid: true }, { alias: 'old', valid: false }, { alias: 'docs', valid: true }] } });

// server: { status, confirm, startResponse, pauseResponse } - responses are {status, json} or promises of them.
function harness(server) {
  const nodes = new Map();
  const requests = [];
  const confirmations = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const document = { hidden: false, title: '', getElementById: get,
    createElement: node, createTextNode: t => { const n = node(); n.textContent = t; return n; },
    addEventListener() {} };
  const response = (status, json) => ({ status, json: () => Promise.resolve(json) });
  const context = { document, window: { location: { hash: '' }, confirm(question) {
      confirmations.push(question);
      return server.confirmAnswers ? server.confirmAnswers.shift() === true : server.confirm !== false;
    } }, console, history: { replaceState() {} }, setTimeout() { return 1; }, clearTimeout() {},
    fetch(url, opts) {
      const method = opts.method || 'GET';
      const payload = opts.body ? JSON.parse(opts.body) : undefined;
      requests.push({ url, method, payload });
      let out;
      if (url === '/api/work-session' && method === 'GET') {
        out = server.getResponse ? server.getResponse() : { status: 200, json: { success: true, data: server.status } };
      } else if (url === '/api/work-session/checkpoint') {
        out = server.checkpointResponse || { status: 200, json: { success: true, data: { available: false } } };
      } else if (url.startsWith('/api/work-session/resume-preview?')) {
        out = server.previewResponse || { status: 200, json: { success: true, data: {
          session_id: server.status.session_id, revision: server.status.revision,
          cancel_count: 2, checkpoint_missing: false } } };
      } else if (url === '/api/work-session/prepare-resume') {
        out = server.prepareResponse;
        if (server.afterPrepare) server.status = server.afterPrepare;
      } else if (url === '/api/work-session/resume-plan') {
        out = server.planResponse || { status: 200, json: { success: true, data: { available: false } } };
      } else if (url === '/api/work-session/saved') {
        out = server.savedResponse || { status: 200, json: { success: true, data: { sessions: [], truncated: false } } };
      } else if (url === '/api/work-session/recover') {
        out = server.recoverResponse;
      } else if (url === '/api/work-session/start') {
        out = server.startResponse;
      } else if (url === '/api/work-session/end') {
        out = server.endResponse;
      } else if (url === '/api/work-session/pause') {
        out = server.pauseResponse;
      } else if (url.startsWith('/api/roots') && method === 'POST') {
        out = server.rootResponse || { status: 200, json: { success: true, data: {} } };
      } else if (url === '/api/roots') {
        out = server.rootsResponse || { status: 200, json: rootsPayload() };
      } else {
        out = { status: 200, json: { success: true, data: { tasks: [] } } };
      }
      return Promise.resolve(out).then(s => response(s.status, s.json));
    }
  };
  vm.runInNewContext(source, context);
  const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
  const open = async () => { get('tab-session').listeners.click(); await flush(); };
  const click = async id => { get(id).listeners.click(); await flush(); };
  const posts = () => requests.filter(r => r.method === 'POST' && r.url.startsWith('/api/work-session'));
  const gets = () => requests.filter(r => r.url === '/api/work-session' && r.method === 'GET');
  return { get, requests, confirmations, flush, open, click, posts, gets, server };
}

test('jedna lista folderow, start obejmuje dostepne, pauza nieaktywna', async () => {
  const h = harness({ status: status() });
  await h.open();
  assert.equal(h.get('session-view').hidden, false);
  assert.equal(h.get('tasks-view').hidden, true);
  assert(text(h.get('roots-list')).includes('old'));
  assert.equal(all(h.get('roots-list'), n => n.type === 'checkbox').length, 0);
  assert.ok(text(h.get('session-state')).includes('Brak przypiętej sesji'));
  assert.equal(h.get('session-pause').disabled, true);
  assert.equal(h.get('root-editor').disabled, false);
  assert.equal(h.get('session-start').disabled, false);
  assert.equal(h.posts().length, 0);
});

test('start wysyla wybrane foldery po potwierdzeniu i renderuje stan', async () => {
  const h = harness({ status: status(), startResponse: { status: 201, json: { success: true, data: active() } } });
  await h.open();
  await h.click('session-start');
  assert.ok(h.confirmations[0].includes('instancj'));
  assert.deepEqual(h.posts().map(r => [r.url, r.payload]), [['/api/work-session/start', { roots: ['sklr', 'docs'] }]]);
  const shown = text(h.get('session-state'));
  assert.ok(shown.includes('active') && shown.includes('3') && shown.includes('sklr'));
  assert.equal(h.get('session-start').disabled, true);
  assert.equal(h.get('session-pause').disabled, false);
  assert.equal(h.get('session-select').hidden, true);
});

test('odmowa w potwierdzeniu nie wysyla zadania', async () => {
  const h = harness({ status: status(), confirm: false });
  await h.open();
  await h.click('session-start');
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.posts().length, 0);
});

test('stan sesji: licznik, rewizja, checkpoint niedostepny, brak wznowienia i przelaczenia', async () => {
  const h = harness({ status: active({ reason: '<b>x</b>\u202e' }) });
  await h.open();
  const shown = text(h.get('session-state'));
  assert.ok(shown.includes('2'));
  assert.ok(shown.includes('<b>x</b>'));
  assert.ok(shown.includes('niedostępna'));
  assert.ok(shown.includes('Wznowienie jest niedostępne'));
  assert.ok(!/resume|switch/.test(h.requests.map(r => r.url).join(' ')));
  assert.equal(h.get('session-prepare').disabled, true);
  assert.equal(h.get('session-resume-section').hidden, true);
});

test('pauza wysyla dokladne session_id i rewizje', async () => {
  const paused = active({ state: 'pause_requested', execution_blocked: true });
  const h = harness({ status: active(), pauseResponse: { status: 200, json: { success: true, data: paused } } });
  await h.open();
  await h.click('session-pause');
  assert.ok(h.confirmations[0].includes('instancj'));
  assert.deepEqual(h.posts().map(r => [r.url, r.payload]), [
    ['/api/work-session/pause', { session_id: 's1', revision: 3 }]]);
  assert.ok(text(h.get('session-state')).includes('pause_requested'));
  assert.equal(h.get('session-pause').disabled, true);
});

test('pauza niedostepna po pause_requested i paused', async () => {
  for (const state of ['pause_requested', 'paused']) {
    const h = harness({ status: active({ state, execution_blocked: true }) });
    await h.open();
    assert.equal(h.get('session-pause').disabled, true, state);
  }
});

test('konflikt 409 przy pauzie: komunikat i odswiezenie bez ponowienia', async () => {
  const h = harness({ status: active(), pauseResponse: { status: 409, json: { success: false, error: 'konflikt' } } });
  await h.open();
  const before = h.gets().length;
  await h.click('session-pause');
  assert.equal(h.posts().length, 1);
  assert.ok(h.get('banner')._text.includes('zmieni'));
  assert.equal(h.gets().length, before + 1);
});

test('funkcja wylaczona (404): komunikat i brak aktywnych przyciskow', async () => {
  const h = harness({ getResponse: () => ({ status: 404, json: {} }) });
  await h.open();
  assert.ok(text(h.get('session-state')).includes('wyłączone'));
  assert.equal(h.get('session-start').disabled, true);
  assert.equal(h.get('session-pause').disabled, true);
  assert.equal(h.posts().length, 0);
});

test('podwojny klik startu wysyla jedno zadanie', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ status: status(), startResponse: pending });
  await h.open();
  h.get('session-start').listeners.click();
  h.get('session-start').listeners.click();
  await h.flush();
  assert.equal(h.posts().length, 1);
  release({ status: 201, json: { success: true, data: active() } });
  await h.flush();
  assert.equal(h.posts().length, 1);
});

const savedState = () => active({ state: 'paused', execution_blocked: true, running_actions: 0,
  checkpoint_state: 'saved', checkpoint_available: true, checkpoint_content_available: true });
const checkpointRecord = () => ({ available: true, revision: 2, session_revision: 3, saved_at: 'now',
  checkpoint: { goal: '<b>checkpoint</b>\u202e', stage: 'P3a', next_step: 'Wait',
    findings: [{ text: 'finding', source: 'docs/spec.md' }], done_task_ids: [7],
    open_questions: ['question'], uncertain: ['remote outcome uncertain'] } });

test('checkpoint jako tekst, zrodla, pytania i niepewne wyniki', async () => {
  const h = harness({ status: savedState(), checkpointResponse: {
    status: 200, json: { success: true, data: checkpointRecord() } } });
  await h.open();
  const shown = text(h.get('session-checkpoint'));
  assert.ok(shown.includes('<b>checkpoint</b>') && shown.includes('\\u{202E}'));
  assert.ok(shown.includes('docs/spec.md') && shown.includes('remote outcome uncertain'));
  assert.ok(shown.includes('question') && shown.includes('Wait'));
  assert.equal(h.get('session-checkpoint-section').hidden, false);
  assert.equal(h.posts().length, 0);
  await h.click('tab-tasks');
  assert.equal(text(h.get('session-checkpoint')), '');
  assert.equal(h.get('session-checkpoint-section').hidden, true);
});

test('saved bez RAM nie pobiera ani nie deklaruje tresci', async () => {
  const h = harness({ status: { ...savedState(), checkpoint_content_available: false } });
  await h.open();
  assert.ok(text(h.get('session-state')).includes('brak potwierdzonej treści'));
  assert.equal(h.requests.filter(r => r.url.endsWith('/checkpoint')).length, 0);
});

test('spozniona odpowiedz po opuszczeniu zakladki nie przywraca checkpointu', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ status: savedState(), checkpointResponse: pending });
  await h.open();
  await h.click('tab-tasks');
  release({ status: 200, json: { success: true, data: checkpointRecord() } });
  await h.flush();
  assert.equal(text(h.get('session-checkpoint')), '');
  assert.equal(h.get('session-checkpoint-section').hidden, true);
});

test('inna rewizja nie wyswietla starej tresci', async () => {
  const record = { ...checkpointRecord(), session_revision: 99 };
  const h = harness({ status: savedState(), checkpointResponse: {
    status: 200, json: { success: true, data: record } } });
  await h.open();
  assert.ok(!text(h.get('session-checkpoint')).includes('<b>checkpoint</b>'));
  assert.ok(text(h.get('session-checkpoint')).includes('Odśwież'));
});

test('blad odczytu usuwa poprzednia tresc', async () => {
  const h = harness({ status: savedState(), checkpointResponse: {
    status: 200, json: { success: true, data: checkpointRecord() } } });
  await h.open();
  h.server.checkpointResponse = { status: 500, json: {} };
  await h.click('session-refresh');
  assert.ok(!text(h.get('session-checkpoint')).includes('<b>checkpoint</b>'));
  assert.ok(text(h.get('session-checkpoint')).includes('Nie można'));
});

test('odswiezenie to tylko przycisk, bez timerow w zakladce', async () => {
  const h = harness({ status: status() });
  await h.open();
  const before = h.gets().length;
  await h.click('session-refresh');
  assert.equal(h.gets().length, before + 1);
  const code = source.slice(source.indexOf('function refreshSession'), source.indexOf('function initSession'));
  assert.ok(code.length > 0 && !/setTimeout|setInterval/.test(code));
  assert.ok(!/innerHTML/.test(code));
});

const resumable = (extra = {}) => active({ state: 'paused', execution_blocked: true,
  running_actions: 0, checkpoint_available: true, checkpoint_content_available: false,
  resume_available: true, resume_pending: false, ...extra });
const ok = data => ({ status: 200, json: { success: true, data } });
const resumePosts = h => h.posts().filter(r => r.url.endsWith('/prepare-resume'));
const savedButtons = h => all(h.get('session-saved'), n => n.type === 'button');

test('przygotowanie: preview, confirm z liczba, jeden POST i oczekiwanie na model', async () => {
  const waiting = resumable({ revision: 4, resume_pending: true });
  const h = harness({ status: resumable(), prepareResponse: ok({ prepared: true, revision: 4, cancelled_count: 2 }),
    afterPrepare: waiting });
  await h.open();
  assert.equal(h.get('session-prepare').disabled, false);
  assert.equal(h.get('session-resume-section').hidden, false);
  h.get('session-supplement').value = 'new instructions';
  await h.click('session-prepare');
  assert.equal(h.requests.filter(r => r.url.includes('/resume-preview?')).length, 1);
  assert.equal(h.confirmations.length, 1);
  assert.ok(h.confirmations[0].includes('Anulujemy 2'));
  assert.deepEqual(resumePosts(h)[0].payload, { session_id: 's1', revision: 3,
    supplement: 'new instructions', cancel_count: 2, allow_missing_checkpoint: false, confirmed: true });
  assert.equal(h.get('session-supplement').value, '');
  assert.ok(text(h.get('session-state')).includes('Oczekiwanie na model'));
  assert.ok(text(h.get('session-state')).includes('sklr nie budzi modelu'));
  assert.equal(h.get('session-prepare').disabled, true);
});

test('brak checkpointu: osobny confirm przed confirm anulowania', async () => {
  const h = harness({ status: resumable(), previewResponse: ok({ session_id: 's1', revision: 3,
    cancel_count: 0, checkpoint_missing: true }), prepareResponse: ok({ prepared: true }),
    afterPrepare: resumable({ revision: 4, resume_pending: true }), confirmAnswers: [true, true] });
  await h.open();
  await h.click('session-prepare');
  assert.equal(h.confirmations.length, 2);
  assert.ok(h.confirmations[0].includes('bez checkpointu'));
  assert.ok(h.confirmations[1].includes('Anulujemy 0'));
  assert.equal(resumePosts(h)[0].payload.allow_missing_checkpoint, true);
});

test('odmowa ktoregokolwiek confirm nie wysyla POST', async () => {
  for (const answers of [[false], [true, false]]) {
    const h = harness({ status: resumable(), previewResponse: ok({ session_id: 's1', revision: 3,
      cancel_count: 2, checkpoint_missing: true }), confirmAnswers: answers.slice() });
    await h.open();
    h.get('session-supplement').value = 'keep';
    await h.click('session-prepare');
    assert.equal(resumePosts(h).length, 0);
    assert.equal(h.get('session-supplement').value, 'keep');
    assert.equal(h.get('session-prepare').disabled, false);
  }
});

test('przygotowanie niedostepne podczas running/pending/active i bez funkcji', async () => {
  for (const data of [resumable({ running_actions: 1 }), resumable({ resume_pending: true }),
    active({ resume_available: true }), resumable({ resume_available: false })]) {
    const h = harness({ status: data });
    await h.open();
    await h.click('session-prepare');
    assert.equal(h.get('session-prepare').disabled, true);
    assert.equal(h.requests.filter(r => r.url.includes('/resume-preview?')).length, 0);
    assert.equal(resumePosts(h).length, 0);
  }
});

test('niepewny confirm umozliwia tylko nowe jawne przygotowanie', async () => {
  const h = harness({ status: resumable({ state: 'active', resume_recovery_required: true }) });
  await h.open();
  assert.equal(h.get('session-prepare').disabled, false);
  assert.ok(text(h.get('session-state')).includes('Niepewne potwierdzenie'));
});

test('za dlugie uzupelnienie nie pobiera preview', async () => {
  const h = harness({ status: resumable() });
  await h.open();
  h.get('session-supplement').value = 'x'.repeat(3001);
  await h.click('session-prepare');
  assert.equal(h.requests.filter(r => r.url.includes('/resume-preview?')).length, 0);
  assert.ok(h.get('banner')._text.includes('3000'));
});

test('podwojny klik przygotowania: jedno preview i jeden POST', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ status: resumable(), previewResponse: pending, prepareResponse: ok({ prepared: true }),
    afterPrepare: resumable({ revision: 4, resume_pending: true }) });
  await h.open();
  h.get('session-prepare').listeners.click();
  h.get('session-prepare').listeners.click();
  await h.flush();
  assert.equal(h.requests.filter(r => r.url.includes('/resume-preview?')).length, 1);
  assert.equal(h.get('session-refresh').disabled, true);
  release(ok({ session_id: 's1', revision: 3, cancel_count: 2, checkpoint_missing: false }));
  await h.flush();
  assert.equal(resumePosts(h).length, 1);
});

test('stary preview nie wysyla POST ani confirm', async () => {
  const h = harness({ status: resumable(), previewResponse: ok({ session_id: 's1', revision: 99,
    cancel_count: 2, checkpoint_missing: false }) });
  await h.open();
  await h.click('session-prepare');
  assert.equal(resumePosts(h).length, 0);
  assert.equal(h.confirmations.length, 0);
  assert.ok(h.get('banner')._text.includes('innego stanu'));
});

test('konflikt przygotowania odswieza ale nie ponawia POST', async () => {
  const h = harness({ status: resumable(), prepareResponse: { status: 409, json: {
    success: false, error: 'Stan zmieniony' } } });
  await h.open();
  const before = h.gets().length;
  await h.click('session-prepare');
  assert.equal(resumePosts(h).length, 1);
  assert.equal(h.gets().length, before + 1);
  assert.ok(h.get('banner')._text.includes('zmieniony'));
});

test('plan jako tekst bez tokenu i znika po opuszczeniu zakladki', async () => {
  const h = harness({ status: active({ resume_available: true }), planResponse: ok({ available: true,
    session_id: 's1', revision: 3, plan: '<b>plan</b>\u202e', preparation_token: 'must-not-render' }) });
  await h.open();
  const shown = text(h.get('session-plan'));
  assert.ok(shown.includes('<b>plan</b>') && shown.includes('\\u{202E}'));
  assert.ok(!shown.includes('must-not-render'));
  h.get('session-supplement').value = 'RAM';
  await h.click('tab-tasks');
  assert.equal(text(h.get('session-plan')), '');
  assert.equal(h.get('session-supplement').value, '');
  assert.equal(h.get('session-plan-section').hidden, true);
});

test('spozniony plan i spozniony preview po opuszczeniu zakladki sa odrzucane', async () => {
  for (const target of ['plan', 'preview']) {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    const h = harness(target === 'plan' ? { status: active({ resume_available: true }), planResponse: pending }
      : { status: resumable(), previewResponse: pending });
    await h.open();
    if (target === 'preview') { h.get('session-prepare').listeners.click(); await h.flush(); }
    await h.click('tab-tasks');
    release(ok(target === 'plan' ? { available: true, session_id: 's1', revision: 3, plan: 'late-plan' }
      : { session_id: 's1', revision: 3, cancel_count: 2, checkpoint_missing: false }));
    await h.flush();
    assert.equal(text(h.get('session-plan')), '');
    assert.equal(resumePosts(h).length, 0);
    assert.equal(h.confirmations.length, 0);
  }
});

test('bledna rewizja planu i blad odczytu nie pokazuja starej tresci', async () => {
  const h = harness({ status: active({ resume_available: true }), planResponse: ok({ available: true,
    session_id: 's1', revision: 99, plan: 'stale-plan' }) });
  await h.open();
  assert.ok(!text(h.get('session-plan')).includes('stale-plan'));
  assert.ok(text(h.get('session-plan')).includes('Odśwież'));
  h.server.planResponse = { status: 500, json: {} };
  await h.click('session-refresh');
  assert.ok(text(h.get('session-plan')).includes('Nie można'));
});

test('jawny wybor zapisanej sesji przypina zablokowana i zabrania starego klikniecia', async () => {
  const restored = resumable({ ram_lost: true, reason: 'server_restarted' });
  const h = harness({ status: status(), savedResponse: ok({ sessions: [{ session_id: 's1', revision: 3,
    roots: ['<b>sklr</b>'], state: 'paused', reason: 'server_restarted', recoverable: true }], truncated: false }),
    recoverResponse: ok(restored) });
  await h.open();
  assert.equal(h.posts().length, 0);
  assert.ok(text(h.get('session-saved')).includes('<b>sklr</b>'));
  const button = savedButtons(h)[0];
  button.listeners.click(); await h.flush();
  assert.deepEqual(h.posts()[0].payload, { session_id: 's1', revision: 3, confirmed: true });
  assert.ok(h.confirmations[0].includes('RAM został utracony'));
  assert.ok(text(h.get('session-state')).includes('utracony'));
  assert.equal(h.get('session-start').disabled, true);
  assert.equal(h.get('session-pause').disabled, true);
  button.listeners.click(); await h.flush();
  assert.equal(h.posts().length, 1);
});

test('niedostepna zapisana sesja i odmowa wyboru nie wysylaja POST', async () => {
  for (const recoverable of [true, false]) {
    const h = harness({ status: status(), confirm: false, savedResponse: ok({ sessions: [{
      session_id: 's1', revision: 3, roots: ['sklr'], recoverable }], truncated: false }) });
    await h.open();
    const button = savedButtons(h)[0];
    assert.equal(button.disabled, !recoverable);
    button.listeners.click(); await h.flush();
    assert.equal(h.posts().length, 0);
  }
});

test('odswiezenie zmienionej rewizji usuwa uzupelnienie', async () => {
  const h = harness({ status: resumable() });
  await h.open();
  h.get('session-supplement').value = 'old instructions';
  h.server.status = resumable({ revision: 4 });
  await h.click('session-refresh');
  assert.equal(h.get('session-supplement').value, '');
});

test('odswiezenie aktualizuje wspolna liste bez recznego wyboru', async () => {
  const h = harness({ status: status() });
  await h.open();
  assert.equal(h.get('session-start').disabled, false);
  h.server.rootsResponse = { status: 200, json: { success: true, data: { roots: [] } } };
  await h.click('session-refresh');
  assert.equal(h.get('session-start').disabled, true);
  assert.equal(h.posts().length, 0);
});

test('przypiete foldery maja aktualne metadane bez mozliwosci zmiany wyboru', async () => {
  const h = harness({ status: active(), rootsResponse: { status: 200, json: { success: true, data: { roots: [
    { alias: 'sklr', path: 'C:\\projekty\\sklr', mode: 'ro', description: '<projekt>', valid: true }
  ] } } } });
  await h.open();
  const shown = text(h.get('roots-list'));
  assert(shown.includes('C:\\projekty\\sklr') && shown.includes('tylko odczyt') && shown.includes('<projekt>'));
  assert.equal(h.get('root-editor').disabled, true);
  assert(all(h.get('roots-list'), n => n.className === 'root-edit').every(n => n.disabled));
  assert(text(h.get('session-summary')).includes('Sesja aktywna'));
  assert.equal(h.get('session-pause').hidden, false);
  h.server.status = active({ state: 'pause_requested', execution_blocked: true });
  await h.click('session-refresh');
  assert(text(h.get('session-summary')).includes('Trwa wstrzymywanie'));
  assert.equal(h.get('session-pause').hidden, true);
});

test('blad pobrania folderow blokuje start ze starym wyborem', async () => {
  const h = harness({ status: status() });
  await h.open();
  h.server.rootsResponse = { status: 500, json: {} };
  await h.click('session-refresh');
  assert.equal(h.get('session-start').disabled, true);
  assert(text(h.get('roots-list')).includes('niedostępna'));
});


test('paused folder mutation includes identity, blocks double clicks and refreshes membership', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ status: resumable(), rootResponse: pending });
  await h.open();
  assert.equal(h.get('root-editor').disabled, false);
  const save = all(h.get('roots-list'), n => n._text === 'Zapisz')[0];
  save.listeners.click(); save.listeners.click(); await h.flush();
  const mutations = () => h.requests.filter(r => r.method === 'POST' && r.url.startsWith('/api/roots'));
  assert.equal(mutations().length, 1);
  assert.equal(mutations()[0].payload.session_id, h.server.status.session_id);
  assert.equal(mutations()[0].payload.revision, h.server.status.revision);
  assert.equal(h.get('root-editor').disabled, true);
  h.server.status = resumable({ revision: 4, roots: ['sklr', 'docs'] });
  release({ status: 200, json: { success: true, data: {} } }); await h.flush();
  assert.equal(mutations().length, 1);
  assert.equal(h.get('root-editor').disabled, false);
  assert(text(h.get('session-state')).includes('sklr, docs'));
});

test('active and pending sessions reject folder clicks before POST', async () => {
  for (const status of [active(), resumable({ resume_pending: true })]) {
    const h = harness({ status }); await h.open();
    const save = all(h.get('roots-list'), n => n._text === 'Zapisz')[0];
    save.listeners.click(); await h.flush();
    assert.equal(h.get('root-editor').disabled, true);
    assert.equal(h.requests.filter(r => r.method === 'POST').length, 0);
  }
});

test('folder management remains available when work sessions are disabled', async () => {
  const h = harness({ getResponse: () => ({ status: 404, json: {} }) });
  await h.open();
  assert.equal(h.get('root-editor').disabled, false);
  assert.equal(h.get('session-start').disabled, true);
  assert.equal(all(h.get('roots-list'), n => n.className && n.className.startsWith('root-card')).length, 3);
});


test('end confirms discard and enables a fresh start and folder changes', async () => {
  const h = harness({ status: active(), endResponse: ok(status()) });
  await h.open();
  assert.equal(h.get('session-end').hidden, false);
  await h.click('session-end');
  assert.equal(h.posts().length, 1);
  assert.equal(h.posts()[0].url, '/api/work-session/end');
  assert.equal(h.posts()[0].payload.confirmed, true);
  assert.equal(h.posts()[0].payload.session_id, 's1');
  assert(h.confirmations[0].includes('bez zapisywania'));
  assert.equal(h.get('session-start').disabled, false);
  assert.equal(h.get('root-editor').disabled, false);
  assert.equal(h.get('session-end').hidden, true);
});

test('ending blocks folders, resume and repeated end while actions drain', async () => {
  const h = harness({ status: resumable({ state: 'ending', running_actions: 1, resume_recovery_required: true }) });
  await h.open();
  await h.click('session-end');
  assert.equal(h.posts().length, 0);
  assert.equal(h.get('session-end').disabled, true);
  assert.equal(h.get('root-editor').disabled, true);
  assert.equal(h.get('session-prepare').disabled, true);
  assert(text(h.get('session-summary')).includes('Trwa kończenie'));
});

test('end cancellation and stale response never retry automatically', async () => {
  const cancelled = harness({ status: active(), confirm: false });
  await cancelled.open(); await cancelled.click('session-end');
  assert.equal(cancelled.posts().length, 0);
  const stale = harness({ status: active(), endResponse: { status: 409, json: { success: false } } });
  await stale.open(); await stale.click('session-end');
  assert.equal(stale.posts().length, 1);
  assert.equal(stale.get('session-start').disabled, true);
});

test('folder list renders while session status is still pending and edits wait for current status', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ getResponse: () => pending });
  await h.open();
  assert(h.requests.some(r => r.url === '/api/roots'));
  assert(h.requests.some(r => r.url === '/api/work-session'));
  assert(text(h.get('roots-list')).includes('sklr'));
  assert.equal(h.get('session-start').disabled, true);
  assert.equal(h.get('root-editor').disabled, true);
  release(ok(status())); await h.flush();
  assert.equal(h.get('session-start').disabled, false);
  assert.equal(h.get('root-editor').disabled, false);
});

test('session controls render while folder list is still pending', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ status: active(), rootsResponse: pending });
  await h.open();
  assert(text(h.get('session-summary')).includes('Sesja aktywna'));
  assert.equal(h.get('session-pause').disabled, false);
  assert.equal(h.get('session-end').disabled, false);
  assert.equal(h.get('root-editor').disabled, true);
  release({ status: 200, json: rootsPayload() }); await h.flush();
  assert(text(h.get('roots-list')).includes('sklr'));
});
