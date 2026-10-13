/* Widok secret_reveal w prawdziwym app.js z atrapami DOM; bez serwera i bibliotek. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/app.js'), 'utf8');

const SECRET = 'SENTINEL-PANEL-SECRET';
const BS = String.fromCharCode(92);

function makeNode() {
  const node = {
    _text: '', value: '', hidden: false, disabled: false, className: '',
    childNodes: [], listeners: {},
    appendChild(child) { this.childNodes.push(child); return child; },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  };
  // Jak w DOM: przypisanie textContent usuwa dzieci.
  Object.defineProperty(node, 'textContent', {
    enumerable: true,
    get() { return this._text; },
    set(value) { this._text = String(value); this.childNodes = []; }
  });
  return node;
}

function texts(node, out = []) {
  if (node._text) out.push(node._text);
  (node.childNodes || []).forEach((child) => texts(child, out));
  return out;
}

function harness(summary, details) {
  const nodes = new Map();
  const document = {
    hidden: false, title: '', listeners: {},
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, makeNode()); return nodes.get(id); },
    createElement() { return makeNode(); },
    createTextNode(text) { const n = makeNode(); n.textContent = text; return n; },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  };
  const urls = [];
  const context = {
    document, window: { location: { hash: '' } }, history: { replaceState() {} }, console,
    setTimeout() { return 1; }, clearTimeout() {},
    fetch(url) {
      urls.push(url);
      const data = url.indexOf('/api/tasks/') === 0 ? details : { tasks: [summary] };
      return Promise.resolve({
        status: 200, json: () => Promise.resolve({ success: true, data })
      });
    }
  };
  vm.runInNewContext(source, context, { filename: 'app.js' });
  const flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); };
  async function openDetails() {
    await flush();
    const list = nodes.get('pending-list');
    assert.ok(list && list.childNodes.length === 1, 'karta zadania na liście');
    list.childNodes[0].listeners.click();
    await flush();
    return {
      list: texts(nodes.get('pending-list')),
      details: texts(nodes.get('details'))
    };
  }
  return { nodes, urls, openDetails };
}

const summary = (extra = {}) => ({
  id: 7, status: 'awaiting_approval', kind: 'secret_reveal', title: 'Ujawnij wskazany sekret',
  sensitive: true, offer_trust: false, created_at: '2026-10-13T05:00:00+00:00', ...extra
});

const secretDetails = (payload, extra = {}) => ({
  ...summary(extra), timeout: 10, command: [], approval_reason: 'Powód pytania',
  decision: '', started_at: null, finished_at: null, error: '',
  stdout: 'STDOUT-' + SECRET, stderr: 'STDERR-' + SECRET, script_preview: 'PREVIEW-' + SECRET,
  secret_result: { state: 'available', data: { content: SECRET } },
  payload: {
    root: 'app', filepath: '.env', selector: { key: 'TOKEN' }, max_chars: 12000,
    description: '', diff: 'DIFF-' + SECRET, content: SECRET, ...payload
  }
});

test('widok secret_reveal pokazuje metadane i ostrzeżenie, bez wartości i diffu', async () => {
  const h = harness(summary(), secretDetails({ description: 'Potrzebne do testu' }));
  const view = await h.openDetails();
  const joined = view.details.join('\n');
  assert.ok(joined.includes('Po zgodzie wynik trafi wyłącznie do klienta MCP przez get_task'));
  assert.ok(joined.includes('Retencja po stronie klienta lub modelu jest poza kontrolą sklr.'));
  for (const expected of ['secret_reveal', 'app', '.env', 'klucz: TOKEN', '12000', 'Potrzebne do testu']) {
    assert.ok(view.details.includes(expected), expected);
  }
  assert.ok(view.details.includes('Plik') && view.details.includes('Selektor'));
  assert.doesNotMatch(joined, /Diff|stdout|stderr|Podgląd skryptu|DIFF-|STDOUT-|STDERR-|PREVIEW-/);
  assert.ok(!joined.includes(SECRET) && !view.list.join('\n').includes(SECRET));
  assert.doesNotMatch(joined + view.list.join('\n'), /Ufaj/);
});

test('zakres linii jest pokazany jawnie, a oferta zaufania nigdy nie pojawia się', async () => {
  const h = harness(
    summary({ offer_trust: true }),
    secretDetails({ selector: { start_line: 3, end_line: 5 } }, { offer_trust: true }));
  const view = await h.openDetails();
  assert.ok(view.details.includes('linie 3-5'));
  assert.doesNotMatch(view.list.join('\n') + view.details.join('\n'), /Ufaj/);
  assert.ok(view.list.includes('Zezwól') && view.list.includes('Odrzuć'));
});

test('znaki sterujące i kierunkowe w nowym widoku są escapowane jawnie', async () => {
  const filepath = 'dir/a\u202Eb\u200Bc\u0007d\u2028e\uD800f';
  const expectedPath = 'dir/a' + BS + 'u{202E}b' + BS + 'u{200B}c' + BS + 'u{0007}d' +
    BS + 'u{2028}e' + BS + 'u{D800}f';
  const h = harness(summary(), secretDetails({
    filepath, selector: { key: 'TO\u202EKEN' }, description: 'opis\u202Etekst'
  }, { title: 'Ujawnij\u200B' }));
  const view = await h.openDetails();
  assert.ok(view.details.includes(expectedPath));
  assert.ok(view.details.includes('klucz: TO' + BS + 'u{202E}KEN'));
  assert.ok(view.details.includes('opis' + BS + 'u{202E}tekst'));
  assert.ok(view.details.includes('Ujawnij' + BS + 'u{200B}'));
  assert.doesNotMatch(view.details.join('\n'), /[\u202E\u200B\u0007\u2028\uD800]/);
});

test('inne rodzaje zadań zachowują dotychczasowy widok z diffem', async () => {
  const edit = summary({ kind: 'file_edit', sensitive: false, title: 'Edytuj app:a.txt' });
  const details = {
    ...edit, timeout: 10, command: [], approval_reason: 'Zmiana', decision: '',
    started_at: null, finished_at: null, error: '', stdout: '', stderr: '',
    payload: { root: 'app', paths: ['app:a.txt'], diff: '-stara\n+nowa', description: '' }
  };
  const view = await harness(edit, details).openDetails();
  const joined = view.details.join('\n');
  assert.ok(joined.includes('Diff') && view.details.includes('+nowa'));
  assert.ok(!joined.includes('Po zgodzie wynik trafi wyłącznie do klienta MCP'));
});
