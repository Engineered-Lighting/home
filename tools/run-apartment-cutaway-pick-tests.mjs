#!/usr/bin/env node
/* Cut-away picking on an authored room with a ceiling over the floor and a
 * hairline floor crack. Under the dollhouse cut-away the viewer looks down
 * into the room: every tap on floor they can see must resolve to the floor,
 * never to the collision ceiling above it, and never to "no surface" through
 * the crack. Without the cut-away filters the pick is unchanged. */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { createPicking, cutawayPickFilters } from "../app/src/home-3d/picking.js";

let passed = 0;
function check(name, condition, detail = "") {
  if (!condition) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
  passed += 1;
  process.stdout.write(`PASS  ${name}\n`);
}

// Apartment frame: Z-up data under the engine's Y-up root rotation.
const root = new THREE.Group();
root.rotation.x = -Math.PI / 2;
const collisionMaterial = new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide });
function plane(width, height, position, rotation = [0, 0, 0]) {
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), collisionMaterial);
  mesh.position.set(...position); mesh.rotation.set(...rotation, "XYZ");
  return mesh;
}
// A 5 x 4 m room, 2.5 m high. The floor has a 1 cm crack along x = 2.5.
const collision = new THREE.Group();
collision.add(plane(2.495, 4, [2.495 / 2, 2, 0]));                        // floor A
collision.add(plane(2.495, 4, [2.505 + 2.495 / 2, 2, 0]));               // floor B
collision.add(plane(5, 4, [2.5, 2, 2.5]));                                // ceiling
collision.add(plane(5, 2.5, [2.5, 4, 1.25], [Math.PI / 2, 0, 0]));        // back wall (y = 4)
collision.add(plane(4, 2.5, [0, 2, 1.25], [Math.PI / 2, Math.PI / 2, 0])); // side wall (x = 0)
const desk = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 0.75), collisionMaterial);
desk.position.set(3.5, 1.5, 0.375); collision.add(desk);
root.add(collision);
root.updateMatrixWorld(true);

// Truth: the same room without its ceiling or crack.
const truthRoot = new THREE.Group(); truthRoot.rotation.x = -Math.PI / 2;
const truthFloor = plane(5, 4, [2.5, 2, 0]);
const truthOccluders = [plane(5, 2.5, [2.5, 4, 1.25], [Math.PI / 2, 0, 0]), plane(4, 2.5, [0, 2, 1.25], [Math.PI / 2, Math.PI / 2, 0]), desk.clone()];
truthRoot.add(truthFloor, ...truthOccluders); truthRoot.updateMatrixWorld(true);

// Looking down into the room from the open front-right corner, as the overview does.
const camera = new THREE.PerspectiveCamera(40, 800 / 600, 0.05, 200);
const eye = root.localToWorld(new THREE.Vector3(7.5, -3.5, 6.2)), look = root.localToWorld(new THREE.Vector3(2.4, 2.1, 0.4));
camera.position.copy(eye); camera.lookAt(look); camera.updateProjectionMatrix(); camera.updateMatrixWorld(true);
const host = { clientWidth: 800, clientHeight: 600, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }) };
const picking = createPicking(camera, host);
const filters = cutawayPickFilters({ floorZ: 0, topZ: 2.5 });

const truthRay = new THREE.Raycaster();
function truthAt(x, y) {
  truthRay.setFromCamera(new THREE.Vector2(x / 400 - 1, 1 - y / 300), camera);
  const hit = truthRay.intersectObjects([truthFloor, ...truthOccluders], false)[0];
  if (!hit || hit.object !== truthFloor) return null;
  return truthRoot.worldToLocal(hit.point.clone());
}

const grid = [];
for (let y = 6; y < 600; y += 12) for (let x = 6; x < 800; x += 12) grid.push([x, y]);
const floorTaps = grid.map(([x, y]) => ({ x, y, truth: truthAt(x, y) })).filter(tap => tap.truth);
check("the authored view shows plenty of floor", floorTaps.length > 300, String(floorTaps.length));

/* ---- the bug, reproduced ---------------------------------------------------- */
const plain = floorTaps.map(tap => picking.surfaceHit(root, [collision], tap.x, tap.y));
const onCeiling = plain.filter(hit => hit && hit.point[2] > 2.4).length, missed = plain.filter(hit => !hit).length;
check("without the filters, visible-floor taps land on the ceiling", onCeiling > floorTaps.length * 0.1, `${onCeiling}/${floorTaps.length}`);
check("without the filters, taps along the crack find no surface", missed > 0, String(missed));

/* ---- the fix ------------------------------------------------------------------ */
const fixed = floorTaps.map(tap => ({ tap, hit: picking.surfaceHitUnderCutaway(root, [collision], tap.x, tap.y, { rejectHit: filters.rejectHit, floorGap: filters.floorGap }) }));
const wrong = fixed.filter(({ hit }) => !hit || Math.abs(hit.point[2]) > 0.02 || hit.normal[2] < 0.7);
check("100% of visible-floor taps resolve to the floor under the cut-away", wrong.length === 0,
  `${wrong.length}/${fixed.length} wrong, e.g. ${JSON.stringify(wrong.slice(0, 2).map(({ tap, hit }) => [tap.x, tap.y, hit?.point]))}`);
const worst = Math.max(...fixed.map(({ tap, hit }) => Math.hypot(hit.point[0] - tap.truth.x, hit.point[1] - tap.truth.y)));
check("each floor tap lands where the ray meets the floor (crack taps included)", worst < 1e-6, String(worst));
const bridged = fixed.filter((_, index) => !plain[index]);
check("every tap that fell through the crack now resolves to the floor", bridged.length === missed && bridged.every(({ hit }) => hit && Math.abs(hit.point[2]) < .02),
  `${bridged.length} of ${missed}`);

const allFiltered = grid.map(([x, y]) => picking.surfaceHitUnderCutaway(root, [collision], x, y, { rejectHit: filters.rejectHit, floorGap: filters.floorGap }));
check("the ceiling is ignored under the cut-away (walls may still reach the band)", allFiltered.every(hit => !hit || hit.point[2] < filters.ceilingZ || Math.abs(hit.normal[2]) < .1)
  && allFiltered.some(hit => hit && hit.point[2] >= filters.ceilingZ));
const deskTop = (() => {
  const p = root.localToWorld(new THREE.Vector3(3.5, 1.5, 0.75)).project(camera);
  return [Math.round((p.x + 1) * 400), Math.round((1 - p.y) * 300)];
})();
const deskHit = picking.surfaceHitUnderCutaway(root, [collision], ...deskTop, { rejectHit: filters.rejectHit, floorGap: filters.floorGap });
check("furniture tops stay pickable", deskHit && Math.abs(deskHit.point[2] - 0.75) < 1e-6, JSON.stringify(deskHit?.point));
const wallPoint = root.localToWorld(new THREE.Vector3(1.5, 4, 1.4)).project(camera);
const wallHit = picking.surfaceHitUnderCutaway(root, [collision], Math.round((wallPoint.x + 1) * 400), Math.round((1 - wallPoint.y) * 300),
  { rejectHit: filters.rejectHit, floorGap: filters.floorGap });
check("kept walls stay pickable", wallHit && Math.abs(wallHit.point[1] - 4) < 1e-6 && Math.abs(wallHit.normal[2]) < .1, JSON.stringify(wallHit?.point));

/* ---- without the cut-away: unchanged ------------------------------------------ */
const strip = hit => hit && { point: hit.point, normal: hit.normal, object: hit.object.uuid };
const keepFront = point => point[1] > 0.5;
const same = grid.every(([x, y]) => JSON.stringify(strip(picking.surfaceHitUnderCutaway(root, [collision], x, y))) === JSON.stringify(strip(picking.surfaceHit(root, [collision], x, y)))
  && JSON.stringify(strip(picking.surfaceHitUnderCutaway(root, [collision], x, y, { acceptPoint: keepFront }))) === JSON.stringify(strip(picking.surfaceHit(root, [collision], x, y, { acceptPoint: keepFront }))));
check("with no filters the cut-away pick equals surfaceHit exactly", same);
const pickingSource = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../app/src/home-3d/picking.js"), "utf8");
check("surfaceHit itself is unchanged", pickingSource.includes("surfaceHit(apartmentRoot, objects, clientX, clientY, { acceptPoint = null, doubleSided = false } = {}) {" + String.fromCharCode(10) + "            const hits = this.pick(objects || [], clientX, clientY, { doubleSided });"));
check("pick() bounds are untouched by the gap search", picking.raycaster.near === 0 && picking.raycaster.far === Infinity);

/* ---- filter rules ---------------------------------------------------------------- */
check("the ceiling band is the top 0.35 m, never under 1.8 m", filters.ceilingZ === 2.15
  && cutawayPickFilters({ floorZ: 0, topZ: 2 }).ceilingZ === 1.8);
check("a horizontal hit seen from below is never rejected", !filters.rejectHit({ point: [0, 0, 2.5], normal: [0, 0, 1], eye: [0, 0, 1.6] }));
check("a vertical hit in the ceiling band is never rejected", !filters.rejectHit({ point: [0, 0, 2.4], normal: [1, 0, 0], eye: [0, 0, 6] }));
check("unusable bounds give no filters", cutawayPickFilters({ floorZ: 0, topZ: 0 }) === null && cutawayPickFilters({}) === null);

/* ---- engine wiring --------------------------------------------------------------- */
const engineSource = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../app/src/home-3d/engine.js"), "utf8");
const method = engineSource.slice(engineSource.indexOf("        displaySurfaceHit(clientX, clientY) {"));
const body = method.slice(0, method.indexOf("\n        },") + 10);
check("the engine uses the cut-away pick only while the cut-away is enabled, else plain surfaceHit",
  body.includes("const filters = policy?.enabled ? collisionCutawayFilters(collision) : null;")
  && body.includes("if (!filters) return picking.surfaceHit(apartmentRoot, [collision], clientX, clientY, { acceptPoint });")
  && body.includes("return picking.surfaceHitUnderCutaway(apartmentRoot, [collision], clientX, clientY, {")
  && body.includes("const acceptPoint = point => cutawayM.dollhouseKeep(point, policy) > .05;"));

process.stdout.write(`\n${passed} passed\n`);
