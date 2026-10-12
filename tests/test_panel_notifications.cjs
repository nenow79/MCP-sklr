/* Testy prawdziwego app.js z atrapami przeglądarki; bez bibliotek i bez serwera. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../panel/static/app.js'), 'utf8');

function harness({ hidden = true, permission = 'granted', requestResult = 'granted' } = {}) {
  const nodes = new Map();
  function node() {
    return {
      textContent: '', value: '', hidden: false, disabled: false, childNodes: [], listeners: {},
      appendChild(child) { this.childNodes.push(child); return child; },
      addEventListener(name, handler) { this.listeners[name] = handler; }
    };
  }
  const document = {
    hidden, title: '', listeners: {},
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
    createElement: node, createTextNode(text) { const n = node(); n.textContent = text; return n; },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  };
  const timers = new Map();
  let timerId = 0;
  const requests = [], notices = [], audio = { resumes: 0, plays: 0 };
  function Notification(title, options) { notices.push({ title, options }); }
  Notification.permission = permission;
  Notification.requestPermission = () => {
    Notification.permission = requestResult;
    return Promise.resolve(requestResult);
  };
  class AudioContext {
    constructor() { this.state = 'suspended'; this.currentTime = 0; this.destination = {}; }
    resume() { audio.resumes++; this.state = 'running'; return Promise.resolve(); }
    createOscillator() {
      return { frequency: {}, connect() {}, start() { audio.plays++; }, stop() {} };
    }
    createGain() { return { gain: {}, connect() {} }; }
  }
  const context = {
    document, Notification, window: { AudioContext, location: { hash: '' } },
    history: { replaceState() {} }, console,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url) {
      return new Promise((resolve, reject) => requests.push({ url, resolve, reject }));
    }
  };
  vm.runInNewContext(source, context, { filename: 'app.js' });
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  async function reply(tasks = [], status = 200) {
    const request = requests.shift();
    assert.ok(request, 'oczekiwane żądanie API');
    request.resolve({ status, json: () => Promise.resolve({ success: status === 200, data: { tasks } }) });
    await flush();
  }
  function poll() {
    assert.equal(timers.size, 1);
    const [id, timer] = timers.entries().next().value;
    timers.delete(id); timer.fn();
  }
  function visibility(value) {
    document.hidden = value;
    document.listeners.visibilitychange();
  }
  return { document, nodes, requests, notices, timers, audio, flush, reply, poll, visibility };
}

const pending = (id, extra = {}) => ({
  id, status: 'awaiting_approval', kind: 'skill_script', skill: 'hello-skill',
  script: 'scripts/hello.py', args: [], created_at: '', ...extra
});

test('ukryta karta pobiera zadania, aktualizuje tytuł i nie renderuje list', async () => {
  const h = harness();
  await h.flush();
  await h.reply([]);
  assert.equal([...h.timers.values()][0].delay, 5000);
  h.nodes.get('notify-btn').listeners.click();
  await h.flush();
  assert.equal(h.audio.resumes, 1);
  h.poll();
  await h.reply([pending(1)]);
  assert.match(h.document.title, /^\(1\)/);
  assert.equal(h.notices.length, 1);
  assert.equal(h.audio.plays, 2); // kliknięcie i nowe zadanie
  assert.equal(h.nodes.has('pending-list'), false);
  h.poll();
  await h.reply([pending(1)]);
  assert.equal(h.notices.length, 1);
  assert.equal(h.audio.plays, 2);
  h.visibility(false);
  await h.reply([pending(1)]);
  assert.ok(h.nodes.get('pending-list').childNodes.length);
  assert.equal([...h.timers.values()][0].delay, 1500);
  assert.equal(h.notices.length, 1);
});

test('zmiany widoczności i akcje UI nie mnożą żądań ani timerów', async () => {
  const h = harness();
  await h.flush();
  assert.equal(h.requests.length, 1);
  h.visibility(false);
  h.visibility(true);
  h.visibility(false);
  h.nodes.get('status-filter').listeners.change();
  assert.equal(h.requests.length, 1);
  await h.reply([]);
  assert.equal(h.timers.size, 1);
  assert.equal([...h.timers.values()][0].delay, 1500);
  h.visibility(true);
  assert.equal(h.timers.size, 0);
  assert.equal(h.requests.length, 1);
  await h.reply([]);
  assert.equal(h.timers.size, 1);
  assert.equal([...h.timers.values()][0].delay, 5000);
});

test('odmowa uprawnień nie jest przedstawiana jako włączone powiadomienia', async () => {
  const h = harness({ permission: 'default', requestResult: 'denied' });
  await h.flush();
  await h.reply([]);
  h.nodes.get('notify-btn').listeners.click();
  await h.flush();
  assert.match(h.nodes.get('notify-btn').textContent, /powiadomienia zablokowane/);
  assert.equal(h.audio.resumes, 1);
  h.poll();
  await h.reply([pending(2)]);
  assert.equal(h.notices.length, 0);
  assert.match(h.document.title, /^\(1\)/);
});

test('powiadomienia innych rodzajów mają tytuł zadania i stabilny tag', async () => {
  const h = harness();
  await h.flush();
  await h.reply([]);
  h.poll();
  await h.reply([pending(3, { kind: 'root_change', title: 'Dodaj folder', skill: undefined, script: undefined })]);
  assert.equal(h.notices[0].options.body, 'Dodaj folder');
  assert.equal(h.notices[0].options.tag, 'vibecoder-task-3');
});

test('błąd sieci nie zatrzymuje pollingu, 401 zatrzymuje', async () => {
  const h = harness();
  await h.flush();
  h.requests.shift().reject(new Error('offline'));
  await h.flush();
  assert.equal(h.timers.size, 1);
  h.poll();
  await h.reply([], 401);
  assert.equal(h.timers.size, 0);
  h.visibility(false);
  assert.equal(h.requests.length, 0);
});
