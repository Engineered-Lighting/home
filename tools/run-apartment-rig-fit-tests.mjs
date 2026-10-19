#!/usr/bin/env node
/* Rig gestures and fit policy (APT-S04 fitting, APT-R01 pan, SPEC Gestures).
 *
 * Shift-drag and right-drag pan, two-finger pinch steps the zoom detents,
 * a second finger cancels an orbit, and a fit with keepPose refreshes the fit
 * without moving the owner's camera. Pure three.js; no WebGL. */

import * as THREE from "three";
import { attachInput, createRig, PINCH_STEP, ZOOM_FACTORS } from "../app/src/home-3d/rig.js";

let passed = 0;
function check(name, condition, detail = "") {
  if (!condition) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
  passed += 1;
  process.stdout.write(`PASS  ${name}\n`);
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;

function fittedRig() {
  const camera = new THREE.PerspectiveCamera(35, 1366 / 768, 0.05, 200);
  const rig = createRig(camera);
  const box = new THREE.Box3(new THREE.Vector3(-4, 0, -3), new THREE.Vector3(4, 2.6, 3));
  rig.fitBounds(box, { dur: 0 });
  return { camera, rig, box };
}
function settle(rig, seconds = 1) { for (let t = 0; t < seconds; t += 0.05) rig.update(0.05); }
function fakeHost() {
  const listeners = new Map();
  return {
    listeners,
    clientWidth: 1000, clientHeight: 800,
    addEventListener(type, handler) { listeners.set(type, handler); },
    removeEventListener(type) { listeners.delete(type); },
    setPointerCapture() {}, releasePointerCapture() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 800 }; },
    fire(type, event) { listeners.get(type)?.(event); },
  };
}

/* ---- fit and owner pose ------------------------------------------------ */
{
  const { rig, box } = fittedRig();
  check("a fresh fit is not an owner pose", rig.ownerPose() === false);
  rig.stepZoom(1, 0);
  settle(rig);
  check("a zoom step marks the owner pose", rig.ownerPose() === true);
  const radius = rig.currentRadius(), target = rig.state.target.clone(), zoom = rig.zoomIndex();
  const fitBefore = rig.state.fitDistance;
  rig.fitBounds(box, { dur: 0, keepPose: true, frame: { fovV: THREE.MathUtils.degToRad(30), aspect: 1.2 } });
  settle(rig);
  check("keepPose leaves the camera radius alone", near(rig.currentRadius(), radius), `${rig.currentRadius()} vs ${radius}`);
  check("keepPose leaves the orbit centre alone", rig.state.target.distanceTo(target) < 1e-9);
  check("keepPose leaves the zoom detent alone", rig.zoomIndex() === zoom);
  check("keepPose still refreshes the fit distance for later steps", !near(rig.state.fitDistance, fitBefore));
  check("keepPose keeps the owner pose", rig.ownerPose() === true);
  rig.fitBounds(box, { dur: 0 });
  check("an explicit fit returns to detent 0 and clears the owner pose", rig.zoomIndex() === 0 && rig.ownerPose() === false);
  rig.stepAzimuth(1, 0);
  check("an orbit step marks the owner pose", rig.ownerPose() === true);
  rig.snapHome(0);
  check("snapHome clears the owner pose", rig.ownerPose() === false);
  rig.stepElevation(-1, 0);
  check("a tilt against the lower stop is not an owner pose", rig.ownerPose() === false);
}
{
  const { rig, box } = fittedRig();
  const plain = rig.state.fitDistance;
  rig.fitBounds(box, { dur: 0, frame: { fovV: THREE.MathUtils.degToRad(35), aspect: 1366 / 768 } });
  check("a frame equal to the camera frustum fits identically", near(rig.state.fitDistance, plain, 1e-9));
  const shorter = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(35) / 2) * (768 - 80) / 768);
  rig.fitBounds(box, { dur: 0, frame: { fovV: shorter, aspect: 1366 / (768 - 80) } });
  check("a visible frame shortened by a toolbar inset fits from farther away", rig.state.fitDistance > plain,
    `${rig.state.fitDistance} vs ${plain}`);
}

/* ---- pan ---------------------------------------------------------------- */
{
  const { rig, camera } = fittedRig();
  rig.stepZoom(1, 0); rig.stepZoom(1, 0);
  settle(rig);
  const before = rig.state.target.clone();
  check("pan starts unlocked", rig.panStart() === true && rig.isPanning());
  rig.panMove(120, 0, 800);
  const moved = rig.state.target.clone().sub(before);
  camera.updateMatrixWorld(true);
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  check("a rightward pan drag moves the centre left, so the room follows the cursor", moved.dot(right) < 0,
    `dot=${moved.dot(right)}`);
  check("pan stays on the floor plane", near(moved.y, 0));
  rig.panEnd();
  check("an interior pan is kept and marks the owner pose", rig.state.target.distanceTo(before) > 0.01 && rig.ownerPose());
  rig.panStart(); rig.panMove(-100000, 100000, 800); rig.panEnd();
  const b = rig.homeTarget(); // fitted centre lies inside the box
  check("an interior pan is clamped to the fitted bounds", Math.abs(rig.state.target.x) <= 4 + 1e-9
    && Math.abs(rig.state.target.z) <= 3 + 1e-9, JSON.stringify(rig.state.target));
  check("the fitted centre is unchanged by panning", near(b.x, 0) && near(b.z, 0));
  const panned = rig.state.target.clone();
  rig.stepAzimuth(1, 0);
  check("an orbit step keeps the panned centre", rig.state.target.distanceTo(panned) < 1e-9);
}
{
  const { rig } = fittedRig();
  const home = rig.homeTarget();
  rig.panStart(); rig.panMove(400, 0, 800);
  const offset = rig.state.target.distanceTo(home);
  check("an overview pan previews with resistance and a cap", offset > 0 && offset <= 0.12 * rig.state.fitDistance + 1e-9,
    `offset=${offset}`);
  rig.panEnd();
  settle(rig);
  check("an overview pan springs back to the fitted centre", rig.state.target.distanceTo(home) < 1e-9);
  check("an overview pan is not an owner pose", rig.ownerPose() === false);
}
{
  const { rig } = fittedRig();
  rig.stepZoom(1); // a 350 ms tween in flight
  rig.update(0.1);
  check("a pan started mid-tween lands the tween first", rig.panStart() && !rig.isTweening());
  rig.panEnd();
}

/* ---- pointer input ------------------------------------------------------ */
{
  const { rig } = fittedRig();
  rig.stepZoom(1, 0); settle(rig);
  const host = fakeHost();
  const detach = attachInput(host, rig);
  const az = rig.azimuthIndex(), target = rig.state.target.clone();
  host.fire("pointerdown", { button: 0, shiftKey: true, clientX: 500, clientY: 400, pointerId: 1, pointerType: "mouse" });
  host.fire("pointermove", { clientX: 440, clientY: 380, pointerId: 1, pointerType: "mouse" });
  host.fire("pointerup", { clientX: 440, clientY: 380, pointerId: 1, pointerType: "mouse" });
  check("Shift-drag pans instead of orbiting", rig.azimuthIndex() === az && rig.state.target.distanceTo(target) > 0.01);
  const afterShift = rig.state.target.clone();
  host.fire("pointerdown", { button: 2, clientX: 500, clientY: 400, pointerId: 2, pointerType: "mouse" });
  host.fire("pointermove", { clientX: 560, clientY: 400, pointerId: 2, pointerType: "mouse" });
  host.fire("pointerup", { button: 2, clientX: 560, clientY: 400, pointerId: 2, pointerType: "mouse" });
  check("right-drag pans", rig.azimuthIndex() === az && rig.state.target.distanceTo(afterShift) > 0.01);
  let prevented = false;
  host.fire("contextmenu", { preventDefault() { prevented = true; } });
  check("the canvas context menu is suppressed for right-drag pan", prevented);
  host.fire("pointerdown", { button: 0, clientX: 500, clientY: 400, pointerId: 3, pointerType: "mouse" });
  host.fire("pointermove", { clientX: 440, clientY: 400, pointerId: 3, pointerType: "mouse" });
  host.fire("pointerup", { clientX: 440, clientY: 400, pointerId: 3, pointerType: "mouse" });
  check("a plain drag still orbits one detent", rig.azimuthIndex() !== az);
  detach();
  check("detach removes every listener", host.listeners.size === 0);
}
{
  const { rig } = fittedRig();
  const host = fakeHost();
  const steps = [];
  const detach = attachInput(host, rig, { onZoomStep: (direction, meta) => { steps.push([direction, meta.source]); rig.stepZoom(direction, 0); } });
  const az = rig.azimuthIndex();
  host.fire("pointerdown", { button: 0, clientX: 450, clientY: 400, pointerId: 11, pointerType: "touch" });
  host.fire("pointermove", { clientX: 430, clientY: 400, pointerId: 11, pointerType: "touch" });
  host.fire("pointerdown", { button: 0, clientX: 550, clientY: 400, pointerId: 12, pointerType: "touch" });
  const d0 = 120;
  const spreadTo = d => {
    host.fire("pointermove", { clientX: 490 - d / 2, clientY: 400, pointerId: 11, pointerType: "touch" });
    host.fire("pointermove", { clientX: 490 + d / 2, clientY: 400, pointerId: 12, pointerType: "touch" });
  };
  spreadTo(d0 * PINCH_STEP * 1.01);
  check("one PINCH_STEP of spread zooms in one detent through the host zoom path",
    steps.length === 1 && steps[0][0] === 1 && steps[0][1] === "pinch", JSON.stringify(steps));
  spreadTo(d0 * PINCH_STEP * PINCH_STEP * 1.01);
  check("two steps of spread reach detent 2", rig.zoomIndex() === 2 && steps.length === 2);
  spreadTo(d0 * 0.99);
  check("closing the pinch walks back down the same detents", rig.zoomIndex() === 0
    && steps.slice(2).every(([direction]) => direction === -1), JSON.stringify(steps));
  host.fire("pointerup", { clientX: 450, clientY: 400, pointerId: 11, pointerType: "touch" });
  host.fire("pointermove", { clientX: 700, clientY: 400, pointerId: 12, pointerType: "touch" });
  host.fire("pointerup", { clientX: 700, clientY: 400, pointerId: 12, pointerType: "touch" });
  settle(rig);
  check("the second finger cancelled the orbit and the finger left after a pinch never orbits", rig.azimuthIndex() === az);
  check("pinch walks the same five detents", rig.detentCount() === ZOOM_FACTORS.length && ZOOM_FACTORS.length === 5);
  host.fire("pointerdown", { button: 0, clientX: 500, clientY: 400, pointerId: 13, pointerType: "touch" });
  host.fire("pointermove", { clientX: 440, clientY: 400, pointerId: 13, pointerType: "touch" });
  host.fire("pointerup", { clientX: 440, clientY: 400, pointerId: 13, pointerType: "touch" });
  check("a one-finger drag after the pinch orbits again", rig.azimuthIndex() !== az);
  detach();
}

process.stdout.write(`\n${passed} passed\n`);
