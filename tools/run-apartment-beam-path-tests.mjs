#!/usr/bin/env node
/* picking.beamPath and the aim-chip blocker adapter (APT-A01 validity copy:
 * "Blocked by ..." with a marker, the grazing note). beamPath is additive:
 * pick(), surfaceHit() and beamTrace() must return exactly what they did. */

import * as THREE from "three";
import { aimChipBlocker, BEAM_GRAZING_DEG, createPicking, surfaceClassFromNormal } from "../app/src/home-3d/picking.js";

let passed = 0;
function check(name, condition, detail = "") {
  if (!condition) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
  passed += 1;
  process.stdout.write(`PASS  ${name}\n`);
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;

// An Apartment root in the engine's convention: Z-up data under a Y-up scene.
const root = new THREE.Group();
root.rotation.x = -Math.PI / 2;
const doubleSided = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
const floor = new THREE.Mesh(new THREE.PlaneGeometry(10, 10), doubleSided);           // z = 0, facing +Z
const wall = new THREE.Mesh(new THREE.PlaneGeometry(10, 3), doubleSided);             // y = 3 plane
wall.rotation.x = Math.PI / 2; wall.position.set(0, 3, 1.5);
const desk = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 0.75), doubleSided);           // a box blocker
desk.position.set(0, 0, 0.375);
const room = new THREE.Group();
room.add(floor, wall, desk);
root.add(room);
root.updateMatrixWorld(true);

const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 200);
camera.position.set(0, 6, 8); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
const host = { clientWidth: 800, clientHeight: 600, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }) };
const picking = createPicking(camera, host);
const light = [2, 0, 2.4];

/* ---- clear path ---------------------------------------------------------- */
{
  const path = picking.beamPath(root, [room], light, [2.5, 3, 1.5]);
  check("a clear ray to the wall is clear", path.clear === true && path.blocker === null);
  check("the target distance is the straight span", near(path.target_distance_m, Math.hypot(0.5, 3, 0.9)));
  check("the hit is at the target", path.hit && Math.hypot(...path.hit.point_m.map((v, i) => v - [2.5, 3, 1.5][i])) < 1e-6);
  check("the hit normal faces the light", path.hit.normal_m[1] < -0.99, JSON.stringify(path.hit.normal_m));
  check("a vertical face is classed as a wall", path.hit.surface_class === "wall");
  check("a head-on aim is not grazing", path.grazing === false && path.incidence_deg < 30);
  check("the result is frozen", Object.isFrozen(path) && Object.isFrozen(path.hit) && Object.isFrozen(path.hit.point_m));
}
/* ---- blocked -------------------------------------------------------------- */
{
  const path = picking.beamPath(root, [room], light, [-1.5, 0, 0]);
  check("the desk blocks a ray to the floor behind it", path.clear === false && path.blocker !== null);
  check("the blocker is the first surface along the ray", path.blocker.distance_m < path.target_distance_m - 0.03);
  check("the blocker point lies on the desk", Math.abs(path.blocker.point_m[0]) <= 0.5 + 1e-6 && path.blocker.point_m[2] <= 0.75 + 1e-6);
  check("the blocker normal faces the light", path.blocker.normal_m[0] > 0 || path.blocker.normal_m[2] > 0, JSON.stringify(path.blocker.normal_m));
  check("a blocker without a semantic producer gets a class name", ["a surface", "a wall", "an object"].includes(path.blocker.name), path.blocker.name);
  check("incidence is not claimed while blocked", path.incidence_deg === null && path.grazing === false);
  check("hit and blocker report the same surface", path.hit.distance_m === path.blocker.distance_m && !("name" in path.hit));
  const chip = aimChipBlocker(path);
  check("the chip adapter returns point_m and name for #304's traceBeam", Array.isArray(chip.point_m) && typeof chip.name === "string"
    && chip.point_m.every((v, i) => v === path.blocker.point_m[i]) && chip.name === path.blocker.name);
  check("the chip adapter returns null for a clear path", aimChipBlocker(picking.beamPath(root, [room], light, [2.5, 3, 1.5])) === null);
  check("the chip adapter returns null for nothing", aimChipBlocker(null) === null);
}
{
  desk.userData.semantic_name = "Desk";
  const path = picking.beamPath(root, [room], light, [-1.5, 0, 0]);
  check("an explicit semantic label names the blocker", path.blocker.name === "Desk");
  delete desk.userData.semantic_name;
}
/* ---- tolerance, grazing, acceptPoint --------------------------------------- */
{
  const onDesk = [0.2, 0, 0.75];
  check("a target on the first surface is not blocked by itself", picking.beamPath(root, [room], light, onDesk).clear === true);
  const shallow = picking.beamPath(root, [room], [0, -4.8, 0.3], [0, -1, 0]);
  check("a shallow floor aim is grazing", shallow.clear && shallow.grazing === true && shallow.incidence_deg > BEAM_GRAZING_DEG,
    String(shallow.incidence_deg));
  check("the grazing threshold matches the aim chip", BEAM_GRAZING_DEG === 80);
  const skipped = picking.beamPath(root, [room], light, [-1.5, 0, 0], { acceptPoint: (point) => point[2] < 0.01 });
  check("acceptPoint skips faces that must not block", skipped.clear === true && skipped.hit.surface_class === "floor");
  const none = picking.beamPath(root, [wall], light, [-1.5, 0, 0]);
  check("a ray that meets nothing is clear with no hit", none.clear === true && none.hit === null && none.incidence_deg === null);
}
/* ---- input -------------------------------------------------------------------- */
{
  check("bad origin is null", picking.beamPath(root, [room], [0, 0], [1, 1, 1]) === null);
  check("non-finite target is null", picking.beamPath(root, [room], light, [NaN, 0, 0]) === null);
  check("zero span is null", picking.beamPath(root, [room], light, light.slice()) === null);
  check("missing root is null", picking.beamPath(null, [room], light, [0, 0, 0]) === null);
  check("surface classes cover every orientation", surfaceClassFromNormal([0, 0, 1], 0) === "floor"
    && surfaceClassFromNormal([0, 0, 1], 0.75) === "flat surface" && surfaceClassFromNormal([0, 0, -1]) === "ceiling"
    && surfaceClassFromNormal([1, 0, 0]) === "wall" && surfaceClassFromNormal([0.6, 0, 0.6]) === "surface"
    && surfaceClassFromNormal(null) === "surface");
}
/* ---- existing results unchanged ---------------------------------------------- */
{
  const before = picking.pick([room], 400, 300).map((hit) => [hit.object.uuid, hit.distance]);
  const surfaceBefore = picking.surfaceHit(root, [room], 400, 300);
  const traceBefore = picking.beamTrace(root, [room], light, [-0.8, 0, -0.5], 20, 5);
  picking.beamPath(root, [room], light, [-1.5, 0, 0]);
  const after = picking.pick([room], 400, 300).map((hit) => [hit.object.uuid, hit.distance]);
  check("pick() results are unchanged by beamPath", JSON.stringify(after) === JSON.stringify(before));
  check("pick() raycaster bounds are untouched", picking.raycaster.near === 0 && picking.raycaster.far === Infinity);
  const surfaceAfter = picking.surfaceHit(root, [room], 400, 300);
  check("surfaceHit() is unchanged", JSON.stringify(surfaceAfter.point) === JSON.stringify(surfaceBefore.point)
    && JSON.stringify(surfaceAfter.normal) === JSON.stringify(surfaceBefore.normal));
  const traceAfter = picking.beamTrace(root, [room], light, [-0.8, 0, -0.5], 20, 5);
  check("beamTrace() keeps its exact shape and values", JSON.stringify(Object.keys(traceAfter)) === JSON.stringify(["center", "footprint", "obstruction_point", "blocked"])
    && JSON.stringify(traceAfter.center.point) === JSON.stringify(traceBefore.center.point) && traceAfter.blocked === traceBefore.blocked);
}

process.stdout.write(`\n${passed} passed\n`);
