/* Stabilność szczegółów i list przy pollingu w prawdziwym app.js z atrapami DOM. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/app.js'), 'utf8');

const BS = String.fromCharCode(92);

function makeNode() {
  const node = {
    _text: '', sets: 0, value: '', hidden: false, disabled: false, className: '',
    childNodes: [], listeners: {}, scrollTop: undefined, scrollLeft: undefined,
    appendChild(child) { this.childNodes.push(child); return child; },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  };
  Object.defineProperty(node, 'textContent', {
    enumerable: true,
    get() { return this._text; },
    set(value) { this._text = String(value); this.childNodes = []; this.sets++; }
  });
  return node;
}

function texts(node, out = []) {
  if (node._text) out.push(node._text);
  (node.childNodes || []).forEach((child) => texts(child, out));
  return out;
}

function findAll(node, predicate, out = []) {
  if (predicate(node)) out.push(node);
  (node.childNodes || []).forEach((child) => findAll(child, predicate, out));
  return out;
}

function harness(getTasks, getDetails) {
  const nodes = new Map();
  const document = {
    hidden: false, title: '', listeners: {},
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, makeNode()); return nodes.get(id); },
    createElement() { return makeNode(); },
    createTextNode(text) { const n = makeNode(); n.textContent = text; return n; },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  };
  const timers = new Map();
  let timerId = 0;
  const context = {
    document, window: { location: { hash: '' } }, history: { replaceState() {} }, console,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url) {
      const match = /^\/api\/tasks\/(\d+)$/.exec(url);
      const data = match ? getDetails(Number(match[1])) : { tasks: getTasks() };
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ success: true, data }) });
    }
  };
  vm.runInNewContext(source, context, { filename: 'app.js' });
  const flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); };
  async function poll() {
    assert.ok(timers.size >= 1, 'oczekiwany timer pollingu');
    const [id, timer] = timers.entries().next().value;
    timers.delete(id);
    timer.fn();
    await flush();
  }
  async function click(listName, index) {
    const list = nodes.get(listName);
    assert.ok(list.childNodes[index], 'karta na liście');
    list.childNodes[index].listeners.click();
    await flush();
  }
  return { nodes, flush, poll, click };
}

const summary = (id, extra = {}) => ({
  id, status: 'awaiting_approval', kind: 'file_edit', title: 'Edytuj app:a.txt',
  sensitive: false, offer_trust: false, created_at: '2026-10-13T05:00:00+00:00', ...extra
});

const LONG_DIFF = Array.from({ length: 300 }, (_, i) => (i % 2 ? '+' : '-') + 'linia ' + i).join('\n');

const details = (id, extra = {}, payload = {}) => ({
  ...summary(id), timeout: 10, command: [], approval_reason: 'Zmiana', decision: '',
  started_at: null, finished_at: null, error: '', exit_code: null, duration_s: null,
  stdout: '', stderr: '',
  payload: { root: 'app', paths: ['app:a.txt'], diff: LONG_DIFF, description: '', ...payload },
  ...extra
});

const diffPre = (box) => findAll(box, (n) => n.className === 'diff')[0];

test('polling z tymi samymi danymi nie przebudowuje list ani szczegółów', async () => {
  const h = harness(() => [summary(1)], (id) => details(id));
  await h.flush();
  await h.click('pending-list', 0);
  const list = h.nodes.get('pending-list');
  const box = h.nodes.get('details');
  assert.ok(diffPre(box), 'diff narysowany');
  const detailsSets = box.sets;
  const listSets = list.sets;
  for (let i = 0; i < 3; i++) await h.poll();
  assert.equal(box.sets, detailsSets);
  assert.equal(list.sets, listSets);
});

test('zmiana danych przebudowuje szczegóły i zachowuje scroll diffu', async () => {
  let reason = 'Zmiana';
  const h = harness(() => [summary(1)], (id) => details(id, { approval_reason: reason }));
  await h.flush();
  await h.click('pending-list', 0);
  const box = h.nodes.get('details');
  const pre = diffPre(box);
  pre.scrollTop = 480;
  pre.scrollLeft = 12;
  const before = box.sets;
  reason = 'Inny powód';
  await h.poll();
  assert.equal(box.sets, before + 1);
  const pre2 = diffPre(box);
  assert.notEqual(pre2, pre);
  assert.equal(pre2.scrollTop, 480);
  assert.equal(pre2.scrollLeft, 12);
  assert.ok(texts(box).includes('Inny powód'));
});

test('zmiana wybranego zadania nie dziedziczy scrolla', async () => {
  const h = harness(() => [summary(1), summary(2)], (id) => details(id));
  await h.flush();
  await h.click('pending-list', 0);
  const box = h.nodes.get('details');
  diffPre(box).scrollTop = 300;
  await h.click('pending-list', 1);
  assert.ok(texts(box).includes('Zadanie #2'));
  assert.ok(!diffPre(box).scrollTop);
  await h.click('pending-list', 0);
  assert.ok(texts(box).includes('Zadanie #1'));
  assert.ok(!diffPre(box).scrollTop);
});

test('Tryb folderu tylko gdy payload.mode istnieje, a pola od modelu są escapowane', async () => {
  const h = harness(
    () => [summary(1, { title: 'a\u202Eb' })],
    () => details(1, { title: 'a\u202Eb', approval_reason: 'x\ny' }));
  await h.flush();
  const cardTexts = texts(h.nodes.get('pending-list')).join('\n');
  assert.ok(cardTexts.includes('a' + BS + 'u{202E}b'));
  assert.ok(!/\u202E/.test(cardTexts));
  await h.click('pending-list', 0);
  const view = texts(h.nodes.get('details'));
  assert.ok(!view.includes('Tryb folderu'));
  assert.ok(view.includes('a' + BS + 'u{202E}b'));
  assert.ok(view.includes('x\ny'), 'nowa linia w polu wielowierszowym zachowana');

  const h2 = harness(
    () => [summary(1, { kind: 'root_change' })],
    () => details(1, { kind: 'root_change' }, { mode: 'rw' }));
  await h2.flush();
  await h2.click('pending-list', 0);
  const view2 = texts(h2.nodes.get('details'));
  assert.ok(view2.includes('Tryb folderu') && view2.includes('rw'));
});

test('długie wiersze są na końcu tabeli', async () => {
  const h = harness(() => [summary(1)], (id) => details(id, { error: 'błąd' }));
  await h.flush();
  await h.click('pending-list', 0);
  const classes = findAll(h.nodes.get('details'), (n) => /^row( wide)?$/.test(n.className))
    .map((n) => n.className);
  const firstWide = classes.indexOf('row wide');
  assert.ok(firstWide > 0, 'są zwykłe i długie wiersze');
  assert.ok(classes.slice(firstWide).every((c) => c === 'row wide'));
});

test('kolejność bloków: opis, stdout, stderr, na końcu diff', async () => {
  const h = harness(
    () => [summary(1)],
    (id) => details(id, { stdout: 'wynik', stderr: 'err' }, { description: 'Opis testowy' }));
  await h.flush();
  await h.click('pending-list', 0);
  const view = texts(h.nodes.get('details'));
  const opis = view.findIndex((t) => t.indexOf('Opis od modelu') === 0);
  assert.ok(opis > 0);
  assert.ok(view.indexOf('stdout') > opis);
  assert.ok(view.indexOf('stderr') > view.indexOf('stdout'));
  assert.ok(view.indexOf('Diff') > view.indexOf('stderr'));
  assert.ok(view.includes('Opis testowy') && view.includes('wynik') && view.includes('err'));
});
