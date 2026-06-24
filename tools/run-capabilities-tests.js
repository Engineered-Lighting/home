#!/usr/bin/env node
/* ============================================================================
 * tools/run-capabilities-tests.js
 *
 * Pure-function tests for home-capabilities.js (Addendum 27 A-1 + F-18).
 *
 *   node tools/run-capabilities-tests.js
 *
 * Exit 0 if all pass, 1 if any fail.
 * ============================================================================ */

"use strict";

const fs = require("fs");
const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src", "home-capabilities.js"));
const APP_SRC = path.join(__dirname, "..", "app", "src", "home-app.jsx");
const appSource = fs.readFileSync(APP_SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  \x1b[32mPASS\x1b[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  \x1b[31mFAIL\x1b[0m  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ");
      process.stdout.write("\n        " + dumped);
    }
    process.stdout.write("\n");
  }
}

/* Helper: build a services array shaped like buildStackList() emits.
 * Override `overrides` to flip individual services to err/warn. */
function svcs(overrides) {
  overrides = overrides || {};
  const defaults = {
    "vllm":             "cool",
    "wyoming-parakeet": "good",
    "kokoro":           "good",
    "vision-sidecar":   "good",
    "haos":             "good",
    "ha bridge":        "good",
    "frigate":          "good",
    "network":          "good",
  };
  const merged = Object.assign({}, defaults, overrides);
  return Object.entries(merged).map(([name, dot]) => ({
    name, dot, meta: "test", cls: "default",
  }));
}

function byName(rows, name) {
  return (rows || []).find((s) => s && s.name === name);
}

function sourceSlice(startText, endText) {
  const start = appSource.indexOf(startText);
  if (start < 0) return "";
  const end = endText ? appSource.indexOf(endText, start) : -1;
  return end >= 0 ? appSource.slice(start, end) : appSource.slice(start);
}

/* Suite 1 — CAPABILITY_REGISTRY shape */
process.stdout.write("\nregistry_shape_test\n");

assert("registry is non-empty", H.CAPABILITY_REGISTRY.length >= 4);
assert("every entry has id + label + requires + impact strings",
  H.CAPABILITY_REGISTRY.every((c) =>
    c.id && c.label && Array.isArray(c.requires) &&
    typeof c.impact_offline === "string" &&
    typeof c.impact_degraded === "string"
  ));
assert("ids are unique",
  new Set(H.CAPABILITY_REGISTRY.map((c) => c.id)).size === H.CAPABILITY_REGISTRY.length);

/* Suite 2 — deriveCapabilityHealth happy path */
process.stdout.write("\ncapability_health_happy_path_test\n");

const healthy = H.deriveCapabilityHealth(svcs());
assert("healthy: every capability ok",
  healthy.every((c) => c.status === "ok"));
assert("healthy: no impact string on ok capabilities",
  healthy.every((c) => c.impact === null));
assert("healthy: missing arrays are empty",
  healthy.every((c) => Array.isArray(c.missing) && c.missing.length === 0));

process.stdout.write("\nruntime_service_health_overlay_test\n");

const runtimeHealthy = H.deriveRuntimeServiceHealth(svcs(), {
  connection: "online",
  sidecarOnline: true,
  bridgeOnline: true,
  bridgeHealth: { ha_connected: true, tts_engine: "chatterbox" },
  visionHealth: { ok: true },
  frigateMetrics: { fps: { kitchen: 5 } },
  cameraLabels: {},
});
assert("runtime overlay keeps HAOS connected when HA + bridge agree",
  byName(runtimeHealthy, "haos").dot === "good", byName(runtimeHealthy, "haos"));
assert("runtime overlay keeps metrics-sidecar online when healthz is ok",
  byName(runtimeHealthy, "metrics-sidecar").dot === "good", byName(runtimeHealthy, "metrics-sidecar"));
assert("runtime overlay keeps Frigate online when telemetry exists",
  byName(runtimeHealthy, "frigate").dot === "good", byName(runtimeHealthy, "frigate"));

const runtimeHaDown = H.deriveRuntimeServiceHealth(svcs(), {
  connection: "offline",
  bridgeHealth: { ha_connected: false, tts_engine: "chatterbox" },
});
assert("runtime overlay marks HAOS offline from app connection",
  byName(runtimeHaDown, "haos").dot === "err", byName(runtimeHaDown, "haos"));

const runtimeBridgeDown = H.deriveRuntimeServiceHealth(svcs(), {
  connection: "online",
  bridgeOnline: false,
  bridgeHealth: null,
});
const bridgeVoice = H.summarizeCapabilityHealth(runtimeBridgeDown);
assert("runtime overlay marks bridge service offline",
  byName(runtimeBridgeDown, "ha bridge").dot === "err", byName(runtimeBridgeDown, "ha bridge"));
assert("runtime overlay makes voice capability offline when bridge is down",
  bridgeVoice && bridgeVoice.chip_text === "voice offline", bridgeVoice);
assert("bridge-down voice tooltip preserves typed-input fallback",
  bridgeVoice.tooltip_text.includes("typed input"), bridgeVoice);

const runtimeTtsDown = H.deriveRuntimeServiceHealth(svcs(), {
  connection: "online",
  bridgeOnline: true,
  bridgeHealth: { ha_connected: true, tts_engine: null },
});
assert("runtime overlay marks TTS rows offline when bridge reports no engine",
  byName(runtimeTtsDown, "kokoro").dot === "err" &&
  byName(runtimeTtsDown, "chatterbox-tts").dot === "err", runtimeTtsDown);
assert("runtime TTS outage maps to voice capability impact",
  H.summarizeCapabilityHealth(runtimeTtsDown).chip_text === "voice offline");

const runtimeFrigateUnknown = H.deriveRuntimeServiceHealth(svcs(), {
  frigateMetrics: null,
  cameraLabels: {},
});
assert("runtime overlay treats no Frigate evidence as unknown, not offline",
  byName(runtimeFrigateUnknown, "frigate").dot === "idle", byName(runtimeFrigateUnknown, "frigate"));
assert("unknown Frigate does not raise a capability alarm",
  H.summarizeCapabilityHealth(runtimeFrigateUnknown) === null);

const runtimeFrigateDown = H.deriveRuntimeServiceHealth(svcs(), {
  frigateMetrics: null,
  cameraLabels: {},
  cameraStates: {
    living_room: "stale", kitchen: "stale", dining_room: "stale",
  },
});
const frigateSummary = H.summarizeCapabilityHealth(runtimeFrigateDown);
assert("runtime overlay marks Frigate offline from stale camera map",
  byName(runtimeFrigateDown, "frigate").dot === "err", byName(runtimeFrigateDown, "frigate"));
assert("Frigate outage maps to perception capability offline",
  frigateSummary && frigateSummary.chip_text === "perception offline", frigateSummary);
assert("Frigate outage tooltip names occupancy impact",
  frigateSummary.tooltip_text.includes("occupancy detection"), frigateSummary);

const runtimeVisionDown = H.deriveRuntimeServiceHealth(svcs(), {
  visionSidecarOnline: false,
});
const visionSummary2 = H.summarizeCapabilityHealth(runtimeVisionDown);
assert("runtime overlay marks vision-sidecar offline from explicit signal",
  byName(runtimeVisionDown, "vision-sidecar").dot === "err", byName(runtimeVisionDown, "vision-sidecar"));
assert("vision outage maps to vision capability offline",
  visionSummary2 && visionSummary2.chip_text === "vision offline", visionSummary2);

const runtimeVisionBadHealth = H.deriveRuntimeServiceHealth(svcs(), {
  visionHealth: { ok: false },
});
assert("runtime overlay marks vision-sidecar offline from ok=false health body",
  byName(runtimeVisionBadHealth, "vision-sidecar").dot === "err", byName(runtimeVisionBadHealth, "vision-sidecar"));

const runtimeVisionUnknownHealth = H.deriveRuntimeServiceHealth(svcs(), {
  visionHealth: null,
  visionSidecarOnline: null,
});
assert("runtime overlay leaves vision-sidecar unchanged when no runtime signal exists",
  byName(runtimeVisionUnknownHealth, "vision-sidecar").dot === "good", byName(runtimeVisionUnknownHealth, "vision-sidecar"));

const runtimeMetricsDown = H.deriveRuntimeServiceHealth(svcs(), {
  sidecarOnline: false,
});
assert("runtime overlay marks metrics-sidecar offline from healthz failure",
  byName(runtimeMetricsDown, "metrics-sidecar").dot === "err", byName(runtimeMetricsDown, "metrics-sidecar"));
assert("metrics-sidecar outage does not invent a user-facing capability",
  H.summarizeCapabilityHealth(runtimeMetricsDown) === null);

/* Suite 3 — single capability degradation */
process.stdout.write("\nsingle_capability_test\n");

const visionDown = H.deriveCapabilityHealth(svcs({ "vision-sidecar": "err" }));
const vCap = visionDown.find((c) => c.id === "vision");
assert("vision capability marked offline", vCap.status === "offline");
assert("vision impact populated", typeof vCap.impact === "string" && vCap.impact.length > 0);
assert("vision missing includes vision-sidecar",
  vCap.missing.includes("vision-sidecar"));
assert("other capabilities still ok when vision down",
  visionDown.filter((c) => c.id !== "vision").every((c) => c.status === "ok"));

/* Suite 4 — multi-requirement capability (voice needs BOTH stt + tts) */
process.stdout.write("\nmulti_requirement_test\n");

const ttsDown = H.deriveCapabilityHealth(svcs({ "kokoro": "err" }));
const voiceCap = ttsDown.find((c) => c.id === "voice");
assert("voice capability offline when EITHER stt OR tts is err",
  voiceCap.status === "offline");
assert("voice missing lists exactly the broken service",
  voiceCap.missing.length === 1 && voiceCap.missing[0] === "kokoro");

const sttWarn = H.deriveCapabilityHealth(svcs({ "wyoming-parakeet": "warn" }));
const voiceCap2 = sttWarn.find((c) => c.id === "voice");
assert("voice capability degraded when stt is warn (less severe)",
  voiceCap2.status === "degraded");

/* Suite 5 — worst-severity wins for a capability */
process.stdout.write("\nworst_severity_test\n");

const bothBad = H.deriveCapabilityHealth(svcs({
  "wyoming-parakeet": "warn",
  "kokoro": "err",
}));
const voiceCapBoth = bothBad.find((c) => c.id === "voice");
assert("voice = offline when one required is err + other is warn (worst wins)",
  voiceCapBoth.status === "offline");
assert("voice missing lists BOTH non-ok services",
  voiceCapBoth.missing.length === 2);

/* Suite 6 — unknown / idle dot is treated as ok */
process.stdout.write("\nunknown_idle_test\n");

const idle = H.deriveCapabilityHealth(svcs({ "vllm": "idle" }));
const chatCap = idle.find((c) => c.id === "chat");
assert("idle dot does NOT flag the capability (don't alarm on unknown)",
  chatCap.status === "ok");
assert("idle dot does NOT show up in missing[]",
  chatCap.missing.length === 0);

/* Suite 7 — service entirely absent from list → treated as offline */
process.stdout.write("\nabsent_service_test\n");

const partial = [
  { name: "vllm", dot: "cool" },
  // vision-sidecar absent entirely (registry misconfigured / API drift)
];
const absent = H.deriveCapabilityHealth(partial);
const visionAbsent = absent.find((c) => c.id === "vision");
assert("absent required service → capability marked offline",
  visionAbsent.status === "offline");
assert("absent service appears in missing[]",
  visionAbsent.missing.includes("vision-sidecar"));

/* Suite 8 — defensive against null/undefined input */
process.stdout.write("\nnull_input_test\n");

const nullOut = H.deriveCapabilityHealth(null);
assert("null services → every capability offline (treats all required as absent)",
  nullOut.every((c) => c.status === "offline"));
assert("null services → CAPABILITY_REGISTRY length preserved",
  nullOut.length === H.CAPABILITY_REGISTRY.length);

const undefOut = H.deriveCapabilityHealth(undefined);
assert("undefined services handled same as null",
  undefOut.every((c) => c.status === "offline"));

/* Suite 9 — summarizeCapabilityHealth */
process.stdout.write("\nsummarize_test\n");

const summaryOk = H.summarizeCapabilityHealth(svcs());
assert("summary: ok everywhere → null", summaryOk === null);

const summary1Off = H.summarizeCapabilityHealth(svcs({ "vision-sidecar": "err" }));
assert("summary: 1 offline → worst_status='offline'",
  summary1Off.worst_status === "offline");
assert("summary: 1 offline chip text = '<label> offline'",
  summary1Off.chip_text === "vision offline", summary1Off.chip_text);
assert("summary: tooltip mentions the impact",
  summary1Off.tooltip_text.includes("vision:") && summary1Off.tooltip_text.includes("unavailable"));
assert("summary: counts correct (1 offline, 0 degraded)",
  summary1Off.offline_count === 1 && summary1Off.degraded_count === 0);

const summary2Off = H.summarizeCapabilityHealth(svcs({
  "vision-sidecar": "err",
  "kokoro": "err",
}));
assert("summary: 2 offline chip = '2 offline'",
  summary2Off.chip_text === "2 offline", summary2Off.chip_text);
assert("summary: 2 offline tooltip has both",
  summary2Off.tooltip_text.includes("vision:") &&
  summary2Off.tooltip_text.includes("voice:"));

const summary1Deg = H.summarizeCapabilityHealth(svcs({ "frigate": "warn" }));
assert("summary: 1 degraded only → worst_status='degraded'",
  summary1Deg.worst_status === "degraded");
assert("summary: 1 degraded chip = '<label> degraded'",
  summary1Deg.chip_text === "perception degraded", summary1Deg.chip_text);

const summaryMixed = H.summarizeCapabilityHealth(svcs({
  "vision-sidecar": "err",
  "frigate": "warn",
}));
assert("summary: mixed → worst_status='offline' (offline beats degraded)",
  summaryMixed.worst_status === "offline");
assert("summary: mixed tooltip lists both (offline first)",
  summaryMixed.tooltip_text.startsWith("vision:"));

/* Suite 10 — non-listed services don't affect capabilities */
process.stdout.write("\nirrelevant_services_test\n");

const networkDown = H.summarizeCapabilityHealth(svcs({ "network": "err" }));
assert("network err alone → no capability flagged (network isn't user-facing cap)",
  networkDown === null);

const haosDown = H.summarizeCapabilityHealth(svcs({ "haos": "err" }));
assert("haos err alone → no capability flagged",
  haosDown === null);

/* Suite 11 — affected_tools enrichment */
process.stdout.write("\naffected_tools_test\n");

// Registry entries carry the new field
assert("registry: every entry has affected_tools array",
  H.CAPABILITY_REGISTRY.every((c) => Array.isArray(c.affected_tools)));

// Vision capability lists describe_clip + refresh_perception
const visionEntry = H.CAPABILITY_REGISTRY.find((c) => c.id === "vision");
assert("vision affected_tools includes describe_clip",
  visionEntry.affected_tools.includes("describe_clip"));
assert("vision affected_tools includes refresh_perception",
  visionEntry.affected_tools.includes("refresh_perception"));

// Perception capability lists find_clips + find_person + recap
const perceptionEntry = H.CAPABILITY_REGISTRY.find((c) => c.id === "perception");
assert("perception affected_tools includes find_clips",
  perceptionEntry.affected_tools.includes("find_clips"));
assert("perception affected_tools includes find_person",
  perceptionEntry.affected_tools.includes("find_person"));
assert("perception affected_tools includes recap",
  perceptionEntry.affected_tools.includes("recap"));
assert("perception affected_tools includes get_room_state",
  perceptionEntry.affected_tools.includes("get_room_state"));

// Chat + voice intentionally have empty affected_tools (they're not
// tool-gating — chat down breaks every tool; voice is I/O only)
const chatEntry = H.CAPABILITY_REGISTRY.find((c) => c.id === "chat");
assert("chat affected_tools is empty (chat-down implies every-tool-down)",
  chatEntry.affected_tools.length === 0);
const voiceEntry = H.CAPABILITY_REGISTRY.find((c) => c.id === "voice");
assert("voice affected_tools is empty (voice is I/O, not gating)",
  voiceEntry.affected_tools.length === 0);

// deriveCapabilityHealth propagates affected_tools to the returned object
const visionDownDerive = H.deriveCapabilityHealth(svcs({ "vision-sidecar": "err" }));
const vDerived = visionDownDerive.find((c) => c.id === "vision");
assert("derive: affected_tools surface on capability output",
  Array.isArray(vDerived.affected_tools) && vDerived.affected_tools.length >= 2);

// Summary tooltip names the specific tools
const summaryToolsList = H.summarizeCapabilityHealth(svcs({ "vision-sidecar": "err" }));
assert("summary tooltip includes 'tools:' line for vision-down case",
  summaryToolsList.tooltip_text.includes("tools:"));
assert("summary tooltip names describe_clip explicitly",
  summaryToolsList.tooltip_text.includes("describe_clip"));
assert("summary tooltip names refresh_perception explicitly",
  summaryToolsList.tooltip_text.includes("refresh_perception"));

// Capabilities with empty affected_tools should NOT add a noise line
const chatDownSummary = H.summarizeCapabilityHealth(svcs({ "vllm": "err" }));
assert("summary tooltip omits 'tools:' line when affected_tools is empty",
  !chatDownSummary.tooltip_text.includes("tools:"));

// Mixed: vision (with tools) + voice (without) — only vision's tools listed
const mixedSummary = H.summarizeCapabilityHealth(svcs({
  "vision-sidecar": "err",
  "kokoro": "err",
}));
const toolLineCount = (mixedSummary.tooltip_text.match(/tools:/g) || []).length;
assert("summary tooltip has tools: line ONLY for caps with affected_tools",
  toolLineCount === 1, `expected 1 tools line, got ${toolLineCount}`);

// Frigate down (perception cap) → 6 tools listed
const frigateDownSummary = H.summarizeCapabilityHealth(svcs({ "frigate": "err" }));
assert("frigate-down tooltip lists find_clips + find_person + recap",
  frigateDownSummary.tooltip_text.includes("find_clips") &&
  frigateDownSummary.tooltip_text.includes("find_person") &&
  frigateDownSummary.tooltip_text.includes("recap"));

/* Suite 12 — fallback_hint propagation + tooltip rendering */
process.stdout.write("\nfallback_hint_test\n");

// Every entry has fallback_hint (string OR empty)
assert("registry: every entry has fallback_hint",
  H.CAPABILITY_REGISTRY.every((c) => typeof c.fallback_hint === "string"));

// Voice cap declares typed-fallback hint
const voiceEntry2 = H.CAPABILITY_REGISTRY.find((c) => c.id === "voice");
assert("voice fallback_hint mentions typed input",
  /typed/i.test(voiceEntry2.fallback_hint));

// Chat is honest about having no fallback
const chatEntry2 = H.CAPABILITY_REGISTRY.find((c) => c.id === "chat");
assert("chat fallback_hint explicitly says 'no fallback'",
  /no fallback/i.test(chatEntry2.fallback_hint));

// Vision cap mentions HA presence + frigate as fallbacks
const visionEntry2 = H.CAPABILITY_REGISTRY.find((c) => c.id === "vision");
assert("vision fallback_hint mentions HA presence",
  /ha presence/i.test(visionEntry2.fallback_hint));

// Perception cap mentions device tracker
const perceptionEntry2 = H.CAPABILITY_REGISTRY.find((c) => c.id === "perception");
assert("perception fallback_hint mentions device tracker / person",
  /device tracker|person/i.test(perceptionEntry2.fallback_hint));

// deriveCapabilityHealth propagates fallback_hint
const visionDownFallback = H.deriveCapabilityHealth(svcs({ "vision-sidecar": "err" }));
const vDerived2 = visionDownFallback.find((c) => c.id === "vision");
assert("derive: fallback_hint propagates to output",
  vDerived2.fallback_hint && vDerived2.fallback_hint.length > 0);

// Tooltip renders the fallback line with → prefix
const visionTooltip = H.summarizeCapabilityHealth(svcs({ "vision-sidecar": "err" }));
assert("tooltip includes → fallback line for vision down",
  visionTooltip.tooltip_text.includes("→") &&
  visionTooltip.tooltip_text.includes("frigate occupancy"));

// Voice degraded tooltip mentions typed fallback
const voiceTooltip = H.summarizeCapabilityHealth(svcs({ "wyoming-parakeet": "warn" }));
assert("voice tooltip includes 'typed input still works' as fallback",
  voiceTooltip.tooltip_text.includes("typed input"));

// 3-line check: when a cap has BOTH affected_tools AND fallback_hint,
// the tooltip should have 3 lines for it (header + tools + fallback)
const visionLineCount = visionTooltip.tooltip_text.split("\n").length;
assert("vision down → 3 tooltip lines (header + tools + fallback)",
  visionLineCount === 3, `expected 3 lines, got ${visionLineCount}`);

// Mixed: vision + frigate down → 6 lines (3 per cap)
const mixedTooltip = H.summarizeCapabilityHealth(svcs({
  "vision-sidecar": "err",
  "frigate": "err",
}));
const mixedLineCount = mixedTooltip.tooltip_text.split("\n").length;
assert("vision+frigate down → 6 tooltip lines (3 per cap)",
  mixedLineCount === 6, `expected 6 lines, got ${mixedLineCount}`);

// Chat-down: 2 lines (header + fallback; no tools line since affected_tools empty)
const chatTooltip = H.summarizeCapabilityHealth(svcs({ "vllm": "err" }));
const chatLineCount = chatTooltip.tooltip_text.split("\n").length;
assert("chat down → 2 tooltip lines (header + fallback; no tools)",
  chatLineCount === 2, `expected 2 lines (header + fallback), got ${chatLineCount}`);
assert("chat down tooltip mentions 'no fallback'",
  /no fallback/i.test(chatTooltip.tooltip_text));

/* Suite 13 - F-18 HomeApp capability chip source contract */
process.stdout.write("\ncapability_chip_source_contract_test\n");

assert("HomeApp skips capability chip in Simulation Mode",
  /if\s*\(sim\?\.active\)\s*return null/.test(appSource));
assert("HomeApp derives chip from buildStackList + summarizeCapabilityHealth",
  /window\.buildStackList/.test(appSource) &&
  /window\.summarizeCapabilityHealth/.test(appSource) &&
  /summary\s*=\s*window\.summarizeCapabilityHealth\(services\)/.test(appSource));
assert("HomeApp overlays runtime health before capability summary",
  /window\.deriveRuntimeServiceHealth/.test(appSource) &&
  /services\s*=\s*window\.deriveRuntimeServiceHealth\(services,\s*\{/.test(appSource));
assert("HomeHeader receives outage signals used by runtime overlay",
  /bridgeHealth=\{bridgeHealth\}/.test(appSource) &&
  /visionSidecarOnline=\{visionSidecarOnline\}/.test(appSource) &&
  /visionHealth=\{visionHealth\}/.test(appSource) &&
  /frigateMetrics=\{frigateMetrics\}/.test(appSource) &&
  /cameraLabels=\{cameraLabels\}/.test(appSource));
const bridgePollBlock = sourceSlice('const r2 = await tauriFetch(`${bridgeUrl}/healthz`', "// Stack supervisor");
assert("HomeApp bridge health poll clears stale health on bad JSON, non-ok, and fetch failure",
  /catch\s*\{\s*setBridgeHealth\(null\);\s*\}/.test(bridgePollBlock) &&
  /else\s*\{\s*setBridgeHealth\(null\);\s*\}/.test(bridgePollBlock) &&
  /setBridgeOnline\(false\);[\s\S]*setBridgeHealth\(null\);/.test(bridgePollBlock),
  bridgePollBlock.slice(0, 900));
const visionPollBlock = sourceSlice('const r = await tauriFetch(`${visionUrl}/healthz`', "tick();");
assert("HomeApp vision health poll sets explicit online signal and clears stale health on bad JSON, non-ok, and fetch failure",
  /setVisionSidecarOnline\(r\.ok\);/.test(visionPollBlock) &&
  /catch\s*\{\s*setVisionHealth\(null\);\s*\}/.test(visionPollBlock) &&
  /else\s*\{\s*setVisionHealth\(null\);\s*\}/.test(visionPollBlock) &&
  /setVisionSidecarOnline\(false\);[\s\S]*setVisionHealth\(null\);/.test(visionPollBlock),
  visionPollBlock.slice(0, 900));
const frigateMetricsBlock = sourceSlice("Tray v4: Frigate stats", "Tray v3: bridge /rooms");
assert("HomeApp Frigate metrics hook clears stale telemetry on state-load failure and unavailable sensor updates",
  /catch\s*\(e\)\s*\{\s*if\s*\(!cancelled\)\s*setFrigateMetrics\(null\);/.test(frigateMetricsBlock) &&
  /newState\s*===\s*"unknown"\s*\|\|\s*newState\s*===\s*"unavailable"[\s\S]*rebuild\(\);[\s\S]*return;/.test(frigateMetricsBlock),
  frigateMetricsBlock.slice(0, 900));
const roomContextBlock = sourceSlice("Tray v3: bridge /rooms", "Phase 2: vision-sidecar health");
assert("HomeApp bridge /rooms poll clears stale room context on non-ok, bad JSON, and fetch failure",
  /if\s*\(!r\.ok\)\s*\{\s*setRoomContext\(null\);\s*return;/.test(roomContextBlock) &&
  /try\s*\{\s*setRoomContext\(await r\.json\(\)\);\s*\}\s*catch\s*\{\s*setRoomContext\(null\);\s*\}/.test(roomContextBlock) &&
  /catch\s*\(e\)\s*\{\s*if\s*\(!cancelled\)\s*setRoomContext\(null\);/.test(roomContextBlock),
  roomContextBlock.slice(0, 900));
const roomContextRenderBlock = sourceSlice("const roomContextRows = (() =>", "/* ── Tab renderers");
const bridgeCardBlock = sourceSlice('HmCard title="HA BRIDGE / API"', "Lab tab");
assert("HomeApp renders bridge /rooms context with occupied-first rows",
  /roomContext\.rooms/.test(roomContextRenderBlock) &&
  /personNames\.join\(", "\)/.test(roomContextRenderBlock) &&
  /ao\s*!==\s*bo[\s\S]*return ao \? -1 : 1/.test(roomContextRenderBlock) &&
  /window\.HmRoomRow/.test(bridgeCardBlock) &&
  /roomContextRows\.slice\(0,\s*8\)\.map/.test(bridgeCardBlock),
  roomContextRenderBlock + "\n---\n" + bridgeCardBlock.slice(0, 900));
const metricsPollBlock = sourceSlice("Metrics polling", "HA conversation");
assert("HomeApp metrics poll clears stale model/resource snapshot when /metrics is unavailable",
  /if\s*\(!r\.ok\)\s*\{[\s\S]*setSidecarOnline\(false\);[\s\S]*setMetrics\(CLEARED_METRICS\);[\s\S]*setMetricsHistory\(emptyMetricsHistory\(\)\);[\s\S]*__hav_metricsLatest\s*=\s*null;[\s\S]*return;/.test(metricsPollBlock) &&
  /catch\s*\(e\)\s*\{[\s\S]*setSidecarOnline\(false\);[\s\S]*setMetrics\(CLEARED_METRICS\);[\s\S]*setMetricsHistory\(emptyMetricsHistory\(\)\);[\s\S]*__hav_metricsLatest\s*=\s*null;/.test(metricsPollBlock),
  metricsPollBlock.slice(0, 1400));
assert("HomeApp metrics poll treats explicit null model telemetry as model offline",
  /const hasMetric = \(key\) => Object\.prototype\.hasOwnProperty\.call\(m, key\);/.test(metricsPollBlock) &&
  /model:\s*hasMetric\("model"\)\s*\?\s*\(m\.model\s*\|\|\s*null\)\s*:\s*prev\.model/.test(metricsPollBlock) &&
  /setSidecarOnline\(true\);/.test(metricsPollBlock),
  metricsPollBlock.slice(0, 1400));
assert("HomeApp dependency strip ignores placeholder/default model names",
  /const CLEARED_METRICS = \{ \.\.\.DEFAULT_METRICS, model: null \};/.test(appSource) &&
  /const \[metrics, setMetrics\] = useState\(CLEARED_METRICS\);/.test(appSource) &&
  /function hasLiveModelName\(modelName\)/.test(appSource) &&
  /if\s*\(hasLiveModelName\(metrics\.model\)\)/.test(appSource),
  appSource.slice(appSource.indexOf("const DEFAULT_METRICS"), appSource.indexOf("function metricsBaseFromEndpoint")));
const haTelemetryClearBlock = sourceSlice("const sim = window.useSimulation", "const feedRef = useRef");
assert("HomeApp clears HA-derived telemetry when the live HA connection drops",
  /if\s*\(sim\.active\s*\|\|\s*connection\s*===\s*"online"\)\s*return;/.test(haTelemetryClearBlock) &&
  /setCameraLabels\(\{\}\);/.test(haTelemetryClearBlock) &&
  /setNetworkMetrics\(emptyNetworkMetrics\(\)\);/.test(haTelemetryClearBlock) &&
  /setHostMetrics\(null\);/.test(haTelemetryClearBlock) &&
  /setFrigateMetrics\(null\);/.test(haTelemetryClearBlock) &&
  /setRoomContext\(null\);/.test(haTelemetryClearBlock),
  haTelemetryClearBlock);
const networkMetricsBlock = sourceSlice("Phase 2: network metrics", "Tray v2: HAOS host");
assert("HomeApp network metrics clear stale telemetry on state-load failure and bad numeric sensor states",
  /catch\s*\(e\)\s*\{\s*if\s*\(!cancelled\)\s*setNetworkMetrics\(emptyNetworkMetrics\(\)\);/.test(networkMetricsBlock) &&
  /const numericMetric = eid\.endsWith\("_cpu_utilization"\) \|\| eid\.endsWith\("_memory_utilization"\);/.test(networkMetricsBlock) &&
  /if\s*\(numericMetric && !isFinite\(val\)\)\s*\{\s*rebuildFromStates\(\);\s*return;/.test(networkMetricsBlock) &&
  /switches\.filter\(\(sw\) => sw\.cpu != null \|\| sw\.mem != null \|\| sw\.state\)/.test(networkMetricsBlock),
  networkMetricsBlock.slice(0, 1400));
const hostMetricsBlock = sourceSlice("Tray v2: HAOS host", "Tray v4: Frigate");
assert("HomeApp host metrics clear stale telemetry on state-load failure and non-numeric updates",
  /catch\s*\(e\)\s*\{\s*if\s*\(!cancelled\)\s*setHostMetrics\(null\);/.test(hostMetricsBlock) &&
  /cur\.cpu = isFinite\(val\) \? val : null;/.test(hostMetricsBlock) &&
  /cur\.ram = isFinite\(val\) \? val : null;/.test(hostMetricsBlock) &&
  /cur\.ramGb = isFinite\(val\) \? val \/ 1024 : null;/.test(hostMetricsBlock) &&
  /return any \? \{ \.\.\.cur \} : null;/.test(hostMetricsBlock),
  hostMetricsBlock.slice(0, 1400));
assert("HomeApp hides chip when all capabilities are ok",
  /if\s*\(!summary\)\s*return null/.test(appSource));
assert("HomeApp maps offline summary to critical color",
  /summary\.worst_status\s*===\s*"offline"/.test(appSource) &&
  /var\(--hg-crit\)/.test(appSource));
assert("HomeApp renders capability tooltip text",
  /title=\{summary\.tooltip_text\}/.test(appSource));
assert("HomeApp renders capability chip text",
  /\{summary\.chip_text\}/.test(appSource));

/* ── Footer ───────────────────────────────────────────────────────── */
process.stdout.write("\n");
if (fails === 0) {
  process.stdout.write("\x1b[32m" + passes + " pass · 0 fail\x1b[0m\n");
  process.exit(0);
} else {
  process.stdout.write("\x1b[31m" + passes + " pass · " + fails + " fail\x1b[0m\n");
  process.exit(1);
}
