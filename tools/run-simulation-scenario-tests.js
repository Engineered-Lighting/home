#!/usr/bin/env node
/* Pure local tests for app/src/simulation-data.jsx scenario registry + fixtures. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const DATA_SRC = path.join(REPO, "app", "src", "simulation-data.jsx");
const CAMERA_SRC = path.join(REPO, "app", "src", "simulation-cameras.jsx");
const dataSource = fs.readFileSync(DATA_SRC, "utf8");
const cameraSource = fs.readFileSync(CAMERA_SRC, "utf8");

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
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function deterministicMath() {
  const out = Object.create(Math);
  let n = 0;
  out.random = () => {
    n = (n + 1) % 997;
    return (n % 100) / 100;
  };
  return out;
}

function loadModule() {
  const window = {
    buildActionContext: ({ id, service, entityTargets = [], areaTargets = [], attrs = {}, status = "success" }) => ({
      id,
      service,
      controllable: true,
      targetEntities: entityTargets,
      targetAreas: areaTargets,
      setValues: attrs,
      startedAt: 1_000_000,
      completedAt: 1_000_100,
      status,
    }),
    SimControlStore: { reset() {}, callService() { return Promise.resolve(); } },
  };
  const sandbox = {
    window,
    Date,
    Math: deterministicMath(),
    JSON,
    Object,
    Array,
    Set,
    encodeURIComponent,
  };
  vm.runInNewContext(cameraSource, sandbox, { filename: CAMERA_SRC });
  vm.runInNewContext(dataSource, sandbox, { filename: DATA_SRC });
  return sandbox.window;
}

function hasAnyCameraLabel(labels) {
  return Object.values(labels || {}).some((arr) => Array.isArray(arr) && arr.length > 0);
}

(function main() {
  process.stdout.write("\nsimulation_scenario_registry_test\n");
  const api = loadModule();
  const S = api.SimScenarios;
  assert("SimScenarios exported", S && typeof S.get === "function" && typeof S.resolveSnapshot === "function");
  const list = S.list();
  const ids = list.map((s) => s.id);
  assert("scenario list is substantial", list.length >= 62, list.length);
  assert("scenario ids are unique", new Set(ids).size === ids.length, ids);
  assert("listed scenarios include labels and descriptions", list.every((s) => s.id && s.label && s.description), list.filter((s) => !s.label || !s.description).slice(0, 5));
  assert("key scenarios exist", ["healthy", "model-offline", "haos-offline", "frigate-offline", "everything-offline", "room-entry-kitchen", "perception-auto-trigger-on-occupancy"].every((id) => ids.includes(id)), ids);
  assert("get unknown returns null", S.get("not-real") === null);
  assert("resolve unknown returns null", S.resolveSnapshot("not-real") === null);

  process.stdout.write("\nsimulation_scenario_baseline_merge_test\n");
  const healthy = S.resolveSnapshot("healthy");
  assert("healthy snapshot has online baseline", healthy.connection === "online" && healthy.bridgeOnline === true && healthy.sidecarOnline === true && healthy.visionSidecarOnline === true, healthy);
  assert("healthy snapshot carries bridge room context for metrics drawer", healthy.roomContext?.rooms?.kitchen?.occupied === true && healthy.roomContext.rooms.office.occupant === "Alex", healthy.roomContext);
  assert("healthy snapshot carries camera fixture map", healthy.cameraStates && healthy.cameraStates.living_room === "online-person-detected", healthy.cameraStates);
  assert("healthy snapshot carries seeded chat events", Array.isArray(healthy.events) && healthy.events.some((e) => e.kind === "perception"), healthy.events && healthy.events.slice(0, 3));
  const modelOffline = S.resolveSnapshot("model-offline");
  assert("model-offline patches model health over healthy baseline", modelOffline.metrics.model === null && modelOffline.metrics.gpu === 0 && modelOffline.connection === "online", modelOffline);
  assert("model-offline exposes AI Stack recovery state",
    modelOffline.aiStackOnline === true &&
    modelOffline.aiStackState?.overall === "down" &&
    modelOffline.aiStackState?.services?.some((svc) => svc.name === "vllm" && svc.container === "missing") &&
    modelOffline.aiStackState?.expected_vllm_model,
    modelOffline.aiStackState);
  const haosOffline = S.resolveSnapshot("haos-offline");
  assert("haos-offline keeps model data but marks HA down", haosOffline.connection === "offline" && haosOffline.bridgeHealth.ha_connected === false && haosOffline.metrics.model, haosOffline);
  const labHighVram = S.resolveSnapshot("metrics-timeline-high-vram");
  assert("baseline chains merge lab fixture and high VRAM patch", labHighVram.lab.turns.length === 21 && labHighVram.metrics.vram === 87.5 && labHighVram.aiStackState.overall === "ready", labHighVram);
  const roomEntry = S.get("room-entry-kitchen");
  assert("timeline scenarios preserve scripted deltas", Array.isArray(roomEntry.timeline) && roomEntry.timeline.length >= 4 && roomEntry.baseline === "healthy", roomEntry);

  process.stdout.write("\nsimulation_scenario_outage_invariant_test\n");
  const frigateOffline = S.resolveSnapshot("frigate-offline");
  assert("frigate-offline has no Frigate metrics", frigateOffline.frigateMetrics === null, frigateOffline);
  assert("frigate-offline clears bridge room context", frigateOffline.roomContext === null, frigateOffline.roomContext);
  assert("frigate-offline does not leave active camera-label flow", Object.keys(frigateOffline.cameraLabels || {}).length === 0 && !hasAnyCameraLabel(frigateOffline.cameraLabels), frigateOffline.cameraLabels);
  assert("frigate-offline marks sim camera states stale", Object.values(frigateOffline.cameraStates || {}).every((v) => v === "stale"), frigateOffline.cameraStates);
  const bridgeOffline = S.resolveSnapshot("bridge-offline");
  assert("bridge-offline clears bridge room context", bridgeOffline.bridgeOnline === false && bridgeOffline.roomContext === null, bridgeOffline);
  const everythingOffline = S.resolveSnapshot("everything-offline");
  assert("everything-offline clears dependency data", everythingOffline.connection === "offline" && everythingOffline.frigateMetrics === null && everythingOffline.bridgeHealth === null && everythingOffline.visionHealth === null && everythingOffline.visionSidecarOnline === false, everythingOffline);
  assert("everything-offline does not inherit healthy camera labels", Object.keys(everythingOffline.cameraLabels || {}).length === 0 && !hasAnyCameraLabel(everythingOffline.cameraLabels), everythingOffline.cameraLabels);
  assert("everything-offline marks every sim camera offline", Object.values(everythingOffline.cameraStates || {}).every((v) => v === "offline"), everythingOffline.cameraStates);

  process.stdout.write("\nsimulation_fixture_contract_test\n");
  const explain = api.__SIM_EXPLAIN_FIXTURE("conv-test");
  assert("explain fixture includes routing and tool-call timeline", explain.some((e) => e.kind === "routing_decision") && explain.filter((e) => e.kind === "tool_call").length >= 3, explain);
  assert("explain fixture preserves requested conversation id", explain.every((e) => e.conv_id === "conv-test"), explain);
  const world = api.__SIM_WORLD_STATE_FIXTURE();
  assert("world-state fixture has occupied rooms and people", world.enabled === true && world.rooms.kitchen.occupied === true && world.people.Alex.currently_seen === true, world);
  const spatial = api.__SIM_SPATIAL_MODEL_FIXTURE();
  assert("spatial fixture has calibration shape", spatial.calibrated === true && Object.keys(spatial.lights || {}).length >= 12 && spatial.lights["light.island_right"].vlm_disagreement === true, spatial);
  assert("spatial fixture does not expose a real LAN URL", !/\b(?:192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[0-1])\.)/.test(String(spatial.frigate_url || "")), spatial.frigate_url);
  const cameras = api.__SIM_CAMERAS_FIXTURE();
  assert("/cameras fixture returns data URI thumbnails", Object.keys(cameras).length >= 5 && Object.values(cameras).every((v) => String(v).startsWith("data:image/svg+xml;utf8,")), cameras);
  const clip = api.__SIM_DESCRIBE_CLIP_FIXTURE("kitchen", 4, 1.5);
  assert("describe-clip fixture matches sidecar shape", clip.camera === "kitchen" && clip.frames_captured === 4 && clip.span_s === 4.5 && clip.entity_id === "camera.kitchen", clip);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})();
