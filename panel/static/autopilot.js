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
  function $(id) { return document.getElementById(id); }
  function error(message) { $("ap-error").textContent = message; }
  function invalidate() {
    preview = null;
    $("ap-preview-content").textContent = "";
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
    $("ap-state").textContent = state ? "Tryb: " + state.state + ". Powód: " + (state.reason || "brak") +
      ". Pozostało " + Math.max(0, Math.floor(state.remaining_s)) + " s i " + state.remaining_operations +
      " operacji. Zużyte: " + state.used + ", rezerwacje: " + state.reserved + ", rozpoczęte: " + state.running +
      ". Nowe wykonania " + (state.execution_blocked ? "zablokowane." : "dopuszczane według stanu sesji.") : "Stan niedostępny.";
    if (state && state.deadline_utc) { $("ap-state").textContent += " Termin: " + new Date(state.deadline_utc * 1000).toLocaleString() + "."; }
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
    resources.filter(function (row) { return kind === "catalog" || row.kind === kinds[kind]; }).forEach(function (row) {
      var option = document.createElement("option"); option.value = row.key;
      option.textContent = row.key + (row.path ? " — " + row.path + " (" + row.mode + ")" : "");
      box.appendChild(option);
    });
    var list = $("ap-tools"); list.textContent = "Jawnie delegowane narzędzia: ";
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
      if (state.policy) {
        $("ap-preview-content").textContent = "Zatwierdzony zakres i pierwotne limity:\n" +
          JSON.stringify({ goal: state.goal, plan: state.plan, policy: state.policy, bindings: state.bindings, limits: state.limits }, null, 2);
      }
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
      var rule = { id: "rule-" + (rules.length + 1), kind: kind, tools: chosen };
      if (kind === "catalog") { rule.resources = selected; }
      else { rule[kinds[kind]] = selected[0]; }
      if (["files", "knowledge", "skill_read"].indexOf(kind) >= 0) { rule.scope = { kind: $("ap-scope-kind").value, path: $("ap-scope").value }; }
      if (kind === "files") { rule.overwrite = $("ap-overwrite").checked; }
      if (kind === "skill_run") { rule.script = $("ap-script").value; rule.args = JSON.parse($("ap-args").value); }
      if (["skill_run", "mcp_discovery", "mcp_call"].indexOf(kind) >= 0) { rule.max_timeout_s = Number($("ap-timeout").value); }
      if (kind === "mcp_call") { rule.tool_name = $("ap-mcp-tool").value; rule.arguments = JSON.parse($("ap-selector").value); }
      rules.push(rule); $("ap-rules").textContent = JSON.stringify(rules, null, 2);
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
  $("ap-clear").addEventListener("click", function () { rules = []; $("ap-rules").textContent = ""; invalidate(); });
  $("ap-preview").addEventListener("click", prepare);
  $("ap-approve").addEventListener("click", function () { if (preview) { decide("approve", { preview_id: preview.preview_id }); } });
  ["reconfirm", "revoke", "manual", "acknowledge-recovery"].forEach(function (action) {
    $("ap-" + action).addEventListener("click", function () { decide(action); });
  });
  function clearPrivate() {
    generation++; state = null; resources = []; rules = []; invalidate();
    $("autopilot-section").hidden = true;
    ["ap-goal", "ap-plan", "ap-args", "ap-selector", "ap-script", "ap-mcp-tool", "ap-scope", "ap-rules", "ap-state", "ap-unresolved"].forEach(function (id) {
      var node = $(id); if ("value" in node) { node.value = ""; } else { node.textContent = ""; }
    });
    $("ap-resource").textContent = ""; $("ap-mcp-tools").textContent = "";
  }
  window.addEventListener("pagehide", clearPrivate);
  window.addEventListener("panel-auth-failed", clearPrivate);
})();
