/* Panel zatwierdzeń zadań. Bez zewnętrznych zasobów; cała treść z serwera trafia do DOM
   przez textContent (CSP: script-src 'self', brak inline). */
(function () {
  "use strict";

  var POLL_MS = 1500;
  var BACKGROUND_POLL_MS = 5000;
  var BASE_TITLE = "Vibecoder - zatwierdzenia";

  var state = {
    selectedId: null,
    knownPending: null,   // Set id zadań awaiting_approval widzianych wcześniej (null = pierwsze ładowanie)
    soundEnabled: false,
    audioCtx: null,
    pollTimer: null,
    refreshPromise: null, // wspólne żądanie dla pollingu i akcji UI
    tickPromise: null,    // tylko jedna pętla planuje następny timer
    stopped: false,
    tab: "tasks",
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
      function play() {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = 880;
        gain.gain.value = 0.08;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.25);
      }
      if (ctx.state === "suspended") {
        ctx.resume().then(play).catch(function () { /* przeglądarka blokuje audio */ });
      } else {
        play();
      }
    } catch (e) { /* dźwięk jest opcjonalny */ }
  }

  function systemNotify(task) {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") { return; }
    try {
      var isSkill = !task.kind || task.kind === "skill_script";
      var body = isSkill ? (task.skill || "Skill") + " / " + (task.script || "")
        : (task.title || task.kind || "Zadanie");
      new Notification("Zadanie #" + task.id + " czeka na decyzję",
                       { body: body, tag: "vibecoder-task-" + task.id });
    } catch (e) { /* powiadomienia są opcjonalne */ }
  }

  function enableNotifications() {
    state.soundEnabled = true;
    beep();
    var btn = $("notify-btn");
    function permissionLabel(permission) {
      btn.textContent = permission === "granted" ? "Dźwięk i powiadomienia włączone"
        : "Dźwięk włączony; powiadomienia zablokowane";
    }
    btn.disabled = true;
    if (typeof Notification === "undefined") {
      btn.textContent = "Dźwięk włączony; powiadomienia niedostępne";
    } else if (Notification.permission === "default") {
      btn.textContent = "Dźwięk włączony; oczekiwanie na zgodę";
      Notification.requestPermission().then(permissionLabel).catch(function () {
        btn.textContent = "Dźwięk włączony; błąd uprawnień powiadomień";
      });
    } else {
      permissionLabel(Notification.permission);
    }
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

  function kindBadges(task) {
    var out = [];
    if (task.kind && task.kind !== "skill_script") {
      out.push(el("span", { className: "badge badge-kind", text: task.kind }));
    }
    if (task.sensitive) {
      out.push(el("span", { className: "badge badge-sensitive", text: "wrażliwe" }));
    }
    return out;
  }

  function taskCard(task, isPending) {
    var cls = "task-card" + (isPending ? " is-pending" : "") +
              (task.id === state.selectedId ? " selected" : "");
    var isSkill = !task.kind || task.kind === "skill_script";
    var titleText = "#" + task.id + "  " + (isSkill ? task.skill : (task.title || task.kind));
    var metaText = isSkill
      ? task.script + (task.args && task.args.length ? " " + task.args.join(" ") : "")
      : task.kind;
    var card = el("div", { className: cls }, [
      el("div", { className: "task-head" }, [
        el("span", { className: "task-title" }, kindBadges(task).concat([document.createTextNode(titleText)])),
        statusBadge(task.status)
      ]),
      el("div", { className: "task-meta", text: metaText }),
      el("div", { className: "task-meta", text: fmtTime(task.created_at) })
    ]);
    card.addEventListener("click", function () { selectTask(task.id); });

    var actions = el("div", { className: "task-actions" });
    if (task.status === "awaiting_approval") {
      actions.appendChild(actionButton(task, "Zezwól", "btn btn-allow", "decision", "allow"));
      if (task.offer_trust && !task.sensitive && isSkill) {
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
    if (state.stopped) { return Promise.resolve(); }
    if (state.refreshPromise) { return state.refreshPromise; }
    state.refreshPromise = api("GET", "/api/tasks?limit=100").then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (res.status !== 200 || !res.json.success) {
        showBanner("Błąd pobierania zadań (HTTP " + res.status + ").", false);
        return;
      }
      hideBanner();
      var tasks = res.json.data.tasks;
      var filter = $("status-filter").value;
      var pending = tasks.filter(function (t) { return t.status === "awaiting_approval"; });
      var others = tasks.filter(function (t) {
        return t.status !== "awaiting_approval" && (!filter || t.status === filter);
      });
      handleNewPending(pending);
      updateTitleAndBadge(pending.length);
      if (document.hidden) { return; } // powiadomienia działają, kosztowny DOM czeka na powrót
      renderList($("pending-list"), pending, true, "Brak zadań oczekujących na decyzję.");
      renderList($("other-list"), others, false, "Brak zadań.");
      if (state.selectedId !== null) { return loadDetails(state.selectedId); }
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    }).then(function () {
      state.refreshPromise = null;
    });
    return state.refreshPromise;
  }

  function schedule() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    if (state.stopped) { return; }
    state.pollTimer = setTimeout(tick, document.hidden ? BACKGROUND_POLL_MS : POLL_MS);
  }

  function tick() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    if (state.stopped) { return Promise.resolve(); }
    if (state.tickPromise) { return state.tickPromise; }
    state.tickPromise = refresh().then(function () {
      state.tickPromise = null;
      schedule();
    }, function () {
      state.tickPromise = null;
      schedule();
    });
    return state.tickPromise;
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

  function diffBlock(parent, text) {
    if (!text) { return; }
    parent.appendChild(el("h3", { text: "Diff" }));
    var pre = el("pre", { className: "diff" });
    String(text).split("\n").forEach(function (line, index, all) {
      var cls = line.charAt(0) === "+" ? "diff-add" : (line.charAt(0) === "-" ? "diff-del" : "");
      var span = el("span", { className: cls, text: line });
      pre.appendChild(span);
      if (index < all.length - 1) { pre.appendChild(document.createTextNode("\n")); }
    });
    parent.appendChild(pre);
  }

  // Widoczne escapowanie znaków sterujących/kierunkowych tylko w widoku secret_reveal.
  // Pełna kontrola innych rodzajów zadań pozostaje w paczce D.
  var HIDDEN_CHARS_RE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Cs}]/gu;

  function visibleText(value) {
    var text = value === null || value === undefined ? "" : String(value);
    return text.replace(HIDDEN_CHARS_RE, function (ch) {
      var hex = ch.codePointAt(0).toString(16).toUpperCase();
      while (hex.length < 4) { hex = "0" + hex; }
      return String.fromCharCode(92) + "u{" + hex + "}";
    });
  }

  function renderSecretDetails(box, task) {
    var payload = task.payload || {};
    var selector = payload.selector || {};
    var hasKey = selector.key !== undefined && selector.key !== null;
    var selectorText = hasKey ? "klucz: " + visibleText(selector.key)
      : "linie " + visibleText(selector.start_line) + "-" + visibleText(selector.end_line);
    box.appendChild(el("h3", { text: "Zadanie #" + task.id }));
    kindBadges(task).forEach(function (badge) { box.appendChild(badge); });
    box.appendChild(statusBadge(task.status));
    box.appendChild(el("div", { className: "secret-warning", text:
      "Po zgodzie wynik trafi wyłącznie do klienta MCP przez get_task, nie do panelu. " +
      "Retencja po stronie klienta lub modelu jest poza kontrolą sklr." }));
    var dl = el("dl");
    row(dl, "Rodzaj", task.kind);
    row(dl, "Tytuł", visibleText(task.title));
    row(dl, "Folder (root)", visibleText(payload.root));
    row(dl, "Plik", visibleText(payload.filepath));
    row(dl, "Selektor", selectorText);
    row(dl, "Limit znaków", visibleText(payload.max_chars));
    row(dl, "Powód pytania", visibleText(task.approval_reason));
    row(dl, "Decyzja", visibleText(task.decision));
    row(dl, "Utworzone", fmtTime(task.created_at));
    row(dl, "Start", fmtTime(task.started_at));
    row(dl, "Koniec", fmtTime(task.finished_at));
    row(dl, "Błąd", visibleText(task.error));
    box.appendChild(dl);
    if (payload.description) {
      box.appendChild(el("div", { className: "from-model" }, [
        el("div", { text: "Opis od modelu (niezweryfikowany, nie jest częścią zatwierdzanej treści):" }),
        el("pre", { text: visibleText(payload.description) })
      ]));
    }
  }

  function renderDetails(task) {
    var box = $("details");
    box.textContent = "";
    if (task.kind === "secret_reveal") {
      renderSecretDetails(box, task);
      return;
    }
    var isSkill = !task.kind || task.kind === "skill_script";
    box.appendChild(el("h3", { text: "Zadanie #" + task.id }));
    kindBadges(task).forEach(function (badge) { box.appendChild(badge); });
    box.appendChild(statusBadge(task.status));
    var dl = el("dl");
    if (isSkill) {
      row(dl, "Skill", task.skill + " (" + task.source + ")");
      row(dl, "Skrypt", task.script);
      row(dl, "Argumenty", task.args && task.args.length ? JSON.stringify(task.args) : "(brak)");
      row(dl, "Katalog", task.skill_dir);
      row(dl, "Hash skilla", task.skill_hash);
    } else {
      var payload = task.payload || {};
      row(dl, "Rodzaj", task.kind);
      row(dl, "Tytuł", task.title);
      row(dl, "Folder (root)", payload.root);
      row(dl, "Tryb folderu", payload.mode);
      row(dl, "Katalog roboczy", payload.cwd);
      row(dl, "Ścieżka", payload.path);
      row(dl, "Ścieżki", payload.paths && payload.paths.length ? payload.paths.join(", ") : null);
    }
    row(dl, "Timeout", task.timeout + " s");
    row(dl, "Komenda", task.command && task.command.length ? JSON.stringify(task.command) : "-");
    row(dl, "Powód pytania", task.approval_reason);
    row(dl, "Decyzja", task.decision);
    row(dl, "Utworzone", fmtTime(task.created_at));
    row(dl, "Start", fmtTime(task.started_at));
    row(dl, "Koniec", fmtTime(task.finished_at));
    row(dl, "Kod wyjścia", task.exit_code);
    row(dl, "Czas trwania", task.duration_s === null || task.duration_s === undefined ? null : task.duration_s + " s");
    row(dl, "Błąd", task.error);
    box.appendChild(dl);
    if (!isSkill) {
      diffBlock(box, (task.payload || {}).diff);
      var description = (task.payload || {}).description;
      if (description) {
        var note = el("div", { className: "from-model" }, [
          el("div", { text: "Opis od modelu (niezweryfikowany, nie jest częścią zatwierdzanej treści):" }),
          el("pre", { text: description })
        ]);
        box.appendChild(note);
      }
    }
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

  // --- foldery (roots) ---

  function showTab(name) {
    var isRoots = name === "roots";
    state.tab = name;
    $("tasks-view").hidden = isRoots;
    $("roots-view").hidden = !isRoots;
    $("tab-tasks").className = isRoots ? "tab" : "tab active";
    $("tab-roots").className = isRoots ? "tab active" : "tab";
    if (isRoots) { refreshRoots(); }
  }

  function rootsResult(res, okMessage) {
    if (res.status === 401) { authFailed(); return false; }
    if (res.status === 404 && !(res.json && res.json.error)) {
      showBanner("Zarządzanie folderami jest wyłączone w tej wersji serwera.", true);
      return false;
    }
    if (res.status >= 200 && res.status < 300 && res.json.success) {
      if (okMessage) { showBanner(okMessage, true); } else { hideBanner(); }
      return true;
    }
    showBanner((res.json && res.json.error) || ("Błąd HTTP " + res.status), false);
    return false;
  }

  function rootAction(alias, action, body, okMessage) {
    var path = "/api/roots/" + encodeURIComponent(alias) + "/" + action;
    return api("POST", path, body || {}).then(function (res) {
      rootsResult(res, okMessage);
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    }).then(refreshRoots);
  }

  function rootCard(root) {
    var cls = "root-card" + (root.default ? " is-default" : "") + (root.valid ? "" : " is-invalid");
    var head = el("div", { className: "task-head" }, [
      el("span", { className: "task-title" }, [
        el("span", { className: "badge badge-" + root.mode, text: root.mode }),
        document.createTextNode(root.alias),
        root.default ? el("span", { className: "badge badge-default", text: "domyślny" }) : null,
        root.valid ? null : el("span", { className: "badge badge-invalid", text: "niedostępny" })
      ])
    ]);
    var card = el("div", { className: cls }, [
      head,
      el("div", { className: "root-path", text: root.path }),
      root.valid ? null : el("div", { className: "root-path", text: root.reason })
    ]);

    var description = el("input", { type: "text" });
    description.value = root.description || "";
    description.maxLength = 200;
    var mode = el("select");
    ["rw", "ro"].forEach(function (value) {
      var option = el("option", { text: value });
      option.value = value;
      mode.appendChild(option);
    });
    mode.value = root.mode;

    var edit = el("div", { className: "root-edit" }, [
      mode,
      description,
      el("button", { type: "button", className: "btn", text: "Zapisz", onclick: function () {
        rootAction(root.alias, "update", { mode: mode.value, description: description.value },
                   "Zapisano folder '" + root.alias + "'.");
      } })
    ]);
    if (!root.default) {
      edit.appendChild(el("button", { type: "button", className: "btn btn-trust", text: "Ustaw domyślny",
        onclick: function () { rootAction(root.alias, "default", {}, "Domyślny folder: '" + root.alias + "'."); } }));
    }
    edit.appendChild(el("button", { type: "button", className: "btn btn-deny", text: "Usuń", onclick: function () {
      if (window.confirm("Usunąć folder '" + root.alias + "' z listy? Pliki na dysku zostają.")) {
        rootAction(root.alias, "delete", {}, "Usunięto folder '" + root.alias + "'.");
      }
    } }));
    card.appendChild(edit);
    return card;
  }

  function refreshRoots() {
    return api("GET", "/api/roots").then(function (res) {
      var list = $("roots-list");
      if (res.status === 401) { authFailed(); return; }
      list.textContent = "";
      if (res.status === 404) {
        list.appendChild(el("p", { className: "muted", text: "Zarządzanie folderami jest wyłączone w tej wersji serwera." }));
        return;
      }
      if (res.status !== 200 || !res.json.success) {
        showBanner("Błąd pobierania folderów (HTTP " + res.status + ").", false);
        return;
      }
      var roots = res.json.data.roots;
      if (!roots.length) {
        list.appendChild(el("p", { className: "muted", text: "Brak folderów. Dodaj pierwszy poniżej." }));
        return;
      }
      roots.forEach(function (root) { list.appendChild(rootCard(root)); });
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    });
  }

  function submitRoot(event) {
    event.preventDefault();
    var body = {
      path: $("root-path").value.trim(),
      mode: $("root-mode").value,
      alias: $("root-alias").value.trim(),
      description: $("root-description").value.trim(),
      default: $("root-default").checked
    };
    api("POST", "/api/roots", body).then(function (res) {
      if (rootsResult(res, "Dodano folder.")) {
        $("root-form").reset();
      }
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu.", false);
    }).then(refreshRoots);
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
    $("tab-tasks").addEventListener("click", function () { showTab("tasks"); });
    $("tab-roots").addEventListener("click", function () { showTab("roots"); });
    $("root-form").addEventListener("submit", submitRoot);
    document.addEventListener("visibilitychange", function () {
      if (!state.stopped) { tick(); }
    });
    exchangeToken().then(function () {
      if (!state.stopped) { tick(); }
    });
  }

  init();
})();
