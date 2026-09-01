#!/usr/bin/env node

import * as THREE from "three";
import { createOverlay } from "../app/src/home-3d/markers.js";

const fakeGradient = { addColorStop() {} };
const fakeContext = {
  createRadialGradient() { return fakeGradient; },
  fillRect() {},
  clearRect() {},
  fillText() {},
  strokeRect() {},
  measureText() { return { width: 20 }; },
  set fillStyle(value) {},
  set strokeStyle(value) {},
  set font(value) {},
  set textAlign(value) {},
  set textBaseline(value) {},
  set lineWidth(value) {},
};

globalThis.document = {
  createElement() {
    return { width: 0, height: 0, getContext: () => fakeContext };
  },
};

let passed = 0;
function assert(name, condition) {
  if (!condition) throw new Error(`FAIL  ${name}`);
  passed += 1;
  process.stdout.write(`PASS  ${name}\n`);
}

const root = new THREE.Group();
const overlay = createOverlay(root);
overlay.setZones([
  { id: "kitchen", color: "#a8ffd8", floor_polygon: [[0, 0], [3, 0], [3, 2], [0, 2]] },
  { id: "dining", color: "#ffe2a8", floor_polygon: [[4, 0], [6, 0], [5, 2]] },
]);

assert("zone groups are created", overlay.zonesById.size === 2);
assert("only shaded zone bodies are pickable before a room is selected", overlay.zonePickObjects().length === 2);

overlay.setZonesVisible(0.7);
overlay.setZoneEdit("kitchen", true, 1);
const kitchen = overlay.zonesById.get("kitchen");
const dining = overlay.zonesById.get("dining");
assert("selected zone shows its four corner handles", kitchen.handles.visible && kitchen.handles.children.length === 4);
assert("unselected zone keeps handles hidden", !dining.handles.visible);
assert("only the selected room exposes its corner picks", overlay.zonePickObjects().length === 6);
assert("selected corner receives a larger visual handle", kitchen.handles.children[1].scale.x === 1.35);
assert("selected zone exposes a shaded drag body", kitchen.fill.material.opacity === 0.13);

overlay.previewZone("kitchen", [[0, 0], [3.5, 0], [3, 2], [0, 2]]);
assert("drag preview moves the chosen corner", kitchen.handles.children[1].position.x === 3.5);

const draftGroup = overlay.zonesGroup.children[0];
overlay.setZoneDraft([[0, 0], [1, 0], [1, 1]]);
assert("new-zone draft renders an open line and three handles", draftGroup.children.length === 4);
overlay.setZoneDraft([]);
assert("new-zone draft clears without touching existing zones",
  draftGroup.children.length === 0 && overlay.zonesById.size === 2);

process.stdout.write(`\n${passed} passed\n`);
