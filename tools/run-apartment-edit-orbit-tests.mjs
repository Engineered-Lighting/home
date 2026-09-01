#!/usr/bin/env node

import * as THREE from "three";
import { attachInput, createRig } from "../app/src/home-3d/rig.js";
import { createPicking } from "../app/src/home-3d/picking.js";

let passed = 0;
function check(name, condition, detail = "") {
  if (!condition) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
  passed += 1;
  process.stdout.write(`PASS  ${name}\n`);
}

const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 200);
const rig = createRig(camera);
rig.state.fitDistance = 20;
const startingAzimuth = rig.state.az;
rig.goEditPose(0);
check("edit pose leaves orbit input unlocked", rig.state.locked === false);
check("edit pose starts at the 35-degree isometric elevation", rig.state.el === 0);
check("edit pose preserves the visible azimuth", rig.state.az === startingAzimuth);

const listeners = new Map();
const host = {
  clientWidth: 1000,
  addEventListener(type, handler) { listeners.set(type, handler); },
  removeEventListener(type) { listeners.delete(type); },
  setPointerCapture() {},
  releasePointerCapture() {},
  getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 800 }; },
};
const detach = attachInput(host, rig);
listeners.get("pointerdown")({ button: 0, clientX: 500, clientY: 400, pointerId: 1 });
listeners.get("pointermove")({ clientX: 465, clientY: 400, pointerId: 1 });
listeners.get("pointerup")({ clientX: 465, clientY: 400, pointerId: 1 });
check("short left-button drag commits an edit-mode orbit step", rig.state.az !== startingAzimuth, `az=${rig.state.az}`);
detach();

const root = new THREE.Group();
root.rotation.x = -Math.PI / 2;
const wall = new THREE.Mesh(new THREE.PlaneGeometry(4, 3),
  new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
wall.rotation.x = Math.PI / 2;
wall.position.set(0, 2, 1);
root.add(wall);
root.updateMatrixWorld(true);
camera.position.set(0, 1, 5);
camera.lookAt(0, 1, -2);
camera.updateProjectionMatrix();
camera.updateMatrixWorld(true);
const picking = createPicking(camera, host);
const hit = picking.surfaceHit(root, [wall], 500, 400);
check("art picking hits an actual vertical mesh face", Boolean(hit));
check("art picking returns a vertical apartment-frame normal",
  Math.abs(hit.normal[2]) <= 0.65, `normal=${JSON.stringify(hit.normal)}`);

process.stdout.write(`\n${passed} passed\n`);
