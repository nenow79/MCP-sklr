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
    listSigs: {},       // podpisy ostatnio narysowanych list (pomijanie renderu)
    detailsId: null,    // zadanie aktualnie narysowane w szczegółach
    detailsSig: null,   // podpis narysowanych szczegółów (pomijanie renderu)
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
    var titleText = visibleText("#" + task.id + "  " + (isSkill ? task.skill : (task.title || task.kind)));
    var metaText = visibleText(isSkill
      ? task.script + (task.args && task.args.length ? " " + task.args.join(" ") : "")
      : task.kind);
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
    var key = isPending ? "pending" : "other";
    var sig = JSON.stringify([tasks, state.selectedId, state.busy]);
    if (state.listSigs[key] === sig) { return; } // bez zmian: nie zastępuj DOM
    state.listSigs[key] = sig;
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

  function row(dl, label, value, wide) {
    var item = el("div", { className: wide ? "row wide" : "row" });
    item.appendChild(el("dt", { text: label }));
    item.appendChild(el("dd", { text: value === null || value === undefined || value === "" ? "-" : visibleText(value, true) }));
    if (wide) { wideRows.push(item); } else { dl.appendChild(item); }
  }

  var detailPres = []; // przewijalne <pre> bieżących szczegółów (indeks = kolejność)

  function trackPre(pre) { detailPres.push(pre); return pre; }

  var wideRows = []; // długie wiersze trafiają na koniec tabeli

  function flushRows(dl) {
    wideRows.forEach(function (item) { dl.appendChild(item); });
    wideRows = [];
  }

  function detailsHead(box, task) {
    var head = el("div", { className: "details-head" }, [
      el("h3", { className: "details-title", text: "Zadanie #" + task.id })
    ].concat(kindBadges(task), [statusBadge(task.status)]));
    box.appendChild(head);
  }

  function block(parent, title, text) {
    if (!text) { return; }
    parent.appendChild(el("h3", { text: title }));
    parent.appendChild(trackPre(el("pre", { text: text })));
  }

  function diffBlock(parent, text) {
    if (!text) { return; }
    parent.appendChild(el("h3", { text: "Diff" }));
    var pre = trackPre(el("pre", { className: "diff" }));
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

  function visibleText(value, keepWhitespace) {
    var text = value === null || value === undefined ? "" : String(value);
    return text.replace(HIDDEN_CHARS_RE, function (ch) {
      if (keepWhitespace && (ch === "\n" || ch === "\r" || ch === "\t")) { return ch; }
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
    detailsHead(box, task);
    box.appendChild(el("div", { className: "secret-warning", text:
      "Po zgodzie wynik trafi wyłącznie do klienta MCP przez get_task, nie do panelu. " +
      "Retencja po stronie klienta lub modelu jest poza kontrolą sklr." }));
    var dl = el("dl");
    row(dl, "Rodzaj", task.kind);
    row(dl, "Tytuł", visibleText(task.title));
    row(dl, "Folder (root)", visibleText(payload.root));
    row(dl, "Plik", visibleText(payload.filepath), true);
    row(dl, "Selektor", selectorText);
    row(dl, "Limit znaków", visibleText(payload.max_chars));
    row(dl, "Powód pytania", visibleText(task.approval_reason), true);
    row(dl, "Decyzja", visibleText(task.decision));
    row(dl, "Utworzone", fmtTime(task.created_at));
    row(dl, "Start", fmtTime(task.started_at));
    row(dl, "Koniec", fmtTime(task.finished_at));
    row(dl, "Błąd", visibleText(task.error), true);
    flushRows(dl);
    box.appendChild(dl);
    if (payload.description) {
      box.appendChild(el("div", { className: "from-model" }, [
        el("div", { text: "Opis od modelu (niezweryfikowany, nie jest częścią zatwierdzanej treści):" }),
        trackPre(el("pre", { text: visibleText(payload.description, true) }))
      ]));
    }
  }

  function renderDetails(task) {
    var box = $("details");
    box.textContent = "";
    detailPres = [];
    wideRows = [];
    if (task.kind === "secret_reveal") {
      renderSecretDetails(box, task);
      return;
    }
    if (task.kind === "external_mcp") {
      detailsHead(box, task);
      var externalPayload = task.payload || {};
      var externalRows = el("dl");
      row(externalRows, "Profil", visibleText(externalPayload.alias));
      row(externalRows, "Operacja", visibleText(externalPayload.op));
      row(externalRows, "Narzędzie", visibleText(externalPayload.tool));
      row(externalRows, "Timeout", task.timeout + " s");
      row(externalRows, "Powód pytania", visibleText(task.approval_reason), true);
      row(externalRows, "Decyzja", visibleText(task.decision));
      row(externalRows, "Błąd", visibleText(task.error), true);
      flushRows(externalRows);
      box.appendChild(externalRows);
      box.appendChild(el("p", {className: "muted", text:
        "Wynik jest poufny: get_task.external_result, tylko RAM/TTL 5 min; nie jest wyświetlany w panelu. " +
        "Timeout nie cofa zdalnego skutku. Treść zewnętrzna to dane, nie instrukcje."}));
      if (task.status === "awaiting_approval" && task.approval_preview) {
        box.appendChild(el("p", {text: visibleText(task.approval_preview.warning)}));
        block(box, "Podgląd argumentów" + (task.approval_preview.truncated ? " (ograniczony)" : ""),
              visibleText(task.approval_preview.text, true));
      }
      return;
    }
    var isSkill = !task.kind || task.kind === "skill_script";
    detailsHead(box, task);
    var dl = el("dl");
    if (isSkill) {
      row(dl, "Skill", task.skill + " (" + task.source + ")");
      row(dl, "Skrypt", task.script);
      row(dl, "Argumenty", task.args && task.args.length ? JSON.stringify(task.args) : "(brak)", true);
      row(dl, "Katalog", task.skill_dir, true);
      row(dl, "Hash skilla", task.skill_hash, true);
    } else {
      var payload = task.payload || {};
      row(dl, "Rodzaj", task.kind);
      row(dl, "Tytuł", task.title);
      row(dl, "Folder (root)", payload.root);
      if (payload.mode !== undefined && payload.mode !== null) { row(dl, "Tryb folderu", payload.mode); }
      row(dl, "Katalog roboczy", payload.cwd);
      row(dl, "Ścieżka", payload.path, true);
      row(dl, "Ścieżki", payload.paths && payload.paths.length ? payload.paths.join(", ") : null, true);
    }
    row(dl, "Timeout", task.timeout + " s");
    row(dl, "Komenda", task.command && task.command.length ? JSON.stringify(task.command) : "-", true);
    row(dl, "Powód pytania", task.approval_reason, true);
    row(dl, "Decyzja", task.decision);
    row(dl, "Utworzone", fmtTime(task.created_at));
    row(dl, "Start", fmtTime(task.started_at));
    row(dl, "Koniec", fmtTime(task.finished_at));
    row(dl, "Kod wyjścia", task.exit_code);
    row(dl, "Czas trwania", task.duration_s === null || task.duration_s === undefined ? null : task.duration_s + " s");
    row(dl, "Błąd", task.error, true);
    flushRows(dl);
    box.appendChild(dl);
    if (!isSkill) {
      // Diff jest rysowany na końcu (największy blok).
      var description = (task.payload || {}).description;
      if (description) {
        var note = el("div", { className: "from-model" }, [
          el("div", { text: "Opis od modelu (niezweryfikowany, nie jest częścią zatwierdzanej treści):" }),
          trackPre(el("pre", { text: visibleText(description, true) }))
        ]);
        box.appendChild(note);
      }
    }
    block(box, "Podgląd skryptu", task.script_preview);
    block(box, "stdout" + (task.stdout_truncated ? " (obcięty)" : ""), task.stdout);
    block(box, "stderr" + (task.stderr_truncated ? " (obcięty)" : ""), task.stderr);
    if (!isSkill) { diffBlock(box, (task.payload || {}).diff); }
  }

  function captureScroll() {
    return {
      page: window.pageYOffset || 0,
      pres: detailPres.map(function (pre) { return [pre.scrollTop || 0, pre.scrollLeft || 0]; })
    };
  }

  function restoreScroll(saved) {
    detailPres.forEach(function (pre, i) {
      if (saved.pres[i]) { pre.scrollTop = saved.pres[i][0]; pre.scrollLeft = saved.pres[i][1]; }
    });
    if (typeof window.scrollTo === "function" && (window.pageYOffset || 0) !== saved.page) {
      window.scrollTo(window.pageXOffset || 0, saved.page);
    }
  }

  function loadDetails(id) {
    return api("GET", "/api/tasks/" + encodeURIComponent(id)).then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (res.status !== 200 || !res.json.success) { return; }
      if (id !== state.selectedId) { return; } // spóźniona odpowiedź dla innego zadania
      var sig = JSON.stringify(res.json.data);
      var same = state.detailsId === id;
      if (same && state.detailsSig === sig) { return; } // bez zmian: zachowaj DOM i scroll
      var saved = same ? captureScroll() : null;
      renderDetails(res.json.data);
      state.detailsId = id;
      state.detailsSig = sig;
      if (saved) { restoreScroll(saved); }
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
    state.tab = name;
    if (name !== "session" && $("session-checkpoint")) {
      clearSessionCheckpoint();
      clearSessionResume();
      $("session-supplement").value = "";
    }
    ["tasks", "roots", "skills", "knowledge", "external", "session"].forEach(function (tab) {
      if ($(tab + "-view")) { $(tab + "-view").hidden = tab !== name; }
      if ($("tab-" + tab)) { $("tab-" + tab).className = tab === name ? "tab active" : "tab"; }
    });
    if (name === "roots") { refreshRoots(); }
    if (name === "skills") { refreshSkills(); }
    if (name === "knowledge") { refreshKnowledge(); }
    if (name === "external") { refreshExternal(); }
    if (name === "session") { refreshSession(); }
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

  // --- skille ---
  var skillsLoading = false;
  var skillsSaving = false;

  function skillCard(skill) {
    var source = ["central", "dev", "project"].indexOf(skill.source) >= 0 ? skill.source : "unknown";
    var card = el("div", { className: "skill-card" + (skill.shadowed ? " is-shadowed" : "") }, [
      el("div", { className: "task-head" }, [
        el("span", { className: "task-title", text: visibleText(skill.name) }),
        el("span", { className: "badge skill-source-" + source, text: source })
      ]),
      el("p", { text: visibleText(skill.description, true) }),
      el("div", { className: "root-path", text: visibleText(skill.path) })
    ]);
    (skill.warnings || []).forEach(function (warning) {
      card.appendChild(el("p", { className: "skill-warning", text: visibleText(warning, true) }));
    });
    var trustLabels = {
      untrusted: "Niezaufany", trusted: "Zaufany — hash zgodny",
      changed: "Zmieniony od zaufania", not_applicable: "Zaufanie nie dotyczy tego źródła",
      hash_error: "Błąd odczytu hasha"
    };
    card.appendChild(el("p", { className: "muted", text: trustLabels[skill.trust_status] || "Status zaufania niedostępny" }));
    if (skill.current_hash) {
      card.appendChild(el("div", { className: "root-path", text: "Hash: " + visibleText(skill.current_hash) }));
    }
    if (skill.trusted_at) {
      card.appendChild(el("div", { className: "root-path", text: "Zaufanie nadano: " + visibleText(skill.trusted_at) }));
    }
    if (source === "central" && !skill.shadowed) {
      var policyText = skill.can_skip_approval ? "Zaufana wersja może działać bez pytania o zgodę." :
        (skill.declares_no_confirm ? "Brak zaufania zgodnej wersji — uruchomienie wymaga zgody." :
          "Każde uruchomienie nadal wymaga zgody — brak vibecoder-confirm=false.");
      card.appendChild(el("p", { className: "muted", text: policyText }));
      var actions = el("div", { className: "task-actions" });
      if (skill.current_hash && skill.trust_status !== "trusted") {
        actions.appendChild(el("button", { type: "button", className: "btn btn-trust", text: "Ufaj bieżącej wersji",
          onclick: function () { skillTrustAction(skill, "trust", actions); } }));
      }
      if (skill.trust_status === "trusted" || skill.trust_status === "changed") {
        actions.appendChild(el("button", { type: "button", className: "btn btn-deny", text: "Cofnij zaufanie",
          onclick: function () { skillTrustAction(skill, "revoke-trust", actions); } }));
      }
      card.appendChild(actions);
    }
    var checkbox = el("input", { type: "checkbox" });
    checkbox.checked = !!skill.enabled;
    checkbox.disabled = !!skill.shadowed;
    card.appendChild(el("label", { className: "skill-toggle" }, [
      checkbox, el("span", { text: "Dostępny dla LLM" })
    ]));
    if (skill.shadowed) {
      card.appendChild(el("p", { className: "muted", text: "Przesłonięty — testuj kopię pod inną nazwą." }));
    }
    checkbox.addEventListener("change", function () {
      if (skill.shadowed || skillsSaving || skillsLoading) { checkbox.checked = !!skill.enabled; return; }
      var wanted = checkbox.checked;
      skillsSaving = true;
      checkbox.disabled = true;
      $("skills-refresh").disabled = true;
      api("POST", "/api/skills/" + encodeURIComponent(skill.name) + "/enabled", { enabled: wanted })
        .then(function (res) {
          if (res.status === 401) { authFailed(); }
          if (res.status === 200 && res.json.success) {
            skill.enabled = !!res.json.data.enabled;
            showBanner("Zapisano dostępność skilla.", true);
          } else {
            showBanner(visibleText((res.json && res.json.error) || "Nie udało się zapisać dostępności."), false);
          }
        }).catch(function () { showBanner("Brak połączenia z serwerem panelu.", false); })
        .then(function () {
          checkbox.checked = !!skill.enabled;
          checkbox.disabled = !!skill.shadowed;
          skillsSaving = false;
          $("skills-refresh").disabled = false;
          refreshSkills();
        });
    });
    return card;
  }

  function skillTrustAction(skill, action, actions) {
    if (skillsSaving || skillsLoading || skill.source !== "central" || skill.shadowed) { return; }
    var grant = action === "trust";
    var question = (grant ? "Ufaj bieżącej wersji skilla?" : "Cofnąć zaufanie skillowi?") +
      "\nNazwa: " + visibleText(skill.name) + "\nKatalog: " + visibleText(skill.path) +
      "\nHash: " + visibleText(skill.current_hash || "niedostępny") +
      "\nTo nie jest sandbox. Skrypty działają z uprawnieniami użytkownika." +
      "\nHash obejmuje SKILL.md i scripts/, nie references/, assets/, środowisko ani dane wejściowe.";
    if (!window.confirm(question)) { return; }
    var body = { path: skill.path };
    if (grant) { body.hash = skill.current_hash; }
    skillsSaving = true;
    $("skills-refresh").disabled = true;
    var buttons = Array.prototype.slice.call(actions.childNodes);
    buttons.forEach(function (button) { button.disabled = true; });
    api("POST", "/api/skills/" + encodeURIComponent(skill.name) + "/" + action, body)
      .then(function (res) {
        if (res.status === 401) { authFailed(); }
        if (res.status === 200 && res.json.success) {
          showBanner(grant ? "Zapisano zaufanie wersji skilla." : "Cofnięto zaufanie skilla.", true);
        } else {
          showBanner(visibleText((res.json && res.json.error) || "Nie udało się zapisać zaufania."), false);
        }
      }).catch(function () { showBanner("Brak połączenia z serwerem panelu.", false); })
      .then(function () {
        skillsSaving = false;
        buttons.forEach(function (button) { button.disabled = false; });
        $("skills-refresh").disabled = false;
        refreshSkills();
      });
  }

  var skillSources = ["central", "dev", "project"];
  var skillFilters = { central: true, dev: true, project: true };
  var skillsViewData = null;
  var skillsFilterKey = "sklr.skills.source-filters";

  function renderFilteredSkills() {
    if (!skillsViewData) { return; }
    var list = $("skills-list");
    list.textContent = "";
    var visible = skillsViewData.skills.filter(function (skill) {
      return skillSources.indexOf(skill.source) < 0 || skillFilters[skill.source];
    });
    $("skills-visible-count").textContent = "Widoczne " + visible.length + " z " + skillsViewData.skills.length;
    if (!visible.length) {
      list.appendChild(el("p", { className: "muted", text: skillsViewData.skills.length ?
        "Filtry ukrywają wszystkie skille." : "Brak skilli." }));
    }
    visible.forEach(function (skill) { list.appendChild(skillCard(skill)); });
  }

  function initSkillFilters() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(skillsFilterKey));
      skillSources.forEach(function (source) {
        if (saved && typeof saved[source] === "boolean") { skillFilters[source] = saved[source]; }
      });
    } catch (error) { /* Brak storage albo uszkodzony wpis: wartości domyślne. */ }
    skillSources.forEach(function (source) {
      var checkbox = $("skills-filter-" + source);
      checkbox.checked = skillFilters[source];
      checkbox.addEventListener("change", function () {
        if (skillsSaving || skillsLoading) { checkbox.checked = skillFilters[source]; return; }
        skillFilters[source] = checkbox.checked;
        try { window.localStorage.setItem(skillsFilterKey, JSON.stringify(skillFilters)); }
        catch (error) { /* Filtry nadal działają bez zapisu preferencji. */ }
        renderFilteredSkills();
      });
    });
  }

  function refreshSkills() {
    if (skillsLoading || skillsSaving) { return Promise.resolve(); }
    skillsLoading = true;
    $("skills-refresh").disabled = true;
    return api("GET", "/api/skills").then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (res.status !== 200 || !res.json.success) {
        showBanner(visibleText((res.json && res.json.error) || "Nie udało się pobrać skilli."), false);
        return;
      }
      var data = res.json.data;
      skillsViewData = data;
      renderFilteredSkills();
      var dirs = $("skills-dev-dirs");
      dirs.textContent = "";
      if (!data.dev_dirs.length) { dirs.textContent = "Brak skonfigurowanych katalogów dev."; }
      data.dev_dirs.forEach(function (directory) {
        dirs.appendChild(el("div", { className: "root-path", text: visibleText(directory) }));
      });
    }).catch(function () { showBanner("Brak połączenia z serwerem panelu.", false); })
      .then(function () { skillsLoading = false; $("skills-refresh").disabled = false; });
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

  // --- wiedza: automatyczne docs i ręczna konfiguracja w uwierzytelnionym panelu ---
  var knowledgeData = null;
  var knowledgeBusy = false;

  function renderKnowledge(data) {
    knowledgeData = data;
    var list = $("knowledge-list");
    list.textContent = "";
    if (!data.bundles.length) { list.appendChild(el("p", {text: "Brak skonfigurowanych zbiorów."})); }
    data.bundles.forEach(function (bundle) {
      var card = el("article", {className: "task-card"});
      card.appendChild(el("h3", {text: visibleText(bundle.label + " (" + (bundle.id || "niepoprawny wpis") + ")")}));
      card.appendChild(el("p", {text: visibleText(bundle.description)}));
      if (bundle.automatic) { card.appendChild(el("p", {text: "Wykryto automatycznie z folderu projektu."})); }
      card.appendChild(el("p", {text: visibleText((bundle.scope || "?") + " / " + (bundle.profile || "?") +
        " / " + (bundle.root || "?") + ":" + (bundle.subpath || "."))}));
      card.appendChild(el("p", {text: bundle.available ?
        "Dostępny, " + (bundle.writable ? "zapis możliwy" : "tylko odczyt") :
        "Niedostępny: " + visibleText(bundle.reason + (bundle.detail ? " / " + bundle.detail : ""))}));
      card.appendChild(el("p", {text: "Indeks: " + visibleText(bundle.index_state) + ", dokumenty: " + bundle.indexed_documents}));
      if (bundle.reasons && bundle.reasons.length) {
        card.appendChild(el("p", {text: "Powody: " + visibleText(bundle.reasons.join(", ")) +
          "; pominięte dokumenty: " + bundle.excluded_documents}));
      }
      if (bundle.refresh_task_id) {
        card.appendChild(el("p", {text: "Odświeżanie — zadanie " + bundle.refresh_task_id}));
      }
      if (bundle.index_error) {
        card.appendChild(el("p", {text: "Błąd indeksu: " + visibleText(bundle.index_error)}));
      }
      var diagnostic = bundle.last_refresh || {};
      if (typeof diagnostic.scan_ms === "number") {
        card.appendChild(el("p", {text: "Ostatnie odświeżenie: odczyt " + diagnostic.scan_ms +
          " ms, publikacja " + (diagnostic.publish_ms || 0) + " ms; odczytane dokumenty: " +
          diagnostic.read_documents + " / " + diagnostic.candidates}));
      }
      ["scan_checks", "publish_checks"].forEach(function (phase) {
        var metrics = diagnostic[phase];
        if (metrics) {
          card.appendChild(el("p", {text: (phase === "scan_checks" ? "Kontrole odczytu: " : "Kontrole publikacji: ") +
            visibleText("bundle " + metrics.bundle_check_calls + " / " + metrics.bundle_check_ms + " ms; " +
              "ścieżka " + metrics.path_resolve_calls + " / " + metrics.path_resolve_ms + " ms; " +
              "autoryzacja " + metrics.authorize_calls + " / " + metrics.authorize_ms + " ms")}));
        }
      });
      ["read_errors", "read_error_stages"].forEach(function (kind) {
        var counts = diagnostic[kind] || {};
        if (Object.keys(counts).length) {
          card.appendChild(el("p", {text: (kind === "read_errors" ? "Błędy odczytu: " : "Etapy błędów: ") +
            visibleText(Object.keys(counts).sort().map(function (code) {
              return code + ": " + counts[code];
            }).join(", "))}));
        }
      });
      var toggle = el("button", {type: "button", className: "btn", text: bundle.automatic ?
        (bundle.enabled ? "Wyłącz automatyczne docs" : "Włącz automatyczne docs") : (bundle.enabled ? "Wyłącz" : "Włącz"),
        onclick: function () { changeKnowledge(bundle.key, "enabled", {enabled: !bundle.enabled}); }});
      var remove = el("button", {type: "button", className: "btn", text: "Usuń konfigurację",
        onclick: function () { changeKnowledge(bundle.key, "remove", {}); }});
      var scan = el("button", {type: "button", className: "btn", text: "Odśwież indeks",
        onclick: function () { changeKnowledge(bundle.key, "refresh", {}); }});
      scan.disabled = !bundle.available;
      card.appendChild(toggle); card.appendChild(scan);
      if (!bundle.automatic) { card.appendChild(remove); }
      list.appendChild(card);
    });
    var select = $("knowledge-root");
    var selected = select.value;
    select.textContent = "";
    data.roots.forEach(function (root) {
      var option = el("option", {text: root.alias + " (" + root.mode + ")"});
      option.value = root.alias; select.appendChild(option);
    });
    if (data.roots.some(function (root) { return root.alias === selected; })) { select.value = selected; }
    var policyRoot = $("knowledge-policy-root");
    var previous = policyRoot.value;
    policyRoot.textContent = "";
    data.roots.forEach(function (root) {
      var option = el("option", {text: root.alias});
      option.value = root.alias; policyRoot.appendChild(option);
    });
    if (data.roots.some(function (root) { return root.alias === previous; })) { policyRoot.value = previous; }
    renderKnowledgePolicy();
  }

  function renderKnowledgePolicy() {
    if (!knowledgeData) { return; }
    var root = knowledgeData.roots.filter(function (item) { return item.alias === $("knowledge-policy-root").value; })[0];
    var policy = root && root.write_policy;
    $("knowledge-policy-save").disabled = !root;
    $("knowledge-policy-mode").value = policy && policy.mode === "require_okf" ? "require_okf" : "compatible";
    $("knowledge-policy-subpaths").value = (policy ? policy.subpaths : ["docs"]).join("\n");
    $("knowledge-policy-exclusions").value = (policy ? policy.exclusions : ["docs/archive/", "docs/archives/"]).join("\n");
    $("knowledge-policy-error").textContent = policy && policy.error ? policy.error : "";
  }

  function submitKnowledgePolicy(event) {
    event.preventDefault();
    if (knowledgeBusy || !knowledgeData) { return; }
    function lines(id) {
      return $(id).value.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
    }
    var proposal = {root: $("knowledge-policy-root").value, mode: $("knowledge-policy-mode").value,
      subpaths: lines("knowledge-policy-subpaths"), exclusions: lines("knowledge-policy-exclusions")};
    knowledgeBusy = true;
    $("knowledge-policy-save").disabled = true;
    return api("POST", "/api/knowledge/policy", {policy: proposal, revision: knowledgeData.revision}).then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (!res.json.success) { showBanner(res.json.error || "Nie udało się zapisać polityki.", false); return; }
      renderKnowledge(res.json.data); hideBanner();
    }).catch(function () { showBanner("Nie udało się zapisać polityki projektu.", false); })
      .then(function () { knowledgeBusy = false; $("knowledge-policy-save").disabled = !knowledgeData.roots.length; });
  }

  function refreshKnowledge() {
    if (knowledgeBusy) { return Promise.resolve(); }
    knowledgeBusy = true;
    return api("GET", "/api/knowledge").then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (!res.json.success) { showBanner(res.json.error || "Nie można odczytać wiedzy.", false); return; }
      renderKnowledge(res.json.data);
    }).catch(function () { showBanner("Brak połączenia z panelem Wiedza.", false); })
      .then(function () { knowledgeBusy = false; });
  }

  function changeKnowledge(key, action, body) {
    if (knowledgeBusy || !knowledgeData) { return; }
    knowledgeBusy = true;
    body.revision = knowledgeData.revision;
    return api("POST", "/api/knowledge/" + encodeURIComponent(key) + "/" + action, body).then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (!res.json.success) { showBanner(res.json.error || "Nie udało się zmienić zbioru.", false); return; }
      renderKnowledge(res.json.data); hideBanner();
    }).catch(function () { showBanner("Nie udało się zmienić konfiguracji wiedzy.", false); })
      .then(function () { knowledgeBusy = false; return refreshKnowledge(); });
  }

  function knowledgeProfileChanged(scopeChanged) {
    var project = $("knowledge-scope").value === "project";
    var profile = $("knowledge-profile");
    if (scopeChanged) { profile.value = project ? "docs" : "all"; }
    Array.prototype.forEach.call(profile.options, function (option) { option.disabled = project === (option.value === "all"); });
    if (project && profile.value !== "docs") { $("knowledge-subpath").value = ""; }
    else if (project && !$("knowledge-subpath").value) { $("knowledge-subpath").value = "docs"; }
  }

  function submitKnowledge(event) {
    event.preventDefault();
    if (knowledgeBusy || !knowledgeData) { return; }
    var bundle = {};
    ["id", "label", "description", "scope", "root", "profile", "subpath"].forEach(function (key) {
      bundle[key] = $("knowledge-" + key).value;
    });
    bundle.enabled = $("knowledge-enabled").checked;
    knowledgeBusy = true;
    $("knowledge-add").disabled = true;
    api("POST", "/api/knowledge", {bundle: bundle, revision: knowledgeData.revision}).then(function (res) {
      if (res.status === 401) { authFailed(); return; }
      if (!res.json.success) { showBanner(res.json.error || "Nie udało się dodać zbioru.", false); return; }
      renderKnowledge(res.json.data); hideBanner();
      $("knowledge-id").value = ""; $("knowledge-label").value = "";
    }).catch(function () { showBanner("Nie udało się zapisać zbioru wiedzy.", false); })
      .then(function () { knowledgeBusy = false; $("knowledge-add").disabled = false; });
  }

  // --- zewnętrzne MCP (M4b): profile, włączanie, usuwanie, cofanie zaufania ---
  // Treść z serwera trafia do DOM przez textContent. Formularz przyjmuje wyłącznie NAZWY
  // zmiennych środowiskowych, nigdy wartości sekretów. Test połączenia i nadawanie zaufania: M4c.

  var externalData = null;
  var externalBusy = false;
  var externalEditing = null;   // id edytowanego profilu; null = dodawanie
  var EXTERNAL_TEXT_FIELDS = ["external-alias", "external-description", "external-url",
    "external-bearer", "external-ca", "external-headers", "external-command",
    "external-args", "external-cwd", "external-env-refs"];

  // --- test połączenia i zaufanie (M4c) ---
  // Katalog narzędzi trzyma serwer. Przeglądarka dostaje nazwy i opisy tylko do wyświetlenia
  // i odsyła wyłącznie token testu oraz nazwy wybranych narzędzi; nie widzi skrótów schematów.

  var externalInspect = {};      // id profilu -> {token, state, tools, selected, code, polls}
  var externalLastError = "";
  var EXTERNAL_POLL_MS = 1000;
  var EXTERNAL_POLL_MAX = 90;    // ok. 90 s, dłużej niż limit zadania testu (60 s)
  var EXTERNAL_HINTS = {
    timeout: "Przekroczono limit czasu. Sprawdź, czy serwer działa i odpowiada.",
    http_transport_failed: "Nie udało się połączyć z serwerem. Sprawdź adres, port i czy serwer jest uruchomiony.",
    dns_failed: "Nie udało się rozwiązać nazwy hosta.",
    dns_timeout: "Rozwiązywanie nazwy hosta trwało zbyt długo.",
    self_or_panel_endpoint: "Adres wskazuje na sklr lub jego panel.",
    destination_blocked: "Adres docelowy jest zablokowany (np. link-local lub metadane chmury).",
    environment_missing: "W procesie sklr brakuje zmiennej środowiskowej wskazanej w profilu.",
    tls_configuration: "Nie udało się wczytać pliku CA lub skonfigurować TLS.",
    cwd_unavailable: "Katalog roboczy nie istnieje.",
    stdio_spawn_failed: "Nie udało się uruchomić programu.",
    stdio_batch_not_supported: "Pliki .cmd i .bat nie są obsługiwane; podaj plik wykonywalny.",
    stdio_containment_failed: "Nie udało się objąć programu kontrolą procesów; nie został uruchomiony.",
    message_limit: "Serwer odesłał zbyt dużą wiadomość.",
    http_message_limit: "Serwer odesłał zbyt dużą odpowiedź.",
    tools_limit: "Katalog narzędzi ma za dużo pozycji.",
    schema_limit: "Schemat narzędzia jest za duży.",
    catalog_limit: "Katalog narzędzi jest za duży.",
    client_failed: "Nieoczekiwany błąd klienta.",
    cancelled: "Test został anulowany.",
    profile_changed: "Ustawienia połączenia zmieniły się w trakcie testu.",
    poll_timeout: "Panel przestał czekać na wynik testu.",
    inspection_expired: "Wynik testu wygasł lub serwer go nie zna.",
    panel_unreachable: "Brak połączenia z serwerem panelu."
  };

  function externalHint(code) {
    if (Object.prototype.hasOwnProperty.call(EXTERNAL_HINTS, code)) { return EXTERNAL_HINTS[code]; }
    if (String(code).indexOf("task_") === 0) {
      return "Zadanie testu zakończyło się bez wyniku (np. po zmianie ustawień połączenia).";
    }
    return "";
  }

  function selectedExternalTools(rec) {
    return rec.tools.map(function (tool) { return tool.name; })
      .filter(function (name) { return rec.selected[name]; });
  }

  function scheduleExternalPoll(id) {
    setTimeout(function () { pollExternalInspection(id); }, EXTERNAL_POLL_MS);
  }

  function settleExternalInspection(rec, state, code) {
    rec.state = state;
    rec.code = code;
    if (externalData) { renderExternal(externalData); }
    return refreshExternal();   // odświeża też stan ostatniego testu z serwera
  }

  function pollExternalInspection(id) {
    var rec = externalInspect[id];
    if (!rec || rec.state !== "pending") { return Promise.resolve(); }
    return api("GET", "/api/external-mcp/inspections/" + encodeURIComponent(rec.token)).then(function (res) {
      if (externalInspect[id] !== rec) { return; }   // zastąpiony albo usunięty
      if (res.status === 401) { authFailed(); return; }
      var data = res.status === 200 && res.json && res.json.success ? res.json.data : null;
      if (data && data.state === "pending") {
        rec.polls += 1;
        if (rec.polls < EXTERNAL_POLL_MAX) { scheduleExternalPoll(id); return; }
        return settleExternalInspection(rec, "failed", "poll_timeout");
      }
      if (data && data.state === "ready") {
        rec.tools = data.tools || [];
        return settleExternalInspection(rec, "ready", "");
      }
      return settleExternalInspection(rec, "failed", data ? data.code : "inspection_expired");
    }).catch(function () {
      if (externalInspect[id] === rec) { return settleExternalInspection(rec, "failed", "panel_unreachable"); }
    });
  }

  function startExternalInspection(profile) {
    var current = externalInspect[profile.id];
    if (current && current.state === "pending") { return Promise.resolve(); }
    var body = {};
    if (profile.transport === "stdio") {
      if (!window.confirm("Test uruchomi lokalny program: " + visibleText(externalTarget(profile)) +
          " Program działa z Twoimi uprawnieniami i może pobierać oraz wykonywać kod. Kontynuować?")) {
        return Promise.resolve();
      }
      body.acknowledge_launch = true;
    }
    return api("POST", externalPath(profile, "inspect"), body).then(function (res) {
      if (externalProblem(res)) { return; }
      if (res.status !== 202 || !res.json.success) {
        showBanner(visibleText((res.json && res.json.error) || ("Błąd HTTP " + res.status)), false);
        return;
      }
      hideBanner();
      externalInspect[profile.id] = {token: res.json.data.token, state: "pending", tools: [],
        selected: {}, code: "", polls: 0};
      if (externalData) { renderExternal(externalData); }
      scheduleExternalPoll(profile.id);
    }).catch(function () { showBanner("Brak połączenia z serwerem panelu.", false); });
  }

  function grantExternalTrust(profile, rec) {
    var names = selectedExternalTools(rec);
    if (!names.length) { return Promise.resolve(false); }
    if (!window.confirm(visibleText("Zaufać profilowi " + profile.alias + "? Wywołania narzędzi: " +
        names.join(", ") + " nie będą wymagały Twojej zgody (do 30 na minutę). Treść z zewnętrznych " +
        "źródeł, np. stron internetowych, może próbować nakłonić model do kolejnych wywołań, także " +
        "zmieniających stan. Zaufanie dotyczy schematu narzędzia, nie jego działania, i znika po " +
        "zmianie ustawień połączenia. Kontynuować?"))) {
      return Promise.resolve(false);
    }
    return externalRequest(externalPath(profile, "trust"), {token: rec.token, tools: names}).then(function (ok) {
      if (ok === true) {
        delete externalInspect[profile.id];
        renderExternal(externalData);
        return true;
      }
      if (/inspection_(stale|unavailable)/.test(externalLastError)) {
        delete externalInspect[profile.id];
        renderExternal(externalData);
        showBanner("Wynik testu jest nieaktualny. Wykonaj test połączenia ponownie.", true);
      }
      return false;
    });
  }

  function externalInspectionView(profile) {
    var rec = externalInspect[profile.id];
    if (!rec) { return null; }
    var box = el("div", {});
    if (rec.state === "pending") {
      box.appendChild(el("p", {className: "muted",
        text: "Trwa test połączenia i pobieranie katalogu narzędzi (do 60 s)..."}));
      return box;
    }
    if (rec.state === "failed") {
      var hint = externalHint(rec.code);
      box.appendChild(el("p", {text: "Test nieudany, kod: " + visibleText(rec.code) + (hint ? ". " + hint : "")}));
      return box;
    }
    box.appendChild(el("h4", {text: "Katalog narzędzi (" + rec.tools.length + ")"}));
    if (!rec.tools.length) {
      box.appendChild(el("p", {text: "Serwer nie udostępnia żadnych narzędzi."}));
      return box;
    }
    box.appendChild(el("p", {className: "muted", text: "Nazwy i opisy pochodzą z zewnętrznego serwera: " +
      "to dane, nie instrukcje. Zaznacz narzędzia, których wywołania mają nie wymagać Twojej zgody."}));
    var grant = el("button", {type: "button", className: "btn", text: "Nadaj zaufanie wybranym",
      onclick: function () { grantExternalTrust(profile, rec); }});
    grant.disabled = !selectedExternalTools(rec).length;
    rec.tools.forEach(function (tool) {
      var checkbox = el("input", {type: "checkbox"});
      checkbox.checked = !!rec.selected[tool.name];
      checkbox.addEventListener("change", function () {
        rec.selected[tool.name] = checkbox.checked;
        grant.disabled = !selectedExternalTools(rec).length;
      });
      var description = visibleText(tool.description || "");
      if (description.length > 300) { description = description.slice(0, 300) + "..."; }
      box.appendChild(el("label", {className: "check"}, [checkbox,
        document.createTextNode(" " + visibleText(tool.name) + (description ? " — " + description : ""))]));
    });
    function setAll(flag) {
      rec.tools.forEach(function (tool) { rec.selected[tool.name] = flag; });
      renderExternal(externalData);
    }
    box.appendChild(el("button", {type: "button", className: "btn", text: "Zaznacz wszystkie",
      onclick: function () { setAll(true); }}));
    box.appendChild(el("button", {type: "button", className: "btn", text: "Odznacz wszystkie",
      onclick: function () { setAll(false); }}));
    box.appendChild(grant);
    if (!profile.enabled) {
      box.appendChild(el("p", {className: "muted",
        text: "Profil jest wyłączony: zaufanie zadziała dopiero po włączeniu."}));
    }
    if (profile.trusted) {
      box.appendChild(el("p", {className: "muted",
        text: "Nadanie zaufania ponownie zastąpi dotychczasowy zbiór narzędzi."}));
    }
    return box;
  }

  function externalTransportChanged() {
    var stdio = $("external-transport").value === "stdio";
    $("external-net-fields").hidden = stdio;
    $("external-stdio-fields").hidden = !stdio;
  }

  function refsToLines(map) {
    return Object.keys(map || {}).map(function (key) { return key + "=" + map[key]; }).join("\n");
  }

  // Wiersze NAZWA=ZMIENNA. null = błąd składni albo powtórzona nazwa.
  function parseRefs(id) {
    var out = Object.create(null);
    var rows = $(id).value.split("\n");
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i].replace(/\r$/, "").trim();
      if (!row) { continue; }
      var at = row.indexOf("=");
      if (at < 1 || at === row.length - 1) { return null; }
      var key = row.slice(0, at).trim();
      if (Object.prototype.hasOwnProperty.call(out, key)) { return null; }
      out[key] = row.slice(at + 1).trim();
    }
    return out;
  }

  function parseArgs() {
    var rows = $("external-args").value.split("\n").map(function (row) { return row.replace(/\r$/, ""); });
    while (rows.length && rows[rows.length - 1] === "") { rows.pop(); }
    return rows;
  }

  function optionalText(id) {
    var value = $(id).value.trim();
    return value ? value : null;
  }

  function buildExternalProfile() {
    var transport = $("external-transport").value;
    var profile = {alias: $("external-alias").value.trim(),
      description: $("external-description").value.trim(),
      enabled: $("external-enabled").checked, transport: transport};
    var refsError = {error: "Zmienne: po jednym wierszu NAZWA=ZMIENNA, bez powtórzeń nazw."};
    if (transport === "stdio") {
      var env = parseRefs("external-env-refs");
      if (env === null) { return refsError; }
      profile.command = $("external-command").value.trim();
      profile.args = parseArgs();
      profile.cwd = optionalText("external-cwd");
      profile.env_refs = env;
    } else {
      var headers = parseRefs("external-headers");
      if (headers === null) { return refsError; }
      profile.url = $("external-url").value.trim();
      profile.headers_env = headers;
      profile.bearer_env = optionalText("external-bearer");
      profile.ca_file = optionalText("external-ca");
    }
    return {profile: profile};
  }

  function externalTarget(profile) {
    if (profile.transport === "stdio") {
      return [profile.command].concat((profile.args || []).map(function (arg) {
        return "[" + arg + "]";
      })).join(" ");
    }
    return profile.url;
  }

  function externalReferences(profile) {
    var names = [];
    [profile.headers_env, profile.env_refs].forEach(function (map) {
      Object.keys(map || {}).forEach(function (key) { names.push(map[key]); });
    });
    if (profile.bearer_env) { names.push(profile.bearer_env); }
    return names;
  }

  function externalCard(profile) {
    var card = el("article", {className: "task-card"});
    var badges = [
      el("span", {className: "badge badge-kind", text: profile.transport}),
      el("span", {className: "badge " + (profile.enabled ? "badge-external-on" : "badge-external-off"),
        text: profile.enabled ? "włączony" : "wyłączony"})
    ];
    if (profile.trusted) { badges.push(el("span", {className: "badge badge-trusted", text: "zaufany"})); }
    card.appendChild(el("h3", {}, badges.concat([document.createTextNode(visibleText(profile.alias))])));
    if (profile.description) { card.appendChild(el("p", {text: visibleText(profile.description)})); }
    card.appendChild(el("p", {text: "Połączenie: " + visibleText(externalTarget(profile))}));
    if (profile.cwd) { card.appendChild(el("p", {text: "Katalog roboczy: " + visibleText(profile.cwd)})); }
    var references = externalReferences(profile);
    if (references.length) {
      card.appendChild(el("p", {text: "Zmienne środowiskowe: " + visibleText(references.join(", "))}));
    }
    if (profile.env_missing && profile.env_missing.length) {
      card.appendChild(el("p", {text: "Brak w procesie sklr: " + visibleText(profile.env_missing.join(", ")) +
        ". Połączenie się nie powiedzie."}));
    }
    if (profile.trusted && profile.trust) {
      var names = Object.keys(profile.trust.tools || {}).sort();
      card.appendChild(el("p", {text: "Zaufanie (od " + fmtTime(profile.trust.granted_at) +
        ") obejmuje narzędzia: " + visibleText(names.join(", ")) + ". Ich wywołania nie wymagają zgody; " +
        "zmiana ustawień połączenia zeruje zaufanie."}));
    }
    var last = profile.last_test;
    card.appendChild(el("p", {className: "muted", text: !last ? "Brak testu połączenia w tej sesji serwera." :
      (last.state === "ok" ? "Ostatni test (" + fmtTime(last.at) + "): połączono, narzędzi: " + last.tools :
        "Ostatni test (" + fmtTime(last.at) + ") nieudany, kod: " + visibleText(last.code))}));
    var inspection = externalInspect[profile.id];
    var testing = !!(inspection && inspection.state === "pending");
    var testButton = el("button", {type: "button", className: "btn",
      text: testing ? "Testowanie…" : "Testuj połączenie",
      onclick: function () { startExternalInspection(profile); }});
    testButton.disabled = testing;
    card.appendChild(testButton);
    card.appendChild(el("button", {type: "button", className: "btn", text: profile.enabled ? "Wyłącz" : "Włącz",
      onclick: function () { toggleExternal(profile); }}));
    card.appendChild(el("button", {type: "button", className: "btn", text: "Edytuj",
      onclick: function () { startExternalEdit(profile); }}));
    if (profile.trusted) {
      card.appendChild(el("button", {type: "button", className: "btn", text: "Cofnij zaufanie",
        onclick: function () { revokeExternalTrust(profile); }}));
    }
    card.appendChild(el("button", {type: "button", className: "btn", text: "Usuń",
      onclick: function () { removeExternal(profile); }}));
    var inspectionView = externalInspectionView(profile);
    if (inspectionView) { card.appendChild(inspectionView); }
    return card;
  }

  function renderExternal(data) {
    externalData = data;
    var known = {};
    (data.profiles || []).forEach(function (item) { known[item.id] = true; });
    Object.keys(externalInspect).forEach(function (id) {
      if (!known[id]) { delete externalInspect[id]; }   // usunięty profil: zapomnij wynik testu
    });
    var list = $("external-list");
    list.textContent = "";
    var invalid = !data.configuration_valid;
    $("external-state").textContent = invalid ? visibleText(
      "Konfiguracja zewnętrznych MCP jest uszkodzona lub w nieznanym formacie (kod: " + data.error +
      "). Nic nie zostało zmienione ani usunięte; popraw config.json ręcznie i odśwież.") : "";
    $("external-save").disabled = invalid;
    if (invalid) { return; }
    if (!data.profiles.length) { list.appendChild(el("p", {text: "Brak zapisanych profili."})); }
    data.profiles.forEach(function (profile) { list.appendChild(externalCard(profile)); });
  }

  function externalProblem(res) {
    if (res.status === 401) { authFailed(); return true; }
    if (res.status === 404 && !(res.json && res.json.error)) {
      showBanner("Zewnętrzne MCP są wyłączone w tej wersji serwera.", true);
      return true;
    }
    return false;
  }

  function refreshExternal() {
    if (externalBusy) { return Promise.resolve(); }
    externalBusy = true;
    return api("GET", "/api/external-mcp").then(function (res) {
      if (externalProblem(res)) { return; }
      if (res.status !== 200 || !res.json.success) {
        showBanner(visibleText((res.json && res.json.error) || ("Błąd HTTP " + res.status)), false);
        return;
      }
      renderExternal(res.json.data);
    }).catch(function () { showBanner("Brak połączenia z serwerem panelu.", false); })
      .then(function () { externalBusy = false; });
  }

  // Zwraca true przy powodzeniu. Konflikt rewizji odświeża widok i nic nie zmienia.
  function externalRequest(path, body) {
    if (externalBusy || !externalData || !externalData.configuration_valid) { return Promise.resolve(false); }
    externalBusy = true;
    body.revision = externalData.revision;
    return api("POST", path, body).then(function (res) {
      externalLastError = String((res.json && res.json.error) || "");
      if (externalProblem(res)) { return false; }
      if (res.status === 409) {
        showBanner("Konfiguracja zmieniła się w międzyczasie. Odświeżono widok; powtórz operację.", true);
        return "conflict";
      }
      if (res.status >= 200 && res.status < 300 && res.json.success) {
        renderExternal(res.json.data);
        hideBanner();
        return true;
      }
      showBanner(visibleText((res.json && res.json.error) || ("Błąd HTTP " + res.status)), false);
      return false;
    }).catch(function () { showBanner("Brak połączenia z serwerem panelu.", false); return false; })
      .then(function (outcome) {
        externalBusy = false;
        if (outcome === "conflict") { return refreshExternal().then(function () { return false; }); }
        return outcome;
      });
  }

  function externalPath(profile, action) {
    return "/api/external-mcp/" + encodeURIComponent(profile.id) + "/" + action;
  }

  function toggleExternal(profile) {
    return externalRequest(externalPath(profile, "enabled"), {enabled: !profile.enabled});
  }

  function revokeExternalTrust(profile) {
    return externalRequest(externalPath(profile, "revoke-trust"), {});
  }

  function removeExternal(profile) {
    if (!window.confirm("Usunąć profil " + visibleText(profile.alias) +
        "? Konfiguracja zostanie zapomniana, a zaufanie utracone.")) {
      return Promise.resolve(false);
    }
    return externalRequest(externalPath(profile, "delete"), {}).then(function (ok) {
      if (ok === true && externalEditing === profile.id) { resetExternalForm(); }
      return ok;
    });
  }

  function resetExternalForm() {
    externalEditing = null;
    EXTERNAL_TEXT_FIELDS.forEach(function (id) { $(id).value = ""; });
    $("external-transport").value = "http";
    $("external-enabled").checked = false;
    $("external-form-title").textContent = "Dodaj profil";
    $("external-save").textContent = "Zapisz profil";
    $("external-cancel").hidden = true;
    $("external-error").textContent = "";
    externalTransportChanged();
  }

  function startExternalEdit(profile) {
    externalEditing = profile.id;
    $("external-alias").value = profile.alias;
    $("external-description").value = profile.description || "";
    $("external-transport").value = profile.transport;
    $("external-url").value = profile.url || "";
    $("external-bearer").value = profile.bearer_env || "";
    $("external-ca").value = profile.ca_file || "";
    $("external-headers").value = refsToLines(profile.headers_env);
    $("external-command").value = profile.command || "";
    $("external-args").value = (profile.args || []).join("\n");
    $("external-cwd").value = profile.cwd || "";
    $("external-env-refs").value = refsToLines(profile.env_refs);
    $("external-enabled").checked = !!profile.enabled;
    $("external-form-title").textContent = "Edytuj profil";
    $("external-save").textContent = "Zapisz zmiany";
    $("external-cancel").hidden = false;
    $("external-error").textContent = "";
    externalTransportChanged();
  }

  function submitExternal(event) {
    event.preventDefault();
    if (externalBusy || !externalData || !externalData.configuration_valid) { return; }
    var built = buildExternalProfile();
    if (built.error) { $("external-error").textContent = built.error; return; }
    $("external-error").textContent = "";
    var editing = externalEditing;
    var current = editing ? externalData.profiles.filter(function (item) { return item.id === editing; })[0] : null;
    if (editing && !current) {
      $("external-error").textContent = "Profil nie istnieje już na liście. Anuluj edycję i odśwież widok.";
      return;
    }
    if (current && current.trusted && !window.confirm("Ten profil jest zaufany. Zmiana ustawień połączenia " +
        "(adres, polecenie, argumenty, katalog, zmienne, nagłówki, CA, transport) zeruje zaufanie i trzeba je " +
        "nadać od nowa. Zmiana samej nazwy, opisu lub włączenia go nie zeruje. Zapisać?")) {
      return;
    }
    $("external-save").disabled = true;
    var path = editing ? externalPath(current, "update") : "/api/external-mcp";
    externalRequest(path, {profile: built.profile}).then(function (ok) {
      if (ok === true) {
        if (editing && externalInspect[editing]) {
          delete externalInspect[editing];   // po edycji wynik testu może być nieaktualny
          renderExternal(externalData);
        }
        resetExternalForm();
      }
      $("external-save").disabled = !externalData || !externalData.configuration_valid;
    });
  }

  function initExternal() {
    if (!$("tab-external")) { return; }
    $("tab-external").addEventListener("click", function () { showTab("external"); });
    $("external-refresh").addEventListener("click", function () { refreshExternal(); });
    $("external-form").addEventListener("submit", submitExternal);
    $("external-transport").addEventListener("change", externalTransportChanged);
    $("external-cancel").addEventListener("click", resetExternalForm);
    externalTransportChanged();
  }

  // --- sesja pracy: jedna na instancję sklr; RAM i ręczne wznowienie ---

  var sessionData = null;
  var sessionSelected = {};
  var sessionBusy = false;
  var sessionCheckpointGeneration = 0;
  var sessionGeneration = 0;

  function clearSessionResume() {
    sessionGeneration += 1;
    $("session-plan").textContent = "";
    $("session-plan-section").hidden = true;
    $("session-saved").textContent = "";
    $("session-saved-section").hidden = true;
    $("session-resume-section").hidden = true;
  }

  function sessionCurrent(generation) {
    return generation === sessionGeneration && state.tab === "session" && !state.stopped;
  }

  function sessionCanPrepare() {
    return sessionData && sessionData.session_id && sessionData.resume_available &&
      sessionData.execution_blocked && !sessionData.resume_pending && sessionData.running_actions === 0 &&
      (sessionData.state === "paused" || sessionData.resume_recovery_required);
  }

  function refreshSessionResume(generation) {
    var data = sessionData;
    if (!data || !sessionCurrent(generation)) { return Promise.resolve(); }
    if (!data.session_id) {
      return api("GET", "/api/work-session/saved").then(function (res) {
        if (!sessionCurrent(generation) || !sessionData || sessionData.session_id) { return; }
        if (res.status === 401) { clearSessionResume(); authFailed(); return; }
        var box = $("session-saved");
        box.textContent = "";
        $("session-saved-section").hidden = false;
        if (res.status !== 200 || !res.json.success || !res.json.data) {
          box.textContent = "Nie można odczytać zapisanych sesji. Odśwież stan."; return;
        }
        var saved = res.json.data;
        (saved.sessions || []).forEach(function (item) {
          var button = el("button", { type: "button", className: "btn", text: "Wybierz zapisaną sesję" });
          button.disabled = sessionBusy || !item.recoverable;
          button.addEventListener("click", function () {
            if (!sessionCurrent(generation) || sessionBusy || !item.recoverable || sessionData.session_id) { return; }
            sessionPost("/api/work-session/recover", {
              session_id: item.session_id, revision: item.revision, confirmed: true
            }, "Przypiąć zapisaną sesję " + visibleText(item.session_id) + " jako zablokowaną? " +
              "RAM został utracony. Nie będzie można przełączyć sesji; potrzebne jest nowe przygotowanie wznowienia.");
          });
          box.appendChild(sessionLine("Sesja", item.session_id));
          box.appendChild(sessionLine("Foldery", (item.roots || []).join(", ")));
          box.appendChild(sessionLine("Rewizja", item.revision));
          box.appendChild(sessionLine("Stan / powód", (item.state || "") + " / " + (item.reason || "")));
          box.appendChild(button);
        });
        if (!(saved.sessions || []).length) { box.textContent = "Brak zapisanych sesji."; }
        if (saved.truncated) { box.appendChild(sessionLine("Lista", "Skrócona do 200 sesji.")); }
      });
    }
    if (data.state !== "active" || data.execution_blocked || !data.resume_available) { return Promise.resolve(); }
    var sid = data.session_id;
    var revision = data.revision;
    return api("GET", "/api/work-session/resume-plan").then(function (res) {
      if (!sessionCurrent(generation) || !sessionData || sessionData.session_id !== sid || sessionData.revision !== revision) { return; }
      if (res.status === 401) { clearSessionResume(); authFailed(); return; }
      var box = $("session-plan");
      box.textContent = "";
      if (res.status !== 200 || !res.json.success) {
        $("session-plan-section").hidden = false;
        box.textContent = "Nie można odczytać planu. Odśwież stan."; return;
      }
      var plan = res.json.data;
      if (!plan || !plan.available) { return; }
      $("session-plan-section").hidden = false;
      if (plan.session_id !== sid || plan.revision !== revision) {
        box.textContent = "Plan dotyczy innego stanu. Odśwież sesję."; return;
      }
      box.appendChild(sessionLine("Plan", plan.plan));
    });
  }

  function prepareSessionResume() {
    if (sessionBusy || !sessionCanPrepare()) { return Promise.resolve(); }
    var data = sessionData;
    var generation = sessionGeneration;
    var supplement = $("session-supplement").value;
    if (supplement.length > 3000) { showBanner("Uzupełnienie przekracza 3000 znaków.", false); return Promise.resolve(); }
    sessionBusy = true;
    sessionButtons();
    return api("GET", "/api/work-session/resume-preview?session_id=" + encodeURIComponent(data.session_id) +
      "&revision=" + encodeURIComponent(data.revision)).then(function (res) {
      if (!sessionCurrent(generation)) { return; }
      if (res.status === 401) { authFailed(); return; }
      if (res.status !== 200 || !res.json.success) {
        showBanner("Nie można przygotować wznowienia. Odśwież stan przed nową decyzją.", false);
        return refreshSession();
      }
      var view = res.json.data;
      if (!view || view.session_id !== data.session_id || view.revision !== data.revision ||
          !Number.isInteger(view.cancel_count) || view.cancel_count < 0) {
        showBanner("Podgląd dotyczy innego stanu. Odśwież sesję.", false); return;
      }
      var missing = view.checkpoint_missing === true;
      if (missing && !window.confirm("Brak potwierdzonego checkpointu w RAM. Przygotować wznowienie bez checkpointu? " +
          "Model musi uwzględnić utratę kontekstu.")) { return; }
      if (!window.confirm("Przygotować wznowienie? Anulujemy " + view.cancel_count +
          " starych zadań oczekujących/queued. Rozpoczęte i zakończone pozostają bez zmian. " +
          "Sesja pozostanie zablokowana do potwierdzenia aktualnego planu przez model.")) { return; }
      return api("POST", "/api/work-session/prepare-resume", {
        session_id: view.session_id, revision: view.revision, supplement: supplement,
        cancel_count: view.cancel_count, allow_missing_checkpoint: missing, confirmed: true
      }).then(function (posted) {
        if (!sessionCurrent(generation)) { return; }
        if (posted.status === 401) { authFailed(); return; }
        if (posted.status === 200 && posted.json.success) {
          $("session-supplement").value = "";
          hideBanner();
        } else {
          showBanner(visibleText((posted.json && posted.json.error) || "Przygotowanie nie powiodło się. Odśwież stan."), false);
        }
        return refreshSession();
      });
    }).catch(function () {
      if (sessionCurrent(generation)) { showBanner("Niepewny wynik przygotowania. Odśwież stan; nie ponawiaj automatycznie.", false); }
    }).then(function () { sessionBusy = false; sessionButtons(); });
  }

  function clearSessionCheckpoint() {
    sessionCheckpointGeneration += 1;
    $("session-checkpoint").textContent = "";
    $("session-checkpoint-section").hidden = true;
  }

  function refreshSessionCheckpoint() {
    clearSessionCheckpoint();
    var data = sessionData;
    if (!data || !data.checkpoint_content_available || state.tab !== "session" || state.stopped) {
      return Promise.resolve();
    }
    var generation = sessionCheckpointGeneration;
    var sid = data.session_id;
    var revision = data.revision;
    $("session-checkpoint-section").hidden = false;
    $("session-checkpoint").textContent = "Odczyt checkpointu…";
    return api("GET", "/api/work-session/checkpoint").then(function (res) {
      if (generation !== sessionCheckpointGeneration || state.tab !== "session") { return; }
      if (res.status === 401) { clearSessionCheckpoint(); authFailed(); return; }
      if (!sessionData || sessionData.session_id !== sid || sessionData.revision !== revision || state.stopped) {
        clearSessionCheckpoint(); return;
      }
      var box = $("session-checkpoint");
      box.textContent = "";
      if (res.status !== 200 || !res.json.success) {
        box.textContent = "Nie można odczytać checkpointu. Odśwież stan."; return;
      }
      var record = res.json.data;
      if (!record || !record.available || record.session_revision !== revision) {
        box.textContent = "Treść niedostępna lub stan się zmienił. Odśwież stan."; return;
      }
      var checkpoint = record.checkpoint;
      box.appendChild(sessionLine("Rewizja zapisu", record.revision));
      box.appendChild(sessionLine("Czas zapisu", record.saved_at));
      box.appendChild(sessionLine("Cel", checkpoint.goal));
      box.appendChild(sessionLine("Etap", checkpoint.stage));
      (checkpoint.findings || []).forEach(function (finding) {
        box.appendChild(sessionLine("Ustalenie", finding.text));
        box.appendChild(sessionLine("Źródło", finding.source));
      });
      box.appendChild(sessionLine("Zadania", (checkpoint.done_task_ids || []).join(", ")));
      (checkpoint.open_questions || []).forEach(function (question) {
        box.appendChild(sessionLine("Pytanie", question));
      });
      (checkpoint.uncertain || []).forEach(function (uncertain) {
        box.appendChild(sessionLine("Niepewny wynik", uncertain));
      });
      box.appendChild(sessionLine("Następny krok", checkpoint.next_step));
    }).catch(function () {
      if (generation === sessionCheckpointGeneration && state.tab === "session") {
        $("session-checkpoint").textContent = "Nie można odczytać checkpointu. Odśwież stan.";
      }
    });
  }

  function sessionChosen() {
    return Object.keys(sessionSelected).filter(function (alias) { return sessionSelected[alias] === true; });
  }

  function sessionButtons() {
    $("session-start").disabled = sessionBusy || !sessionData || !!sessionData.session_id ||
      sessionChosen().length === 0;
    $("session-pause").disabled = sessionBusy || !sessionData || sessionData.state !== "active";
    $("session-prepare").disabled = sessionBusy || !sessionCanPrepare();
    $("session-supplement").disabled = sessionBusy || !sessionCanPrepare();
    $("session-refresh").disabled = sessionBusy;
  }

  function sessionLine(label, value) {
    return el("p", { text: label + ": " + visibleText(value) });
  }

  function renderSession() {
    clearSessionCheckpoint();
    var box = $("session-state");
    var data = sessionData;
    box.textContent = "";
    if (!data) {
      box.appendChild(el("p", { className: "muted", text: "Sesje pracy są wyłączone w tej wersji serwera." }));
    } else if (!data.session_id) {
      box.appendChild(el("p", { className: "muted", text: "Brak przypiętej sesji. Wybierz foldery i rozpocznij sesję." }));
    } else {
      box.appendChild(sessionLine("Stan", data.state));
      box.appendChild(sessionLine("Wykonanie zablokowane", data.execution_blocked ? "tak" : "nie"));
      box.appendChild(sessionLine("Rozpoczęte działania", data.running_actions));
      box.appendChild(sessionLine("Rewizja", data.revision));
      box.appendChild(sessionLine("Foldery", (data.roots || []).join(", ")));
      if (data.reason) { box.appendChild(sessionLine("Powód", data.reason)); }
      var checkpointInfo = " — funkcja zapisu niedostępna; zapis nie jest deklarowany";
      if (data.checkpoint_available) {
        checkpointInfo = data.checkpoint_content_available ?
          " — treść dostępna tylko w RAM" : " — brak potwierdzonej treści w RAM";
      }
      box.appendChild(sessionLine("Checkpoint", (data.checkpoint_state || "brak") + checkpointInfo));
      if (data.ram_lost) { box.appendChild(sessionLine("Restart", "RAM poprzedniego procesu został utracony.")); }
      if (data.resume_pending) {
        box.appendChild(sessionLine("Wznowienie", "Oczekiwanie na model. Przekaż modelowi polecenie pobrania get_resume_context " +
          "i potwierdzenia aktualnego planu. sklr nie budzi modelu. Stan sprawdzisz ręcznie przez Odśwież."));
      } else if (data.resume_recovery_required) {
        box.appendChild(sessionLine("Wznowienie", "Niepewne potwierdzenie; bramka zamknięta. Wymagane nowe jawne przygotowanie."));
      }
      box.appendChild(el("p", { className: "muted", text: (data.resume_available ?
        "Wznowienie wymaga jawnego przygotowania i planu modelu. Przełączenie sesji jest niedostępne. " :
        "Wznowienie i przełączenie sesji są niedostępne w tej wersji. ") +
        "Pauza obejmuje wszystkie rozmowy tej instancji sklr." }));
    }
    $("session-select").hidden = !data || !!data.session_id;
    $("session-resume-section").hidden = !sessionCanPrepare();
    sessionButtons();
  }

  function renderSessionRoots(aliases) {
    var list = $("session-roots");
    var keep = {};
    list.textContent = "";
    aliases.forEach(function (alias) {
      var box = el("input", { type: "checkbox" });
      box.value = alias;
      box.checked = sessionSelected[alias] === true;
      keep[alias] = box.checked;
      box.addEventListener("change", function () {
        sessionSelected[alias] = box.checked;
        sessionButtons();
      });
      list.appendChild(el("label", { className: "check" }, [box, el("span", { text: " " + visibleText(alias) })]));
    });
    sessionSelected = keep;
    if (!aliases.length) {
      list.appendChild(el("p", { className: "muted", text: "Brak dostępnych folderów. Dodaj je w zakładce Foldery." }));
    }
  }

  function refreshSession() {
    clearSessionCheckpoint();
    clearSessionResume();
    var generation = sessionGeneration;
    return api("GET", "/api/work-session").then(function (res) {
      if (!sessionCurrent(generation)) { return null; }
      if (res.status === 401) { authFailed(); return null; }
      if (res.status === 404) { sessionData = null; renderSession(); return null; }
      if (res.status !== 200 || !res.json.success) {
        sessionData = null; renderSession();
        showBanner("Błąd pobierania stanu sesji (HTTP " + res.status + ").", false);
        return null;
      }
      var previous = sessionData;
      sessionData = res.json.data;
      if (!previous || previous.session_id !== sessionData.session_id || previous.revision !== sessionData.revision) {
        $("session-supplement").value = "";
      }
      if (sessionData.session_id) {
        renderSession();
        return Promise.all([refreshSessionCheckpoint(), refreshSessionResume(generation)]);
      }
      return api("GET", "/api/roots").then(function (rootsRes) {
        if (!sessionCurrent(generation)) { return; }
        if (rootsRes.status === 401) { authFailed(); return; }
        var aliases = [];
        if (rootsRes.status === 200 && rootsRes.json.success) {
          aliases = rootsRes.json.data.roots.filter(function (root) { return root.valid === true; })
            .map(function (root) { return root.alias; });
        }
        renderSessionRoots(aliases);
        renderSession();
        return refreshSessionResume(generation);
      });
    }).catch(function () {
      if (sessionCurrent(generation)) {
        sessionData = null; renderSession();
        showBanner("Brak połączenia z serwerem panelu.", false);
      }
    });
  }

  function sessionPost(path, body, question) {
    if (sessionBusy) { return Promise.resolve(false); }
    if (!window.confirm(question)) { return Promise.resolve(false); }
    sessionBusy = true;
    var generation = sessionGeneration;
    sessionButtons();
    return api("POST", path, body).then(function (res) {
      if (!sessionCurrent(generation)) { return null; }
      if (res.status === 401) { authFailed(); return null; }
      if ((res.status === 200 || res.status === 201) && res.json.success) {
        sessionData = res.json.data;
        hideBanner();
        renderSession();
        return null;
      }
      if (res.status === 409) {
        showBanner("Stan sesji się zmienił. Sprawdź odświeżony stan przed kolejną decyzją.", false);
      } else {
        showBanner(visibleText((res.json && res.json.error) || ("Błąd HTTP " + res.status)), false);
      }
      return refreshSession();
    }).catch(function () {
      showBanner("Brak połączenia z serwerem panelu. Odśwież stan sesji.", false);
    }).then(function () {
      sessionBusy = false;
      sessionButtons();
    });
  }

  function initSession() {
    if (!$("tab-session")) { return; }
    $("tab-session").addEventListener("click", function () { showTab("session"); });
    $("session-refresh").addEventListener("click", function () { if (!sessionBusy) { refreshSession(); } });
    $("session-prepare").addEventListener("click", prepareSessionResume);
    $("session-start").addEventListener("click", function () {
      var chosen = sessionChosen();
      if (!chosen.length || !sessionData || sessionData.session_id) { return; }
      sessionPost("/api/work-session/start", { roots: chosen },
        "Rozpocząć sesję pracy dla folderów: " + chosen.join(", ") + "? Pauza obejmie wszystkie rozmowy " +
        "całej instancji sklr. Checkpoint jest tylko w RAM. Wznowienie wymaga przygotowania i planu modelu; przełączenie jest niedostępne.");
    });
    $("session-pause").addEventListener("click", function () {
      if (!sessionData || sessionData.state !== "active") { return; }
      sessionPost("/api/work-session/pause", { session_id: sessionData.session_id, revision: sessionData.revision },
        "Wstrzymać sesję? Nowe działania sklr zostaną zablokowane we wszystkich rozmowach tej instancji. " +
        "Rozpoczęte działania dokończą się w swoich limitach. Checkpoint jest tylko w RAM. " +
        "Dalsza praca pozostanie zablokowana do przygotowania wznowienia i potwierdzenia aktualnego planu modelu.");
    });
    sessionButtons();
  }

  function init() {
    initSkillFilters();
    initExternal();
    initSession();
    $("notify-btn").addEventListener("click", enableNotifications);
    $("status-filter").addEventListener("change", function () { refresh(); });
    $("tab-tasks").addEventListener("click", function () { showTab("tasks"); });
    $("tab-roots").addEventListener("click", function () { showTab("roots"); });
    $("root-form").addEventListener("submit", submitRoot);
    $("tab-skills").addEventListener("click", function () { showTab("skills"); });
    $("skills-refresh").addEventListener("click", refreshSkills);
    if ($("tab-knowledge")) {
      $("tab-knowledge").addEventListener("click", function () { showTab("knowledge"); });
      $("knowledge-refresh").addEventListener("click", refreshKnowledge);
      $("knowledge-form").addEventListener("submit", submitKnowledge);
      $("knowledge-policy-form").addEventListener("submit", submitKnowledgePolicy);
      $("knowledge-policy-root").addEventListener("change", renderKnowledgePolicy);
      $("knowledge-scope").addEventListener("change", function () { knowledgeProfileChanged(true); });
      $("knowledge-profile").addEventListener("change", function () { knowledgeProfileChanged(false); });
      if ($("knowledge-profile").options) { knowledgeProfileChanged(true); }
    }
    document.addEventListener("visibilitychange", function () {
      if (!state.stopped) { tick(); }
    });
    exchangeToken().then(function () {
      if (!state.stopped) { tick(); }
    });
  }

  init();
})();
