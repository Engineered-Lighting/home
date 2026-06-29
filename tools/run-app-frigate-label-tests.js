#!/usr/bin/env node
/* Pure local tests for HomeApp Frigate occupancy-label helper logic. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-app.jsx");
const source = fs.readFileSync(SRC, "utf8");

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

function loadHelpers() {
  const start = source.indexOf("function _escapeRegexPart");
  const end = source.indexOf("/* Tiny presentational footer", start);
  if (start < 0 || end < 0) throw new Error("HomeApp Frigate helper block not found");
  const script = source.slice(start, end) + `
Object.assign(window, {
  _escapeRegexPart,
  _cameraOccupancyRegex,
  _matchCameraOccupancyEntity,
  cameraLabelsFromStates,
  updateCameraLabelsFromEvent,
});`;
  const sandbox = { window: {}, RegExp, String, Set, Object };
  vm.runInNewContext(script, sandbox, { filename: "home-app.frigate-labels.js" });
  return sandbox.window;
}

(function main() {
  process.stdout.write("\napp_frigate_label_exports_test\n");
  const H = loadHelpers();
  assert("cameraLabelsFromStates exported in harness", typeof H.cameraLabelsFromStates === "function");
  assert("updateCameraLabelsFromEvent exported in harness", typeof H.updateCameraLabelsFromEvent === "function");
  assert("empty camera roster returns null regex", H._cameraOccupancyRegex([]) === null);
  assert("regex escapes camera ids before interpolation", H._escapeRegexPart("front.door+cam") === "front\\.door\\+cam", H._escapeRegexPart("front.door+cam"));

  process.stdout.write("\napp_frigate_label_hydration_test\n");
  const cameraIds = ["living_room", "kitchen", "front-door"];
  const hydrated = H.cameraLabelsFromStates(cameraIds, [
    { entity_id: "binary_sensor.kitchen_person_occupancy", state: "on" },
    { entity_id: "binary_sensor.kitchen_delivery_package_occupancy", state: "on" },
    { entity_id: "binary_sensor.kitchen_all_occupancy", state: "on" },
    { entity_id: "binary_sensor.living_room_cat_occupancy", state: "off" },
    { entity_id: "binary_sensor.driveway_person_occupancy", state: "on" },
    { entity_id: "binary_sensor.front-door_person_occupancy", state: "on" },
    { entity_id: "sensor.frigate_kitchen_fps", state: "10" },
  ]);
  assert("hydration keeps active labels by configured camera", JSON.stringify(hydrated) === JSON.stringify({
    kitchen: ["delivery package", "person"],
    "front-door": ["person"],
  }), hydrated);
  assert("aggregate all sensor is skipped", !JSON.stringify(hydrated).includes("all"), hydrated);
  assert("off and unconfigured sensors are skipped", !("living_room" in hydrated) && !("driveway" in hydrated), hydrated);
  assert("missing or malformed states hydrate to empty object", JSON.stringify(H.cameraLabelsFromStates(cameraIds, [null, {}, { entity_id: null }])) === "{}");

  process.stdout.write("\napp_frigate_label_event_test\n");
  let labels = H.updateCameraLabelsFromEvent(cameraIds, {}, {
    data: { entity_id: "binary_sensor.kitchen_person_occupancy", new_state: { state: "on" } },
  });
  assert("state_changed on adds label", JSON.stringify(labels) === JSON.stringify({ kitchen: ["person"] }), labels);
  labels = H.updateCameraLabelsFromEvent(cameraIds, labels, {
    data: { entity_id: "binary_sensor.kitchen_delivery_package_occupancy", new_state: { state: "on" } },
  });
  assert("state_changed keeps labels sorted", JSON.stringify(labels.kitchen) === JSON.stringify(["delivery package", "person"]), labels);
  labels = H.updateCameraLabelsFromEvent(cameraIds, labels, {
    data: { entity_id: "binary_sensor.kitchen_person_occupancy", new_state: { state: "off" } },
  });
  assert("state_changed off removes only that label", JSON.stringify(labels) === JSON.stringify({ kitchen: ["delivery package"] }), labels);
  labels = H.updateCameraLabelsFromEvent(cameraIds, labels, {
    data: { entity_id: "binary_sensor.kitchen_delivery_package_occupancy", new_state: { state: "unavailable" } },
  });
  assert("last label off/unavailable removes empty camera key", JSON.stringify(labels) === "{}", labels);
  labels = H.updateCameraLabelsFromEvent(cameraIds, labels, {
    data: { entity_id: "binary_sensor.living_room_cat_occupancy", new_state: { state: "on" } },
  });
  assert("event creates missing camera bucket", JSON.stringify(labels) === JSON.stringify({ living_room: ["cat"] }), labels);
  const sameAggregate = H.updateCameraLabelsFromEvent(cameraIds, labels, {
    data: { entity_id: "binary_sensor.living_room_all_occupancy", new_state: { state: "on" } },
  });
  assert("aggregate occupancy event is ignored without cloning", sameAggregate === labels, sameAggregate);
  const sameOther = H.updateCameraLabelsFromEvent(cameraIds, labels, {
    data: { entity_id: "binary_sensor.driveway_person_occupancy", new_state: { state: "on" } },
  });
  assert("unconfigured camera event is ignored without cloning", sameOther === labels, sameOther);
  const sameMalformed = H.updateCameraLabelsFromEvent(cameraIds, labels, { data: {} });
  assert("malformed event is ignored without cloning", sameMalformed === labels, sameMalformed);

  process.stdout.write("\napp_frigate_hook_wiring_source_test\n");
  assert("HomeApp hook hydrates labels through helper", source.includes("setCameraLabels(cameraLabelsFromStates(cameraIds, states));"));
  assert("HomeApp hook updates labels through reducer helper", source.includes("setCameraLabels((prev) => updateCameraLabelsFromEvent(cameraIds, prev, ev));"));
  assert("HomeApp still subscribes to state_changed", source.includes('client.subscribeEvents("state_changed"'));

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})();
