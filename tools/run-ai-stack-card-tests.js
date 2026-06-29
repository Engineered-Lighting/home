#!/usr/bin/env node
/* Pure local tests for app/src/home-ai-stack.jsx recovery UI contracts. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-ai-stack.jsx");
const APP_SRC = path.join(REPO, "app", "src", "home-app.jsx");
const source = fs.readFileSync(SRC, "utf8");
const appSource = fs.readFileSync(APP_SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function sliceBetween(startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  if (start < 0) throw new Error("missing start marker: " + startNeedle);
  const end = source.indexOf(endNeedle, start);
  if (end < 0) throw new Error("missing end marker after " + startNeedle + ": " + endNeedle);
  return source.slice(start, end);
}

function sliceBetweenIn(src, startNeedle, endNeedle) {
  const start = src.indexOf(startNeedle);
  if (start < 0) throw new Error("missing start marker: " + startNeedle);
  const end = src.indexOf(endNeedle, start);
  if (end < 0) throw new Error("missing end marker after " + startNeedle + ": " + endNeedle);
  return src.slice(start, end);
}

function loadHelpers() {
  const script = [
    sliceBetween("const _STARTABLE", "/* Phase 2"),
    "Object.assign(window, { _STARTABLE, _RESTARTABLE, _STOPPABLE, AI_STACK_SERVICE_RESTARTS, AI_STACK_VERBS, _aiStackServiceRestartSpec, _aiStackConfirmButtonLabel, _overallTone, _serviceTone, _serviceState, _fmtElapsed, _aiStackCardActionState, _aiStackLogsStreamUrl, _aiStackEmptyState, _aiStackFailureDiagnosis });",
  ].join("\n\n");
  const sandbox = {
    window: {},
    Set,
    Math,
    isFinite,
    Date: {
      parse: Date.parse,
      now: () => Date.parse("2026-06-23T12:01:05Z"),
    },
  };
  vm.runInNewContext(script, sandbox, { filename: "home-ai-stack.helpers.js" });
  return sandbox.window;
}

function loadHomeAppHelpers() {
  const script = [
    sliceBetweenIn(appSource, "function _aiStackDepHealthItem", "function _escapeRegexPart"),
    "Object.assign(window, { _aiStackDepHealthItem });",
  ].join("\n\n");
  const sandbox = { window: {} };
  vm.runInNewContext(script, sandbox, { filename: "home-app.ai-stack.helpers.js" });
  return sandbox.window;
}

const H = loadHelpers();
const AppH = loadHomeAppHelpers();

process.stdout.write("\nai_stack_card_static_contract_test\n");
assert("AiStackCard exported", source.includes("Object.assign(window, { AiStackCard });"));
assert("token setup hint is present", source.includes("Use /stack-token with the token from /opt/home-ai-voice/.env."));
assert("supervisor offline copy is present", source.includes("Cannot reach the stack supervisor at :8093."));
assert("start action label is present", source.includes("start ai stack"));
assert("restart action label is present", source.includes('_aiStackConfirmButtonLabel(confirmVerb, "restart", "restart")'));
assert("free gpu action label is present", source.includes("free gpu"));
assert("stop action label is present", source.includes("stop ai stack"));
assert("start confirm handler is wired", source.includes('confirmOrDispatch("start")'));
assert("restart confirm handler is wired", source.includes('confirmOrDispatch("restart")'));
assert("stop confirm handler is wired", source.includes('confirmOrDispatch("stop")'));
assert("free_gpu confirm handler is wired", source.includes('confirmOrDispatch("free_gpu")'));
assert("old stop modal path removed", !source.includes("setConfirmingStop") && !source.includes("_StopConfirmModal"));
assert("card delegates to HomeStackActions", source.includes("const HSA = window.HomeStackActions"));
assert("missing HomeStackActions surfaces error", source.includes("HomeStackActions not loaded"));
assert("SSE stream uses bearer auth", source.includes("{ Authorization: `Bearer ${stackToken}` }"));
assert("SSE stream URL uses normalized helper", source.includes("const url = _aiStackLogsStreamUrl(supervisorUrl, taskId);"));
assert("SSE stream no longer concatenates supervisor URL directly",
  !source.includes("`${supervisorUrl}/api/stack/logs/stream"));
assert("AiStackCard accepts explicit status errors", source.includes("aiStackStatusError = null"));
assert("AiStackCard forwards status errors to empty-state helper", source.includes("statusError: aiStackStatusError"));
assert("app stores AI stack status error separately",
  appSource.includes("const [aiStackStatusError, setAiStackStatusError] = useState(null);"));
assert("app passes AI stack status error into card",
  appSource.includes("aiStackStatusError={aiStackStatusError}"));
assert("app records rejected stack token",
  appSource.includes('setAiStackStatusError("auth")'));
assert("app records rate-limited stack auth",
  appSource.includes('setAiStackStatusError("rate_limited")'));
assert("app records stack status fetch failure",
  appSource.includes('setAiStackStatusError("fetch_failed")'));
assert("terminal failure diagnosis helper is present", source.includes("function _aiStackFailureDiagnosis"));
assert("terminal renders diagnosis details", source.includes("terminalDiagnosis.detail"));
assert("terminal renders next-step guidance", source.includes("next: {terminalDiagnosis.next}"));

process.stdout.write("\nai_stack_card_s85_verb_contract_test\n");
const S85_VERBS = [
  "start",
  "stop",
  "restart",
  "free_gpu",
  "restart_vllm",
  "restart_parakeet",
  "restart_kokoro",
  "restart_vision_sidecar",
  "restart_metrics_sidecar",
];
assert("S85 verb registry is exact",
  JSON.stringify(H.AI_STACK_VERBS) === JSON.stringify(S85_VERBS),
  H.AI_STACK_VERBS);
assert("armed confirm label is exact",
  H._aiStackConfirmButtonLabel("start", "start", "start ai stack") === "\u2713 confirm");
assert("unarmed confirm label preserves action label",
  H._aiStackConfirmButtonLabel(null, "start", "start ai stack") === "start ai stack");
assert("confirm state is stored in React state", source.includes("const [confirmVerb, setConfirmVerb] = React.useState(null);"));
assert("confirm click arms before dispatch", source.includes("if (confirmVerb !== verb)") && source.includes("setConfirmVerb(verb);"));
assert("confirm click uses 3s timeout", source.includes("}, 3000);"));
assert("confirmed click calls dispatch once", source.includes("setConfirmVerb(null);") && source.includes("dispatch(verb);"));
assert("free_gpu dispatches to HomeStackActions.freeGpu",
  source.includes('verb === "free_gpu"') && source.includes("HSA.freeGpu({ supervisorUrl, stackToken })"));
assert("service restart dispatches through HomeStackActions.serviceRestart",
  source.includes("else if (serviceSpec) result = await HSA.serviceRestart({ supervisorUrl, stackToken, service: serviceSpec.service });"));
assert("one-shot service/free_gpu responses finish without requiring task_id",
  source.includes('kind: "done"') && source.includes("exit_code: result.body?.exit_code ?? 0") &&
    !source.includes("no task_id in body"));
const S85_SERVICES = {
  restart_vllm: "vllm",
  restart_parakeet: "wyoming-parakeet",
  restart_kokoro: "kokoro",
  restart_vision_sidecar: "vision-sidecar",
  restart_metrics_sidecar: "metrics-sidecar",
};
for (const [verb, service] of Object.entries(S85_SERVICES)) {
  const spec = H._aiStackServiceRestartSpec(verb);
  assert(`${verb} maps to ${service}`, spec && spec.service === service, spec);
}
assert("unknown service restart verb is rejected",
  H._aiStackServiceRestartSpec("restart_everything") === null);
assert("service restart buttons are rendered from registry",
  source.includes("AI_STACK_SERVICE_RESTARTS.map((spec)") &&
    source.includes("onClick={() => confirmOrDispatch(spec.verb)}"));

process.stdout.write("\nai_stack_dependency_strip_contract_test\n");
assert("Metrics strip uses shared AI stack dependency helper",
  appSource.includes("items.push(_aiStackDepHealthItem(aiStackOnline, aiStackState, aiStackStatusError));"));
assert("unreachable supervisor chip is critical",
  JSON.stringify(AppH._aiStackDepHealthItem(false, null)) ===
    JSON.stringify({ label: "ai stack", state: "unreachable", tone: "crit" }));
assert("unknown supervisor chip stays idle",
  JSON.stringify(AppH._aiStackDepHealthItem(null, null)) ===
    JSON.stringify({ label: "ai stack", state: "unknown", tone: "idle" }));
assert("online supervisor without token/state chip is idle no-token",
  JSON.stringify(AppH._aiStackDepHealthItem(true, null)) ===
    JSON.stringify({ label: "ai stack", state: "no token", tone: "idle" }));
assert("rejected stack token chip is critical",
  JSON.stringify(AppH._aiStackDepHealthItem(true, null, "auth")) ===
    JSON.stringify({ label: "ai stack", state: "auth failed", tone: "crit" }));
assert("rate-limited stack auth chip is warning",
  JSON.stringify(AppH._aiStackDepHealthItem(true, null, "rate_limited")) ===
    JSON.stringify({ label: "ai stack", state: "rate limited", tone: "warn" }));
assert("generic stack status failure chip is warning",
  JSON.stringify(AppH._aiStackDepHealthItem(true, null, "http_503")) ===
    JSON.stringify({ label: "ai stack", state: "status unavailable", tone: "warn" }));
assert("ready dependency chip is ok",
  AppH._aiStackDepHealthItem(true, { overall: "ready" }).tone === "ok");
for (const state of ["warming", "partial", "starting", "stopping"]) {
  assert(`${state} dependency chip is warn`,
    AppH._aiStackDepHealthItem(true, { overall: state }).tone === "warn");
}
for (const state of ["offline", "down", "error"]) {
  assert(`${state} dependency chip is crit`,
    AppH._aiStackDepHealthItem(true, { overall: state }).tone === "crit");
}
assert("unexpected dependency chip state is idle",
  AppH._aiStackDepHealthItem(true, { overall: "mystery" }).tone === "idle");

process.stdout.write("\nai_stack_empty_state_test\n");
function emptyState(args) {
  return H._aiStackEmptyState(args);
}

let empty = emptyState({ tokenConfigured: false, aiStackOnline: true, aiStackState: null });
assert("missing stack token empty state warns", empty.badge.text === "not configured" && empty.badge.tone === "warn", empty);
assert("missing stack token empty state points at token command", empty.body.includes("/stack-token"), empty);
empty = emptyState({ tokenConfigured: true, aiStackOnline: null, aiStackState: null });
assert("polling empty state is neutral", empty.badge.text === "polling" && empty.badge.tone === "ice", empty);
empty = emptyState({ tokenConfigured: true, aiStackOnline: false, aiStackState: null });
assert("offline supervisor empty state is critical", empty.badge.text === "supervisor offline" && empty.badge.tone === "crit", empty);
assert("offline supervisor empty state names supervisor", empty.body.includes("Cannot reach the stack supervisor"), empty);
empty = emptyState({ tokenConfigured: true, aiStackOnline: true, aiStackState: null, statusError: "auth" });
assert("rejected token empty state is critical", empty.badge.text === "auth failed" && empty.badge.tone === "crit", empty);
assert("rejected token empty state names STACK_TOKEN", empty.body.includes("STACK_TOKEN was rejected"), empty);
empty = emptyState({ tokenConfigured: true, aiStackOnline: true, aiStackState: null, statusError: "rate_limited" });
assert("rate-limited empty state is warning", empty.badge.text === "rate limited" && empty.badge.tone === "warn", empty);
empty = emptyState({ tokenConfigured: true, aiStackOnline: true, aiStackState: null, statusError: "http_503" });
assert("generic status failure empty state is warning", empty.badge.text === "status unavailable" && empty.badge.tone === "warn", empty);
assert("generic status failure empty state includes reason", empty.body.includes("http_503"), empty);
assert("loaded stack state suppresses empty state",
  emptyState({ tokenConfigured: true, aiStackOnline: true, aiStackState: { overall: "ready" }, statusError: "auth" }) === null);

process.stdout.write("\nai_stack_failure_diagnosis_test\n");
let diagnosis = H._aiStackFailureDiagnosis({
  action: { kind: "done", verb: "start", exit_code: 1 },
  logLines: [
    "Container hav-kokoro-tts Starting",
    "Container hav-chatterbox-tts Starting",
    "Container hav-wyoming-parakeet Starting",
    "Container hav-vllm Starting",
    "Error response from daemon: failed to create task for container: failed to create shim task: OCI runtime create failed: runc create failed: unable to start container process: error during container init: error running prestart hook #0: exit status 1. stdout: . stderr: Auto-detected mode as 'legacy'",
  ],
});
assert("OCI/NVIDIA prestart failure is classified as GPU runtime", diagnosis.code === "gpu_runtime", diagnosis);
assert("GPU runtime diagnosis confirms supervisor dispatch worked",
  diagnosis.detail.includes("app reached the supervisor") && diagnosis.detail.includes("Docker accepted"), diagnosis);
assert("GPU runtime diagnosis points at NVIDIA toolkit/runtime",
  diagnosis.next.includes("NVIDIA Container Toolkit") && diagnosis.next.includes("Docker GPU runtime"), diagnosis);
diagnosis = H._aiStackFailureDiagnosis({
  action: { kind: "done", verb: "start", exit_code: 1 },
  logLines: ["write /var/lib/docker/tmp: no space left on device"],
});
assert("disk-full failure is classified", diagnosis.code === "disk_full", diagnosis);
diagnosis = H._aiStackFailureDiagnosis({
  action: { kind: "done", verb: "start", exit_code: 1 },
  logLines: ["listen tcp 0.0.0.0:8000: bind: address already in use"],
});
assert("port conflict failure is classified", diagnosis.code === "port_conflict", diagnosis);
diagnosis = H._aiStackFailureDiagnosis({
  action: { kind: "error", verb: "start", error: "Cannot connect to the Docker daemon. Is the docker daemon running?" },
  logLines: [],
});
assert("docker unavailable failure is classified", diagnosis.code === "docker_unavailable", diagnosis);
assert("successful stack action suppresses failure diagnosis",
  H._aiStackFailureDiagnosis({ action: { kind: "done", verb: "start", exit_code: 0 }, logLines: [] }) === null);
diagnosis = H._aiStackFailureDiagnosis({
  action: { kind: "done", verb: "start", exit_code: 1 },
  logLines: ["unexpected failure"],
});
assert("unknown failed action still gets generic diagnosis", diagnosis.code === "unknown", diagnosis);

process.stdout.write("\nai_stack_card_tone_and_service_test\n");
assert("ready overall tone ok", H._overallTone("ready") === "ok");
for (const state of ["warming", "partial", "starting", "stopping"]) {
  assert(`${state} overall tone warn`, H._overallTone(state) === "warn");
}
for (const state of ["offline", "down", "error"]) {
  assert(`${state} overall tone crit`, H._overallTone(state) === "crit");
}
assert("unknown overall tone idle", H._overallTone("unknown") === "idle");
assert("unexpected overall tone idle", H._overallTone("mystery") === "idle");

assert("missing container is crit", H._serviceTone({ container: "missing" }) === "crit");
assert("exited container is crit", H._serviceTone({ container: "exited" }) === "crit");
assert("starting container is warn", H._serviceTone({ container: "starting" }) === "warn");
assert("probe fail is warn", H._serviceTone({ container: "running", probe: "fail" }) === "warn");
assert("unhealthy service is crit", H._serviceTone({ container: "running", health: "unhealthy" }) === "crit");
assert("starting health is warn", H._serviceTone({ container: "running", health: "starting" }) === "warn");
assert("healthy service is ok", H._serviceTone({ container: "running", health: "healthy", probe: "ok" }) === "ok");

assert("missing service state text", H._serviceState({ container: "missing" }) === "missing");
assert("exited service state text", H._serviceState({ container: "exited" }) === "exited");
assert("starting service state text", H._serviceState({ container: "starting" }) === "starting");
assert("probe failure prefers detail", H._serviceState({ probe: "fail", detail: "http 503" }) === "http 503");
assert("probe failure falls back", H._serviceState({ probe: "fail" }) === "probe fail");
assert("unhealthy service state text", H._serviceState({ health: "unhealthy" }) === "unhealthy");
assert("starting health state text", H._serviceState({ health: "starting" }) === "warming");
assert("healthy fallback state text", H._serviceState({}) === "up");

process.stdout.write("\nai_stack_card_elapsed_test\n");
assert("empty elapsed input returns empty", H._fmtElapsed("") === "");
assert("invalid elapsed input returns empty", H._fmtElapsed("not a date") === "");
assert("sub-minute elapsed renders seconds", H._fmtElapsed("2026-06-23T12:00:20Z") === "45s");
assert("multi-minute elapsed renders minutes and seconds", H._fmtElapsed("2026-06-23T11:59:00Z") === "2m 5s");
assert("future elapsed clamps to zero", H._fmtElapsed("2026-06-23T12:02:00Z") === "0s");

process.stdout.write("\nai_stack_card_log_stream_url_test\n");
assert("log stream URL trims trailing supervisor slash",
  H._aiStackLogsStreamUrl("http://127.0.0.1:8093/", "task/1") ===
    "http://127.0.0.1:8093/api/stack/logs/stream?task_id=task%2F1");
H.HomeStackActions = {
  logsStreamUrl: ({ supervisorUrl, taskId }) => `delegated:${supervisorUrl}|${taskId}`,
};
assert("log stream URL delegates to HomeStackActions when loaded",
  H._aiStackLogsStreamUrl("http://x/", "abc") === "delegated:http://x/|abc");
delete H.HomeStackActions;

process.stdout.write("\nai_stack_card_action_state_test\n");
function actions(overall, aiStackState, action) {
  return H._aiStackCardActionState(overall, aiStackState || {}, action || { kind: "idle" });
}

for (const state of ["offline", "down", "partial", "error", "unknown"]) {
  const a = actions(state);
  assert(`${state} shows start only`, a.showStart && !a.showRestart && !a.showStop && !a.showProgress, a);
}
assert("down is explicitly startable", H._STARTABLE.has("down"));

let a = actions("ready");
assert("ready shows restart, stop, free-gpu, and service restart controls",
  !a.showStart && a.showRestart && a.showStop && a.showFreeGpu && a.showServiceRestarts && !a.showProgress, a);
a = actions("warming");
assert("warming shows restart, stop, free-gpu, and service restart controls",
  !a.showStart && a.showRestart && a.showStop && a.showFreeGpu && a.showServiceRestarts && !a.showProgress, a);
a = actions("starting");
assert("starting without in-flight exposes no action row",
  !a.showStart && !a.showRestart && !a.showStop && !a.showFreeGpu && !a.showServiceRestarts && !a.showProgress, a);
a = actions("ready", { in_flight: { verb: "restart" } });
assert("supervisor in-flight hides controls and shows progress",
  !a.showStart && !a.showRestart && !a.showStop && !a.showFreeGpu && !a.showServiceRestarts && a.showProgress, a);
a = actions("offline", {}, { kind: "starting", verb: "start" });
assert("local starting hides start and shows progress", !a.showStart && a.showProgress, a);
a = actions("offline", {}, { kind: "streaming", verb: "start" });
assert("local streaming hides start and shows progress", !a.showStart && a.showProgress, a);
a = actions("offline", {}, { kind: "done", verb: "start" });
assert("done action exposes terminal result", a.showStart && !a.showProgress && a.showTerminal, a);
a = actions("offline", {}, { kind: "error", verb: "start" });
assert("error action exposes terminal result", a.showStart && !a.showProgress && a.showTerminal, a);

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
}
console.log(`\n${passes} pass . ${fails} fail`);
process.exit(fails ? 1 : 0);
