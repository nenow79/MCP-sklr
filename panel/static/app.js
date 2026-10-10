/* Panel zatwierdzeń zadań. Bez zewnętrznych zasobów; cała treść z serwera trafia do DOM
   przez textContent (CSP: script-src 'self', brak inline). */
(function () {
  "use strict";

  var POLL_MS = 1500;
  var BASE_TITLE = "Vibecoder - zatwierdzenia";

  var state = {
    selectedId: null,
    knownPending: null,   // Set id zadań awaiting_approval widzianych wcześniej (null = pierwsze ładowanie)
    soundEnabled: false,
    audioCtx: null,
    pollTimer: null,
    stopped: false,
    busy: {}              // id zadania -> true, gdy trwa wysyłanie decyzji
  };

  var $ = function (id) { return document.getElementById(id); };

  function el(tag, opts, children) {
    var node = document.createElement(tag);
    opts = opts || {};
    if (opts.className) { node.className = opts.className; }
    if (opts.text !== undefined && opts.text !== null) { node.textContent = String(opts.text); }
    if (opts.type) { node.type = opts.type; }
    if (opts.onclick) { node.addEventListener("click", opts.onclick); }
    (children || []).forEach(function (child) { if (child) { node.appendChild(child); } });
    return node;
  }

  function fmtTime(iso) {
    if (!iso) { return "-"; }
    var d = new Date(iso);
    return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
  }

  // --- API ---

  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (json) {
        return { status: resp.status, json: json };
      });
    });
  }

  // --- komunikaty ---

  function showBanner(text, isInfo) {
    var banner = $("banner");
    banner.textContent = text;
    banner.className = isInfo ? "banner info" : "banner";
    banner.hidden = false;
  }

  function hideBanner() { $("banner").hidden = true; }

  function authFailed() {
    state.stopped = true;
    showBanner("Sesja nieważna. Otwórz ponownie link z konsoli serwera sklr " +
               "(http://127.0.0.1:8102/#token=...).", false);
  }

  // --- powiadomienia ---

  function beep() {
    if (!state.soundEnabled) { return; }
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { return; }
      state.audioCtx = state.audioCtx || new Ctx();
      var ctx = state.audioCtx;
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.value = 0.08;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    } catch (e) { /* dźwięk jest opcjonalny */ }
  }

  function systemNotify(task) {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") { return; }
    try {
      new Notification("Zadanie #" + task.id + " czeka na decyzję",
                       { body: task.skill + " / " + task.script });
    } catch (e) { /* powiadomienia są opcjonalne */ }
  }

  function enableNotifications() {
    state.soundEnabled = true;
    beep();
    var btn = $("notify-btn");
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().then(function () { btn.textContent = "Powiadomienia włączone"; });
    } else {
      btn.textContent = "Powiadomienia włączone";
    }
    btn.disabled = true;
  }

  function updateTitleAndBadge(pendingCount) {
    document.title = (pendingCount > 0 ? "(" + pendingCount + ") " : "") + BASE_TITLE;
    var badge = $("pending-badge");
    badge.hidden = pendingCount === 0;
    badge.textContent = "Czeka: " + pendingCount;
  }

  // --- render listy ---

  function statusBadge(status) {
    return el("span", { className: "badge status-" + status, text: status });
  }

  function taskCard(task, isPending) {
    var cls = "task-card" + (isPending ? " is-pending" : "") +
              (task.id === state.selectedId ? " selected" : "");
    var card = el("div", { className: cls }, [
      el("div", { className: "task-head" }, [
        el("span", { className: "task-title", text: "#" + task.id + "  " + task.skill }),
        statusBadge(task.status)
      ]),
      el("div", { className: "task-meta",
                  text: task.script + (task.args && task.args.length ? " " + task.args.join(" ") : "") }),
      el("div", { className: "task-meta", text: fmtTime(task.created_at) })
    ]);
    card.addEventListener("click", function () { selectTask(task.id); });

    var actions = el("div", { className: "task-actions" });
    if (task.status === "awaiting_approval") {
      actions.appendChild(actionButton(task, "Zezwól", "btn btn-allow", "decision", "allow"));
      if (task.offer_trust) {
        actions.appendChild(actionButton(task, "Ufaj skillowi", "btn btn-trust", "decision", "trust"));
      }
      actions.appendChild(actionButton(task, "Odrzuć", "btn btn-deny", "decision", "deny"));
      actions.appendChild(actionButton(task, "Anuluj", "btn", "cancel", null));
    } else if (task.status === "queued" || task.status === "running") {
      actions.appendChild(actionButton(task, "Anuluj", "btn", "cancel", null));
    }
    if (actions.childNodes.length) { card.appendChild(actions); }
    return card;
  }

  function actionButton(task, label, className, kind, decision) {
    var btn = el("button", { type: "button", className: className, text: label });
    btn.disabled = !!state.busy[task.id];
    btn.addEventListener("click", function (event) {
      event.stopPropagation();
      act(task, kind, decision);
    });
    return btn;
  }

  function renderList(container, tasks, isPending, emptyText) {
    container.textContent = "";
    if (!tasks.length) {
      container.appendChild(el("p", { className: "muted", text: emptyText }));
      return;
    }
    tasks.forEach(function (task) { container.appendChild(taskCard(task, isPending)); });
  }

  function handleNewPending(pending) {
    var current = {};
    pending.forEach(function (t) { current[t.id] = t; });
    if (state.knownPending !== null) {
      var fresh = pending.filter(function (t) { return !state.knownPending[t.id]; });
      if (fresh.length) {
        beep();
        fresh.forEach(systemNotify);
      }
    }
    state.knownPending = current;
  }

  // --- odświeżanie ---

  function refresh() {
    var filter = $("status-filter").value;
    return api("GET", "/api/tasks?limit=100").then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (res.status !== 200 || !res.json.success) {
        showBanner("Błąd pobierania zadań (HTTP " + res.status + ").", false);
        return;
      }
      hideBanner();
      var tasks = res.json.data.tasks;
      var pending = tasks.filter(function (t) { return t.status === "awaiting_approval"; });
      var others = tasks.filter(function (t) {
        return t.status !== "awaiting_approval" && (!filter || t.status === filter);
      });
      handleNewPending(pending);
      updateTitleAndBadge(pending.length);
      renderList($("pending-list"), pending, true, "Brak zadań oczekujących na decyzję.");
      renderList($("other-list"), others, false, "Brak zadań.");
      if (state.selectedId !== null) { return loadDetails(state.selectedId); }
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    });
  }

  function schedule() {
    if (state.stopped) { return; }
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(tick, POLL_MS);
  }

  function tick() {
    if (state.stopped) { return; }
    if (document.hidden) { schedule(); return; }
    refresh().then(schedule, schedule);
  }

  // --- szczegóły ---

  function selectTask(id) {
    state.selectedId = id;
    refresh();
  }

  function row(dl, label, value) {
    dl.appendChild(el("dt", { text: label }));
    dl.appendChild(el("dd", { text: value === null || value === undefined || value === "" ? "-" : value }));
  }

  function block(parent, title, text) {
    if (!text) { return; }
    parent.appendChild(el("h3", { text: title }));
    parent.appendChild(el("pre", { text: text }));
  }

  function renderDetails(task) {
    var box = $("details");
    box.textContent = "";
    box.appendChild(el("h3", { text: "Zadanie #" + task.id }));
    box.appendChild(statusBadge(task.status));
    var dl = el("dl");
    row(dl, "Skill", task.skill + " (" + task.source + ")");
    row(dl, "Skrypt", task.script);
    row(dl, "Argumenty", task.args && task.args.length ? JSON.stringify(task.args) : "(brak)");
    row(dl, "Timeout", task.timeout + " s");
    row(dl, "Katalog", task.skill_dir);
    row(dl, "Komenda", task.command && task.command.length ? JSON.stringify(task.command) : "-");
    row(dl, "Hash skilla", task.skill_hash);
    row(dl, "Powód pytania", task.approval_reason);
    row(dl, "Decyzja", task.decision);
    row(dl, "Utworzone", fmtTime(task.created_at));
    row(dl, "Start", fmtTime(task.started_at));
    row(dl, "Koniec", fmtTime(task.finished_at));
    row(dl, "Kod wyjścia", task.exit_code);
    row(dl, "Czas trwania", task.duration_s === null || task.duration_s === undefined ? null : task.duration_s + " s");
    row(dl, "Błąd", task.error);
    box.appendChild(dl);
    block(box, "Podgląd skryptu", task.script_preview);
    block(box, "stdout" + (task.stdout_truncated ? " (obcięty)" : ""), task.stdout);
    block(box, "stderr" + (task.stderr_truncated ? " (obcięty)" : ""), task.stderr);
  }

  function loadDetails(id) {
    return api("GET", "/api/tasks/" + encodeURIComponent(id)).then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (res.status === 200 && res.json.success) { renderDetails(res.json.data); }
    });
  }

  // --- akcje ---

  function act(task, kind, decision) {
    var path = "/api/tasks/" + encodeURIComponent(task.id) + (kind === "cancel" ? "/cancel" : "/decision");
    var body = kind === "cancel" ? {} : { decision: decision };
    state.busy[task.id] = true;
    refresh();
    api("POST", path, body).then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (res.status === 409) {
        showBanner("Zadanie #" + task.id + " nie czeka już na decyzję (mogło wygasnąć lub zostać anulowane).", true);
      } else if (res.status !== 200 || !res.json.success) {
        showBanner((res.json && res.json.error) || ("Błąd HTTP " + res.status), false);
      } else {
        hideBanner();
      }
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    }).then(function () {
      delete state.busy[task.id];
      refresh();
    });
  }

  // --- start ---

  function exchangeToken() {
    var hash = window.location.hash || "";
    var match = /(?:^#|&)token=([^&]+)/.exec(hash);
    if (!match) { return Promise.resolve(); }
    var token = decodeURIComponent(match[1]);
    // fragment znika z paska adresu od razu (token nie zostaje w historii ani w zakładkach)
    history.replaceState(null, "", window.location.pathname + window.location.search);
    return api("POST", "/api/session", { token: token }).then(function (res) {
      if (res.status !== 200) { authFailed(); }
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    });
  }

  function init() {
    $("notify-btn").addEventListener("click", enableNotifications);
    $("status-filter").addEventListener("change", function () { refresh(); });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !state.stopped) { tick(); }
    });
    exchangeToken().then(function () {
      if (!state.stopped) { tick(); }
    });
  }

  init();
})();
