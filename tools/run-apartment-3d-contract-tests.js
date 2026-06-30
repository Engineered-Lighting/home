#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const APT = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment.jsx"), "utf8");
const MODES = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "modes.js"), "utf8");

let pass = 0;
let fail = 0;

function assert(name, cond, detail) {
  if (cond) {
    pass += 1;
    process.stdout.write(`  PASS  ${name}\n`);
  } else {
    fail += 1;
    process.stdout.write(`  FAIL  ${name}`);
    if (detail) process.stdout.write(`\n        ${detail}`);
    process.stdout.write("\n");
  }
}

process.stdout.write("\napartment_mesh_mode_contract_test\n");
assert("Spark renderer starts hidden", MODES.includes("sr.visible = false;"));
assert("visibleFor computes splatActive", MODES.includes("const splatActive = mode === 'splat' || !!state.fading;"));
assert("mesh/non-photo mode hides SplatMesh", MODES.includes("state.splat.visible = splatActive;"));
assert("mesh/non-photo mode hides SparkRenderer", MODES.includes("state.sparkRenderer.visible = splatActive;"));
assert("hidden splat opacity is forced to zero", MODES.includes("if (!splatActive && 'opacity' in state.splat) state.splat.opacity = 0;"));
assert("tickSpark is gated to active photo/fade modes", MODES.includes("state.mode === 'splat' || state.targetMode === 'splat'"));
assert("mode debug reports Spark visibility", MODES.includes("sparkVisible: state.sparkRenderer ? state.sparkRenderer.visible : null"));

process.stdout.write("\napartment_zoom_control_contract_test\n");
assert("AptZoomButton component exists", APT.includes("function AptZoomButton"));
assert("zoom controls have an accessible group label", APT.includes('aria-label="apartment zoom controls"'));
assert("zoom in button calls rig zoom in", APT.includes('title="zoom in"') && APT.includes("zoomApartment(1)"));
assert("zoom out button calls rig zoom out", APT.includes('title="zoom out"') && APT.includes("zoomApartment(-1)"));
assert("zoom controls hide during camera pose", APT.includes("const showZoomHud = !editing && !cameraTop"));
assert("debug API exposes zoomIn", APT.includes("zoomIn: () => zoomApartment(1)"));
assert("debug API exposes zoomOut", APT.includes("zoomOut: () => zoomApartment(-1)"));

if (fail) {
  process.stdout.write(`\n${pass} pass . ${fail} fail\n`);
  process.exit(1);
}
process.stdout.write(`\n${pass} pass . 0 fail\n`);
