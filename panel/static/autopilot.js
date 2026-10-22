/* Panel-only delegation. No persistence, polling, HTML interpolation or model authority. */
(function () {
  "use strict";
  var state = null, resources = [], rules = [], preview = null, busy = false, generation = 0;
  var tools = {
    files: ["list_directory", "read_file", "read_files_batch", "search_code", "edit_file", "create_file", "create_tree", "manage_folder"],
    knowledge: ["knowledge_search", "knowledge_get"], skill_read: ["activate_skill", "read_skill_resource"],
    skill_run: ["run_skill_script"], mcp_discovery: ["list_external_mcp_tools"],
    mcp_call: ["call_external_mcp_tool"], catalog: ["list_roots", "knowledge_bundles", "list_skills", "list_external_mcp"]
  };
  var kinds = { files: "root", knowledge: "bundle", skill_read: "skill", skill_run: "skill", mcp_discovery: "profile", mcp_call: "profile" };
  var kindNames = { files: "Pliki", knowledge: "Wiedza", skill_read: "Odczyt skilla", skill_run: "Skrypt skilla",
    mcp_discovery: "Discovery MCP", mcp_call: "Wywołanie MCP", catalog: "Katalog zasobów" };
  var stateNames = { disabled: "Ręczne zgody", prepared: "Delegacja przygotowana", active: "Autopilot aktywny",
    suspended: "Autopilot wstrzymany", expired: "Delegacja wygasła", exhausted: "Wyczerpany limit operacji",
    revoked: "Zgoda cofnięta", recovery_required: "Wymagane rozstrzygnięcie" };
  function $(id) { return document.getElementById(id); }
  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = String(text); }
    return node;
  }
  function line(box, label, value) {
    var row = element("div", "session-line");
    row.appendChild(element("span", "session-line-label", label));
    row.appendChild(element("span", "", value)); box.appendChild(row);
  }
  function ruleDescription(rule) {
    var resource = rule.root || rule.bundle || rule.skill || rule.profile || (rule.resources || []).join(", ");
    var lines = [(kindNames[rule.kind] || rule.kind) + " · " + resource, "Narzędzia: " + (rule.tools || []).join(", ")];
    if (rule.scope) { lines.push("Zakres: " + (rule.scope.kind === "tree" ? "poddrzewo" : "plik") + " · " + rule.scope.path); }
    if (rule.kind === "files") { lines.push("Pełne nadpisanie: " + (rule.overwrite ? "tak" : "nie")); }
    if (rule.script) { lines.push("Skrypt: " + rule.script, "Argumenty: " + JSON.stringify(rule.args)); }
    if (rule.tool_name) { lines.push("Narzędzie MCP: " + rule.tool_name, "Selektor argumentów: " + JSON.stringify(rule.arguments)); }
    if (rule.max_timeout_s) { lines.push("Timeout wykonawcy: " + rule.max_timeout_s + " s"); }
    return lines.join("\n");
  }
  function delegationSummary(box, data) {
    box.textContent = "";
    line(box, "Cel", data.goal || "—"); line(box, "Plan", data.plan || "—");
    var limits = data.limits || {};
    line(box, "Pierwotne limity", (limits.duration_s / 60) + " min · " + limits.max_operations + " operacji · " + limits.max_running + " działanie naraz");
    ((data.policy && data.policy.rules) || []).forEach(function (rule, index) {
      line(box, "Reguła " + (index + 1), ruleDescription(rule));
    });
  }
  function renderRules() {
    var list = $("ap-rule-list"); list.textContent = "";
    if (!rules.length) { list.appendChild(element("p", "muted", "Brak reguł. Dodaj jawny zakres działań.")); }
    rules.forEach(function (rule, index) {
      var row = element("div", "session-rule");
      row.appendChild(element("div", "", ruleDescription(rule)));
      var button = element("button", "btn", "Usuń regułę " + (index + 1)); button.type = "button";
      button.addEventListener("click", function () {
        if (busy || $("ap-editor").disabled) { return; }
        rules.splice(index, 1); invalidate(); renderRules();
      });
      row.appendChild(button); list.appendChild(row);
    });
    $("ap-rules").textContent = JSON.stringify(rules, null, 2);
  }
  function error(message) { $("ap-error").textContent = message; }
  function invalidate() {
    preview = null;
    $("ap-preview-content").textContent = "";
    $("ap-preview-summary").textContent = "";
    $("ap-preview-section").hidden = true;
    $("ap-approve").disabled = true;
  }
  function identity() {
    return { session_id: state.session_id, revision: state.revision, decision_revision: state.decision_revision };
  }
  async function api(path, body) {
    var response = await fetch("/api/autopilot" + path, {
      method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (response.status === 401) { clearPrivate(); }
    if (response.status === 404) { throw new Error("Autopilot jest wyłączony w konfiguracji."); }
    var payload = await response.json();
    if (!response.ok || !payload.success) { throw new Error(payload.error || "Decyzja niedostępna. Odśwież stan."); }
    return payload.data;
  }
  function render() {
    var paused = state && state.session_state === "paused" && state.running_actions === 0 && !state.resume_pending;
    $("ap-editor").disabled = busy || !paused;
    ["reconfirm", "manual", "acknowledge-recovery"].forEach(function (action) {
      $("ap-" + action).disabled = busy || !paused;
    });
    $("ap-reconfirm").disabled = busy || !paused || !state.policy || ["prepared", "suspended"].indexOf(state.state) < 0;
    $("ap-acknowledge-recovery").disabled = busy || !paused || state.state !== "recovery_required" || state.unresolved.length > 0;
    $("ap-revoke").disabled = busy || !state || !state.contract_id;
    $("ap-approve").disabled = busy || !paused || !preview;
    $("ap-refresh").disabled = busy;
    $("ap-reconfirm").hidden = $("ap-reconfirm").disabled;
    $("ap-revoke").hidden = !state || !state.contract_id;
    $("ap-manual").hidden = !paused;
    $("ap-acknowledge-recovery").hidden = !state || state.state !== "recovery_required";
    $("ap-editor-hint").textContent = paused ? "Określ cel, dodaj reguły i sprawdź podgląd przed zatwierdzeniem." :
      "Aby zmienić delegację, wstrzymaj sesję i poczekaj na zakończenie rozpoczętych działań.";
    var metrics = $("ap-state"); metrics.textContent = "";
    if (!state) { metrics.appendChild(element("p", "muted", "Stan delegacji niedostępny. Odśwież stan.")); }
    else {
      var values = [[stateNames[state.state] || "Stan: " + state.state, "Tryb pracy"]];
      if (state.contract_id) {
        var seconds = Math.max(0, Math.floor(state.remaining_s));
        values.push([Math.floor(seconds / 60) + " min " + (seconds % 60) + " s", "Pozostały czas"],
          [state.remaining_operations, "Pozostałe operacje"], [state.running, "Rozpoczęte działania"],
          [state.used + " / " + state.reserved, "Zużyte / zarezerwowane"]);
      }
      values.forEach(function (value) {
        var metric = element("div", "session-metric");
        metric.appendChild(element("strong", "", value[0])); metric.appendChild(element("span", "", value[1]));
        metrics.appendChild(metric);
      });
      if (state.reason) {
        var reasons = { scope_mismatch: "Działanie poza zatwierdzonym zakresem", user_requested_pause: "Pauza na żądanie użytkownika",
          server_restarted: "Restart serwera", revoked: "Zgoda cofnięta", expired: "Upłynął czas delegacji", exhausted: "Wyczerpany limit operacji" };
        metrics.appendChild(element("p", "muted", reasons[state.reason] || "Powód: " + state.reason));
      }
      if (state.deadline_utc) { metrics.appendChild(element("p", "muted", "Termin: " + new Date(state.deadline_utc * 1000).toLocaleString())); }
    }
    $("ap-approved-section").hidden = !state || !state.policy;
    $("ap-approved-summary").textContent = ""; $("ap-approved-content").textContent = "";
    if (state && state.policy) {
      delegationSummary($("ap-approved-summary"), state);
      $("ap-approved-content").textContent = JSON.stringify({ goal: state.goal, plan: state.plan,
        policy: state.policy, bindings: state.bindings, limits: state.limits }, null, 2);
    }
    $("ap-recovery-section").hidden = !state || state.state !== "recovery_required" && !state.unresolved.length;
    var box = $("ap-unresolved"); box.textContent = "";
    ((state && state.unresolved) || []).forEach(function (row) {
      var button = document.createElement("button"); button.type = "button"; button.className = "btn";
      button.textContent = "Rozstrzygnięto skutek operacji " + row.operation_id + " (" + row.tool + ")";
      button.disabled = busy || !paused;
      button.addEventListener("click", function () { decide("resolve", { operation_id: row.operation_id }); });
      box.appendChild(button);
    });
  }
  function choices() {
    var kind = $("ap-kind").value, box = $("ap-resource"); box.textContent = "";
    $("ap-scope-fields").hidden = ["files", "knowledge", "skill_read"].indexOf(kind) < 0;
    $("ap-overwrite-field").hidden = kind !== "files";
    $("ap-script-fields").hidden = kind !== "skill_run";
    $("ap-mcp-fields").hidden = kind !== "mcp_call";
    $("ap-timeout-field").hidden = ["skill_run", "mcp_discovery", "mcp_call"].indexOf(kind) < 0;
    resources.filter(function (row) { return kind === "catalog" || row.kind === kinds[kind]; }).forEach(function (row) {
      var option = document.createElement("option"); option.value = row.key;
      option.textContent = row.key + (row.path ? " — " + row.path + " (" + row.mode + ")" : "");
      box.appendChild(option);
    });
    var list = $("ap-tools"); list.textContent = "";
    list.appendChild(element("legend", "", "Jawnie delegowane narzędzia"));
    tools[kind].forEach(function (name) {
      var label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.value = name; input.name = "ap-tool";
      label.appendChild(input); label.appendChild(document.createTextNode(name)); list.appendChild(label);
    });
    mcpChoices();
  }
  function mcpChoices() {
    var box = $("ap-mcp-tools"); box.textContent = "";
    var selected = Array.from($("ap-resource").selectedOptions).map(function (row) { return row.value; });
    resources.filter(function (row) { return selected.indexOf(row.key) >= 0; }).forEach(function (row) {
      (row.tools || []).forEach(function (tool) {
        var option = document.createElement("option"); option.value = tool.name; box.appendChild(option);
      });
    });
  }
  async function refresh() {
    if (busy) { return; }
    var current = ++generation; invalidate(); busy = true; render();
    try {
      var data = await api("");
      if (current !== generation) { return; }
      state = data; $("autopilot-section").hidden = false;
      var currentResources = await api("/resources");
      if (current !== generation) { return; }
      resources = currentResources;
      choices(); error("");
    } catch (err) { if (current === generation) { state = null; error(err.message); } }
    finally { busy = false; render(); }
  }
  function addRule() {
    invalidate(); error("");
    try {
      var kind = $("ap-kind").value;
      var selected = Array.from($("ap-resource").selectedOptions).map(function (row) { return row.value; });
      var chosen = Array.from(document.querySelectorAll('input[name="ap-tool"]:checked')).map(function (row) { return row.value; });
      if (!chosen.length || !selected.length || kind !== "catalog" && selected.length !== 1) { throw new Error("Wybierz narzędzia i jeden zasób (katalog: wiele zasobów)."); }
      var nextId = 1;
      while (rules.some(function (rule) { return rule.id === "rule-" + nextId; })) { nextId += 1; }
      var rule = { id: "rule-" + nextId, kind: kind, tools: chosen };
      if (kind === "catalog") { rule.resources = selected; }
      else { rule[kinds[kind]] = selected[0]; }
      if (["files", "knowledge", "skill_read"].indexOf(kind) >= 0) { rule.scope = { kind: $("ap-scope-kind").value, path: $("ap-scope").value }; }
      if (kind === "files") { rule.overwrite = $("ap-overwrite").checked; }
      if (kind === "skill_run") { rule.script = $("ap-script").value; rule.args = JSON.parse($("ap-args").value); }
      if (["skill_run", "mcp_discovery", "mcp_call"].indexOf(kind) >= 0) { rule.max_timeout_s = Number($("ap-timeout").value); }
      if (kind === "mcp_call") { rule.tool_name = $("ap-mcp-tool").value; rule.arguments = JSON.parse($("ap-selector").value); }
      rules.push(rule); renderRules();
    } catch (err) { error(err.message); }
  }
  async function prepare() {
    if (busy || !state) { return; }
    var current = generation;
    invalidate(); busy = true; render();
    try {
      var data = await api("/preview", Object.assign(identity(), { goal: $("ap-goal").value, plan: $("ap-plan").value,
        limits: { duration_s: Number($("ap-minutes").value) * 60, max_operations: Number($("ap-operations").value), max_running: 1 },
        policy: { version: 1, rules: rules } }));
      if (current !== generation) { return; }
      preview = data;
      $("ap-preview-content").textContent = "Podgląd do zatwierdzenia (ważny 5 minut):\n" + JSON.stringify(preview, null, 2);
      delegationSummary($("ap-preview-summary"), preview);
      $("ap-preview-section").hidden = false;
      error("");
    } catch (err) { if (current === generation) { error(err.message); } }
    finally { busy = false; render(); }
  }
  async function decide(action, extra) {
    if (busy || !state || !window.confirm("Potwierdzić decyzję: " + action + "? Dotyczy całej instancji. Rozpoczęte skutki nie są cofane; operacje nie będą ponawiane.")) { return; }
    busy = true; render();
    var current = generation;
    var failure = "";
    try {
      await api("/" + action, Object.assign(identity(), { confirmed: true }, extra || {}));
      if (current !== generation) { return; }
      invalidate(); error("");
    } catch (err) { invalidate(); failure = err.message + " Sprawdź stan przed kolejną decyzją."; }
    finally { busy = false; state = null; render(); }
    // Read-only refresh; never retry a mutation after an uncertain response.
    if (current !== generation) { return; }
    await refresh();
    if (failure) { error(failure); }
  }
  if (!$("autopilot-section")) { return; }
  $("tab-session").addEventListener("click", refresh);
  $("session-refresh").addEventListener("click", refresh);
  $("ap-refresh").addEventListener("click", refresh);
  $("ap-kind").addEventListener("change", choices);
  $("ap-resource").addEventListener("change", mcpChoices);
  $("ap-editor").addEventListener("input", invalidate);
  $("ap-editor").addEventListener("change", invalidate);
  $("ap-add").addEventListener("click", addRule);
  $("ap-clear").addEventListener("click", function () { rules = []; renderRules(); invalidate(); });
  $("ap-preview").addEventListener("click", prepare);
  $("ap-approve").addEventListener("click", function () { if (preview) { decide("approve", { preview_id: preview.preview_id }); } });
  ["reconfirm", "revoke", "manual", "acknowledge-recovery"].forEach(function (action) {
    $("ap-" + action).addEventListener("click", function () { decide(action); });
  });
  function clearPrivate() {
    generation++; state = null; resources = []; rules = []; invalidate();
    $("autopilot-section").hidden = true;
    $("ap-approved-summary").textContent = ""; $("ap-approved-content").textContent = "";
    $("ap-approved-section").hidden = true;
    $("ap-rule-list").textContent = "";
    ["ap-goal", "ap-plan", "ap-args", "ap-selector", "ap-script", "ap-mcp-tool", "ap-scope", "ap-rules", "ap-state", "ap-unresolved"].forEach(function (id) {
      var node = $(id); if ("value" in node) { node.value = ""; } else { node.textContent = ""; }
    });
    $("ap-resource").textContent = ""; $("ap-mcp-tools").textContent = "";
  }
  window.addEventListener("pagehide", clearPrivate);
  window.addEventListener("panel-auth-failed", clearPrivate);
  window.addEventListener("panel-session-changed", refresh);
  renderRules();
})();
