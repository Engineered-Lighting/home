#!/usr/bin/env node
/* Pure local tests for app/src/simulation-cameras.jsx. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "simulation-cameras.jsx");
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

function loadModule() {
  const sandbox = { window: {}, encodeURIComponent };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return sandbox.window;
}

function decodeSvg(dataUri) {
  const prefix = "data:image/svg+xml;utf8,";
  if (!String(dataUri).startsWith(prefix)) throw new Error("not an svg data URI");
  return decodeURIComponent(String(dataUri).slice(prefix.length));
}

(function main() {
  process.stdout.write("\nsimulation_camera_exports_test\n");
  const api = loadModule();
  assert("SimCameraSvg exported", typeof api.SimCameraSvg === "function");
  assert("SimCameraSvgFor exported", typeof api.SimCameraSvgFor === "function");
  assert("SimCameraSrcFor exported", typeof api.SimCameraSrcFor === "function");
  assert("default camera map exported", api.SimDefaultCameraMap && api.SimDefaultCameraMap.living_room === "online-person-detected", api.SimDefaultCameraMap);
  assert("default camera map covers six rooms", Object.keys(api.SimDefaultCameraMap).sort().join(",") === "dining_room,driveway,kitchen,living_room,office,workshop", api.SimDefaultCameraMap);

  process.stdout.write("\nsimulation_camera_svg_state_test\n");
  const personSvg = decodeSvg(api.SimCameraSvg("kitchen", "online-person-detected"));
  assert("person-detected SVG is a data URI with SIM badge", personSvg.includes(">SIM<") && personSvg.includes("KITCHEN"), personSvg.slice(0, 240));
  assert("person-detected SVG includes confidence bounding box", personSvg.includes("person") && personSvg.includes("0.92") && personSvg.includes('stroke="#7ce69b"'), personSvg);
  const lowSvg = decodeSvg(api.SimCameraSvg("living_room", "online-low-confidence"));
  assert("low-confidence SVG includes dashed uncertain box", lowSvg.includes("person?") && lowSvg.includes("0.41") && lowSvg.includes("stroke-dasharray"), lowSvg);
  const offlineSvg = decodeSvg(api.SimCameraSvg("driveway", "offline"));
  assert("offline SVG uses stripe pattern and offline label", offlineSvg.includes('id="stripes"') && offlineSvg.includes(">OFFLINE<"), offlineSvg);
  const staleSvg = decodeSvg(api.SimCameraSvg("workshop", "stale"));
  assert("stale SVG labels stale state", staleSvg.includes("STALE") && staleSvg.includes("WORKSHOP"), staleSvg);
  const loadingSvg = decodeSvg(api.SimCameraSvg("office", "loading"));
  assert("loading SVG contains shimmer animation", loadingSvg.includes('id="shim"') && loadingSvg.includes("repeatCount=\"indefinite\""), loadingSvg);
  const unknownStateSvg = decodeSvg(api.SimCameraSvg("mystery_room", "not-a-state"));
  assert("unknown camera/state falls back to default palette and offline tone", unknownStateSvg.includes("#222") && unknownStateSvg.includes("MYSTERY ROOM") && unknownStateSvg.includes(">OFFLINE<"), unknownStateSvg);

  process.stdout.write("\nsimulation_camera_src_selection_test\n");
  assert("online room with JPEG uses bundled snapshot path", api.SimCameraSrcFor("kitchen", { kitchen: "online-empty" }) === "simulation/cameras/sim_kitchen.jpg", api.SimCameraSrcFor("kitchen", { kitchen: "online-empty" }));
  assert("person-detected room with JPEG uses bundled snapshot path", api.SimCameraSrcFor("living_room", { living_room: "online-person-detected" }) === "simulation/cameras/sim_living_room.jpg", api.SimCameraSrcFor("living_room", { living_room: "online-person-detected" }));
  assert("low-confidence room with JPEG uses bundled snapshot path", api.SimCameraSrcFor("driveway", { driveway: "online-low-confidence" }) === "simulation/cameras/sim_driveway.jpg", api.SimCameraSrcFor("driveway", { driveway: "online-low-confidence" }));
  assert("offline room uses SVG placeholder", api.SimCameraSrcFor("kitchen", { kitchen: "offline" }).startsWith("data:image/svg+xml;utf8,"), api.SimCameraSrcFor("kitchen", { kitchen: "offline" }));
  assert("office has no JPEG and uses SVG even when online", api.SimCameraSrcFor("office", { office: "online-empty" }).startsWith("data:image/svg+xml;utf8,"), api.SimCameraSrcFor("office", { office: "online-empty" }));
  assert("missing state defaults to online-empty JPEG when available", api.SimCameraSrcFor("workshop", {}) === "simulation/cameras/sim_workshop.jpg", api.SimCameraSrcFor("workshop", {}));
  assert("SimCameraSvgFor defaults missing room state to online-empty", decodeSvg(api.SimCameraSvgFor("dining_room", {})).includes(">EMPTY<"), decodeSvg(api.SimCameraSvgFor("dining_room", {})));

  process.stdout.write("\nsimulation_camera_privacy_contract_test\n");
  const allOutputs = [
    api.SimCameraSrcFor("kitchen", { kitchen: "online-empty" }),
    api.SimCameraSrcFor("office", { office: "online-empty" }),
    api.SimCameraSvg("living_room", "online-person-detected"),
    api.SimCameraSvg("driveway", "offline"),
  ].join("\n");
  assert("camera fixtures do not include LAN URLs", !/\bhttps?:\/\/|ws:\/\//.test(allOutputs), allOutputs);
  assert("camera fixtures do not include HA entity ids", !/\bcamera\.[a-z0-9_]+/.test(allOutputs), allOutputs);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})();
