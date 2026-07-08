#!/usr/bin/env node
/* Local source contracts for DOC-S105 through DOC-S110 outage/recovery stories. */
"use strict";

const fs = require("fs");
const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src", "home-capabilities.js"));

const REPO = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(REPO, "app", "src", "home-app.jsx"), "utf8");
const LIGHTS = fs.readFileSync(path.join(REPO, "app", "src", "home-lights.jsx"), "utf8");
const CONTROL = fs.readFileSync(path.join(REPO, "app", "src", "home-control.jsx"), "utf8");
const VISION = fs.readFileSync(path.join(REPO, "app", "src", "home-vision.jsx"), "utf8");
const S2S = fs.readFileSync(path.join(REPO, "app", "src", "home-s2s.jsx"), "utf8");
const SSE = fs.readFileSync(path.join(REPO, "app", "src", "home-sse-fetch.js"), "utf8");
const CONST = fs.readFileSync(path.join(REPO, "ha-config", "extended_openai_conversation", "const.py"), "utf8");
const WORLD_STATE = fs.readFileSync(path.join(REPO, "ha-config", "extended_openai_conversation", "functions", "world_state.py"), "utf8");
const FRIGATE_TOOL = fs.readFileSync(path.join(REPO, "ha-config", "extended_openai_conversation", "functions", "frigate.py"), "utf8");
const SCENARIOS = fs.readFileSync(path.join(REPO, "docs", "SCENARIO-TESTS.md"), "utf8");
const EVIDENCE = fs.readFileSync(path.join(REPO, "tools", "scenario-evidence-map.json"), "utf8");
const WORLD_STATE_PROMPT = fs.readFileSync(path.join(REPO, "tools", "world_state_prompt_addition.md"), "utf8");
const WORLD_STATE_FUNCTIONS = fs.readFileSync(path.join(REPO, "tools", "world_state_functions_addition.yaml"), "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes += 1;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails += 1;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function svcs(overrides) {
  const defaults = {
    "vllm": "cool",
    "wyoming-parakeet": "good",
    "kokoro": "good",
    "vision-sidecar": "good",
    "haos": "good",
    "ha bridge": "good",
    "frigate": "good",
    "network": "good",
  };
  return Object.entries({ ...defaults, ...(overrides || {}) }).map(([name, dot]) => ({
    name,
    dot,
    meta: "recovery-test",
    cls: dot === "err" || dot === "warn" ? dot : "default",
  }));
}

function byName(rows, name) {
  return (rows || []).find((row) => row && row.name === name);
}

function summaryFor(runtime) {
  return H.summarizeCapabilityHealth(H.deriveRuntimeServiceHealth(svcs(), runtime));
}

process.stdout.write("\nrecovery_scenario_inventory_test\n");
for (const id of ["S105", "S106", "S107", "S108", "S109", "S110"]) {
  assert(`${id} remains documented`, SCENARIOS.includes(`### ${id}`), id);
}
assert("evidence map keeps S105-S110 honest as local partial", /"DOC-S105"[\s\S]*"local_partial_green"[\s\S]*"DOC-S110"[\s\S]*"local_partial_green"/.test(EVIDENCE));

process.stdout.write("\nDOC-S105_ha_offline_recovery_contract_test\n");
const haRows = H.deriveRuntimeServiceHealth(svcs(), {
  connection: "offline",
  bridgeHealth: { ha_connected: false },
});
assert("HA offline maps HAOS row to err", byName(haRows, "haos").dot === "err", haRows);
assert("HA offline path emits offline connection state", /state === "offline"[\s\S]*setConnection/.test(APP), "HomeApp should preserve a visible offline state");
assert("HomeHeader receives connection and renders offline state", /<HomeHeader[\s\S]*connection=\{connection\}/.test(APP) && /connection === "offline"/.test(APP));
assert("Living Lights drawer receives connection and disables writes offline", /<window\.HomeLightsDrawer[\s\S]*connection=\{connection\}/.test(APP) && LIGHTS.includes("Home Assistant is reconnecting; light controls are disabled"));
assert("HA service helpers reject disconnected websocket calls", APP.includes('throw new Error("HA not connected")') || APP.includes('text: "not connected"'));

process.stdout.write("\nDOC-S106_vision_sidecar_unreachable_contract_test\n");
const visionSummary = summaryFor({ visionSidecarOnline: false });
assert("vision sidecar down surfaces vision offline chip", visionSummary && visionSummary.chip_text === "vision offline", visionSummary);
assert("vision tooltip lists affected tools", visionSummary.tooltip_text.includes("refresh_perception") && visionSummary.tooltip_text.includes("describe_clip"), visionSummary);
assert("vision tooltip preserves fallback", visionSummary.tooltip_text.includes("frigate occupancy") && visionSummary.tooltip_text.includes("HA presence"), visionSummary);
assert("HomeApp vision health poll clears stale health on non-ok and fetch failure", /setVisionSidecarOnline\(r\.ok\)[\s\S]*setVisionHealth\(null\)[\s\S]*setVisionSidecarOnline\(false\)[\s\S]*setVisionHealth\(null\)/.test(APP));
assert("world_state refresh_perception returns explicit vision-sidecar errors", WORLD_STATE.includes("vision-sidecar timeout") && WORLD_STATE.includes("vision-sidecar unreachable"));
assert("vision card retries dead streams with visible reconnecting overlay", VISION.includes("scheduleReload") && VISION.includes("reconnecting"));

process.stdout.write("\nvision_direct_camera_question_contract_test\n");
assert("default prompt forces fresh perception for direct camera-view questions",
  CONST.includes("Direct visual/camera questions:") &&
  CONST.includes('"what do you see"') &&
  CONST.includes("what's in the office right") &&
  CONST.includes("MUST call") &&
  CONST.includes("`refresh_perception(room)` before answering") &&
  CONST.includes("NEVER answer a direct camera-view question from motion sensors"));
assert("get_room_state tool description rejects cached-only camera answers",
  CONST.includes("Do NOT use as ") &&
  CONST.includes("the only tool for direct camera-view questions") &&
  CONST.includes("what's in the office right") &&
  CONST.includes("Those require refresh_perception first"));
assert("refresh_perception tool description names direct driveway-camera questions",
  CONST.includes("Use FIRST for direct visual/camera questions") &&
  CONST.includes("what do you see in the driveway camera"));
assert("helper prompt/function snippets preserve fresh-vision contract",
  WORLD_STATE_PROMPT.includes("Direct visual/camera questions:") &&
  WORLD_STATE_PROMPT.includes("what's in the office right now") &&
  WORLD_STATE_PROMPT.includes("Those are not seeing") &&
  WORLD_STATE_FUNCTIONS.includes("Do NOT use as the only tool for direct camera-view questions") &&
  WORLD_STATE_FUNCTIONS.includes("Use FIRST for direct visual/camera questions"));
assert("Home app treats refresh_perception as a vision tool in the chat feed",
  APP.includes('tc.name === "refresh_perception"') &&
  APP.includes('tc.args.camera || tc.args.room') &&
  APP.includes('tc.name === "describe_camera" || tc.name === "refresh_perception"'));

process.stdout.write("\nDOC-S107_frigate_down_contract_test\n");
const frigateRows = H.deriveRuntimeServiceHealth(svcs(), {
  frigateMetrics: null,
  cameraLabels: {},
  cameraStates: { living_room: "stale", kitchen: "stale", dining_room: "stale" },
});
const frigateSummary = H.summarizeCapabilityHealth(frigateRows);
assert("stale camera map maps Frigate to offline", byName(frigateRows, "frigate").dot === "err", frigateRows);
assert("Frigate outage maps to perception offline", frigateSummary && frigateSummary.chip_text === "perception offline", frigateSummary);
assert("Frigate tooltip lists affected visual/occupancy tools", ["find_person", "who_is_in", "get_room_state", "recap"].every((name) => frigateSummary.tooltip_text.includes(name)), frigateSummary);
assert("Frigate fallback says HA presence may still work", H.CAPABILITY_REGISTRY.find((c) => c.id === "perception").fallback_hint.includes("HA presence"));
assert("Frigate metrics hook clears stale telemetry on state load failure and unavailable sensors", /setFrigateMetrics\(null\)/.test(APP) && /newState === "unknown" \|\| newState === "unavailable"/.test(APP));

process.stdout.write("\nDOC-S108_sonos_unavailable_contract_test\n");
assert("control card reports unavailable media entities", CONTROL.includes('["unavailable", "unknown"].includes(s.state)') && CONTROL.includes('missing: true'));
assert("media action cards downgrade all-unavailable targets", CONTROL.includes('allUnavailable ? "unavailable" : lifecycle'));
assert("media service errors surface as call failed instead of silent success", CONTROL.includes('setStatus("error")') && CONTROL.includes('"call failed"'));
assert("native media area resolution remains covered by media_player domain", /media_player/.test(CONTROL) && SCENARIOS.includes("NO silent no-op"));

process.stdout.write("\nDOC-S109_metrics_sidecar_down_contract_test\n");
const metricsRows = H.deriveRuntimeServiceHealth(svcs(), { sidecarOnline: false });
assert("metrics sidecar down maps row offline", byName(metricsRows, "metrics-sidecar").dot === "err", metricsRows);
assert("metrics-sidecar outage does not fake a user capability alarm", H.summarizeCapabilityHealth(metricsRows) === null);
assert("Home header has chat-tee offline warning copy", APP.includes("chat-tee offline"));
assert("metrics poll clears stale model/resource data on failure", /setSidecarOnline\(false\)[\s\S]*setMetrics\(CLEARED_METRICS\)[\s\S]*setMetricsHistory\(emptyMetricsHistory\(\)\)/.test(APP));
assert("metrics poll clears latest metrics singleton on failure", APP.includes("__hav_metricsLatest = null"));

process.stdout.write("\nDOC-S110_bridge_offline_contract_test\n");
const bridgeRows = H.deriveRuntimeServiceHealth(svcs(), {
  connection: "online",
  bridgeOnline: false,
  bridgeHealth: null,
});
const bridgeSummary = H.summarizeCapabilityHealth(bridgeRows);
assert("bridge down maps bridge service offline", byName(bridgeRows, "ha bridge").dot === "err", bridgeRows);
assert("bridge down maps voice capability offline", bridgeSummary && bridgeSummary.chip_text === "voice offline", bridgeSummary);
assert("bridge tooltip preserves typed input fallback", bridgeSummary.tooltip_text.includes("typed input"), bridgeSummary);
assert("HomeApp bridge health poll clears stale body on failure", /setBridgeOnline\(false\)[\s\S]*setBridgeHealth\(null\)/.test(APP));
assert("S2S bridge errors surface to chat feed", APP.includes("s2s · ${msg}") && S2S.includes('onError?.(msg.message || "s2s error")'));
assert("SSE helper calls onError and onClose for stream failures",
  /onError\(new Error\(`http \$\{r\.status\}`\)\)/.test(SSE) &&
  /catch \(e\)[\s\S]*onError\(e\)/.test(SSE) &&
  /finally[\s\S]*onClose\(\)/.test(SSE));

process.stdout.write("\nrecovery_sidecar_tool_failure_contract_test\n");
assert("describe_clip timeout/unreachable/http errors have user-facing phrasing",
  FRIGATE_TOOL.includes("The cameras didn't respond in time.") &&
  FRIGATE_TOOL.includes("I can't reach the vision service right now.") &&
  FRIGATE_TOOL.includes("The vision service had a problem."));
assert("refresh_perception has a cached-state fallback message on budget exhaustion",
  WORLD_STATE.includes("using cached state from get_room_state instead"));

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
}
console.log(`\n${passes} pass . ${fails} fail`);
process.exit(fails ? 1 : 0);
