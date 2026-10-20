/* APT-U04/U05: named-aim geometry (pure). Authored meshes and fixtures only. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const N = require('../app/src/home-apartment-named-aims.js');
const Aiming = require('../app/src/home-apartment-aiming.js');

const close = (actual, expected, tolerance = 1e-3, label = '') => {
  if (Array.isArray(expected)) expected.forEach((value, i) => close(actual[i], value, tolerance, label + '[' + i + ']'));
  else assert.ok(Math.abs(actual - expected) <= tolerance, `${label} ${actual} ≉ ${expected}`);
};

/* A room floor, one wall, and a 1.70 × 0.60 m desk whose top is at 0.74 m.
   Triangles are listed per face so the region search must stop at edges. */
function deskMesh({ x0 = 1.2, y0 = 2.9, w = 1.7, d = 0.6, h = 0.74 } = {}) {
  const quads = [
    [[0, 0, 0], [4, 0, 0], [4, 4, 0], [0, 4, 0]],                                     // floor
    [[0, 4, 0], [4, 4, 0], [4, 4, 2.5], [0, 4, 2.5]],                                 // back wall
    [[x0, y0, h], [x0 + w, y0, h], [x0 + w, y0 + d, h], [x0, y0 + d, h]],             // desk top
    [[x0, y0, 0], [x0 + w, y0, 0], [x0 + w, y0, h], [x0, y0, h]],                     // desk front
  ];
  const positions = [];
  for (const [a, b, c, e] of quads) positions.push(...a, ...b, ...c, ...a, ...c, ...e);
  return new Float32Array(positions);
}

test('planarRegion stops at the desk edge and fitRectangle recovers 1.70 × 0.60 m at the top centre', () => {
  const positions = deskMesh();
  const region = N.planarRegion({ positions, faceIndex: 4 });
  assert.equal(region.triangles, 2, 'only the two desk-top triangles, not the floor or the front');
  close(region.area, 1.02, 1e-3, 'area');
  const fit = N.fitRectangle(region.points, region.normal);
  close(fit.size, [1.7, 0.6], 1e-6, 'size'); close(fit.center, [2.05, 3.2, 0.74], 1e-3, 'centre');
  close(fit.up, [0, 1, 0], 1e-4, 'axis-aligned desk keeps the default orientation');
});

test('planarRegion honours a geometry-to-Apartment matrix (a Y-up mesh under a Z-up root)', () => {
  const zUp = deskMesh();
  const yUp = new Float32Array(zUp.length);
  for (let i = 0; i < zUp.length; i += 3) { yUp[i] = zUp[i]; yUp[i + 1] = zUp[i + 2]; yUp[i + 2] = -zUp[i + 1]; }
  // column-major: x' = x, y' = -z, z' = y
  const matrix = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1];
  const region = N.planarRegion({ positions: yUp, faceIndex: 4, matrix });
  const fit = N.fitRectangle(region.points, region.normal);
  close(fit.size, [1.7, 0.6], 1e-5); close(fit.center, [2.05, 3.2, 0.74], 1e-3);
});

test('a rotated desk fits its own rectangle and a stored up that turns the aim with it', () => {
  const a = 30 * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  const local = [[-0.85, -0.3], [0.85, -0.3], [0.85, 0.3], [-0.85, 0.3]].map(([x, y]) => [2 + x * c - y * s, 2 + x * s + y * c, 0.74]);
  const positions = new Float32Array([...local[0], ...local[1], ...local[2], ...local[0], ...local[2], ...local[3]]);
  const fit = N.fitRectangle(N.planarRegion({ positions, faceIndex: 0 }).points, [0, 0, 1]);
  close(fit.size, [1.7, 0.6], 1e-3); close(fit.center, [2, 2, 0.74], 1e-3);
  close(Math.abs(fit.up[0] * -s + fit.up[1] * c), 1, 1e-3, 'up runs along the desk depth');
});

test('the HANDOFF example: clicking the desk top suggests "Desk", type Table, fitted to 1.70 × 0.60 m', () => {
  const positions = deskMesh(), region = N.planarRegion({ positions, faceIndex: 4 });
  const draft = N.draftFromPick({ point: [1.6, 3.0, 0.74], normal: [0, 0, 1], region, surfaceName: 'Desk', targets: [] });
  assert.equal(draft.name, 'Desk'); assert.equal(draft.category, 'table'); assert.equal(draft.horizontal, true);
  close(draft.size_m, [1.7, 0.6], 1e-6); close(draft.pos, [2.05, 3.2, 0.74]);
  assert.deepEqual(N.validate(draft, []), { ok: true, nameError: '', typeError: '' });
  const second = N.draftFromPick({ point: [1.6, 3.0, 0.74], normal: [0, 0, 1], region, surfaceName: 'Desk', targets: [{ id: 't', name: 'desk' }] });
  assert.equal(second.name, 'Desk 2', 'suggestions stay unique per home');
});

test('walls and the floor start from presets; generic mesh names fall back to the type', () => {
  const positions = deskMesh();
  const wall = N.draftFromPick({ point: [2, 4, 1.4], normal: [0, -1, 0], region: N.planarRegion({ positions, faceIndex: 2 }), surfaceName: 'collision' });
  assert.equal(wall.category, 'custom'); assert.equal(wall.name, 'Wall spot'); assert.deepEqual(wall.size_m, [0.6, 0.6]);
  close(wall.pos, [2, 4, 1.4]); assert.equal(wall.horizontal, false);
  const floor = N.draftFromPick({ point: [1, 1, 0], normal: [0, 0, 1], region: N.planarRegion({ positions, faceIndex: 0 }), surfaceName: 'mesh_0' });
  assert.equal(floor.category, 'custom'); assert.equal(floor.name, 'Floor spot'); assert.deepEqual(floor.size_m, [0.6, 0.6]);
});

test('save checks: empty and duplicate names, and type/surface agreement', () => {
  const flat = { id: null, name: 'Desk', category: 'table', horizontal: true };
  assert.match(N.validate({ ...flat, name: '  ' }).nameError, /Name this aim/);
  assert.match(N.validate(flat, [{ id: 'x', name: ' desk ' }]).nameError, /already called “Desk”/);
  assert.equal(N.validate({ ...flat, id: 'x' }, [{ id: 'x', name: 'Desk' }]).ok, true, 'editing keeps its own name');
  assert.match(N.validate({ ...flat, category: 'art' }).typeError, /Art goes on a wall/);
  assert.match(N.validate({ ...flat, horizontal: false }).typeError, /Tables and islands are flat/);
  assert.equal(N.validate({ ...flat, horizontal: false, category: 'custom' }).ok, true);
});

test('toTarget keeps the existing apartment_model target shape', () => {
  const target = N.toTarget({ name: ' Desk ', category: 'table', shape: 'surface', pos: [2.05, 3.2, 0.74], normal: [0, 0, 1],
    up: [0, 1, 0], size_m: [1.7, 0.6] }, { id: 'target-desk-1', roomId: 'study', now: '2026-10-19T12:00:00.000Z' });
  assert.deepEqual(target, { id: 'target-desk-1', name: 'Desk', category: 'table', shape: 'surface', pos: [2.05, 3.2, 0.74],
    normal: [0, 0, 1], up: [0, 1, 0], room_id: 'study', source: 'manual', confidence: 1, updated_at: '2026-10-19T12:00:00.000Z', size_m: [1.7, 0.6] });
  assert.equal('size_m' in N.toTarget({ name: 'P', category: 'custom', shape: 'point', pos: [1, 1, 1], normal: [0, 0, 1], up: [0, 1, 0] },
    { id: 'p', now: 'n' }), false, 'a point aim has no size');
});

test('Reach uses the existing solver: in reach, out of tilt range, blocked, no position, and lights that cannot aim are not listed', () => {
  const engineered = (id, name, pos, limits = {}) => Aiming.normalizeEngineeredFixture({ id, name, pos, type: 'light', fixture_kind: Aiming.ENGINEERED_KIND,
    gimbal: { limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90, ...limits } } });
  const devices = [engineered('a', 'Ceiling A', [1.2, 1, 2.35]), engineered('b', 'Ceiling B', [2.8, 2.6, 2.35], { tilt_min_deg: 70 }),
    engineered('c', 'Ceiling C', [3.5, 0.5, 2.35]), engineered('d', 'Unplaced', null),
    { id: 'lamp', name: 'Lamp', type: 'light', pos: [3, 1, 0.8] }];
  const draft = { pos: [2.05, 3.2, 0.74] };
  const rows = N.reach({ draft, devices, aiming: Aiming, trace: device => device.id === 'c' });
  assert.deepEqual(rows.map(row => [row.id, row.ok, row.reason]), [['a', true, null], ['b', false, 'out_of_reach'], ['c', false, 'blocked'], ['d', false, 'no_position']]);
  close(rows[0].direction.reduce((sum, v) => sum + v * v, 0), 1, 1e-9, 'unit beam direction for Preview');
  assert.equal(N.reachSentence(rows), 'Ceiling A can reach it. Ceiling B is out of reach, Ceiling C is blocked, Unplaced has no position.');
});

test('corner drags resize with the opposite corner fixed, minimum 5 cm and 1 cm rounding, on the aim plane', () => {
  const d0 = { pos: [2, 2, 0.74], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1, 0.5], shape: 'surface' };
  const before = N.corners(d0);
  const step = N.dragHandle(d0, 'c0', before.c0, [before.c0[0] + 0.234, before.c0[1] + 0.1, 0.74 + 0.5]);
  close(step.size_m, [1.23, 0.6], 1e-9); assert.equal(step.pos[2], 0.74, 'height never leaves the plane');
  const after = N.corners({ ...d0, ...step });
  close(after.c2, before.c2, 1e-3, 'opposite corner stays put');
  const tiny = N.dragHandle(d0, 'c0', before.c0, before.c2);
  close(tiny.size_m, [0.05, 0.05], 1e-9);
  const wall = { pos: [2, 4, 1.4], normal: [0, -1, 0], up: [0, 0, 1], size_m: [0.9, 0.65], shape: 'surface' };
  const moved = N.dragHandle(wall, 'mid', [2, 4, 1.4], [2.3, 3.6, 1.5]);
  close(moved.pos, [2.3, 4, 1.5], 1e-9, 'the centre moves within the wall plane; the normal component is dropped');
});

test('the lift arrow raises or lowers a flat aim within the room; walls have no lift and nudges stay in plane', () => {
  const flat = { pos: [2, 2, 0.74], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1, 0.5], shape: 'surface' };
  assert.deepEqual(N.dragHandle(flat, 'lift', [2, 2, 1.06], [2.1, 2, 1.36], { roomTop: 2.5 }), { pos: [2, 2, 1.04] });
  assert.deepEqual(N.dragHandle(flat, 'lift', [2, 2, 1.06], [2, 2, 9], { roomTop: 2.5 }), { pos: [2, 2, 2.5] });
  assert.deepEqual(N.dragHandle(flat, 'lift', [2, 2, 1.06], [2, 2, -9]), { pos: [2, 2, 0] });
  const points = N.handlePoints(flat);
  close(points.lift, [2, 2, 0.74 + N.LIFT_HANDLE_M]); close(points.floor, [2, 2, 0], 0, 'the dashed drop line ends on the floor');
  const wall = { pos: [2, 4, 1.4], normal: [0, -1, 0], up: [0, 0, 1], size_m: [0.9, 0.65], shape: 'surface' };
  assert.equal(N.dragHandle(wall, 'lift', [0, 0, 0], [0, 0, 1]), null); assert.equal('lift' in N.handlePoints(wall), false);
  close(N.nudge(wall, 1, 0).pos, [2.05, 4, 1.4], 1e-9, 'right along the wall, as seen facing it'); close(N.nudge(wall, 0, 1).pos, [2, 4, 1.45], 1e-9, 'up the wall');
  close(N.nudge(flat, 0, 1).pos, [2, 2.05, 0.74], 1e-9, 'away on a table');
});

test('flat aims rotate in 15° steps in their own plane; plane hits come from the camera ray', () => {
  let draft = { pos: [2, 2, 0.74], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1, 0.5], shape: 'surface' };
  for (let i = 0; i < 3; i++) draft = { ...draft, ...N.rotateFlat(draft, 15) };
  assert.equal(N.flatRotation(draft), 45);
  close(N.rayPlane([0, 0, 2], [0, 0, -1], [5, 5, 0.74], [0, 0, 1]), [0, 0, 0.74], 1e-9);
  assert.equal(N.rayPlane([0, 0, 2], [1, 0, 0], [0, 0, 0.74], [0, 0, 1]), null);
  const plane = N.dragPlane({ pos: [2, 2, 0.74], normal: [0, 0, 1] }, 'lift', [0, -1, -1]);
  close(plane.normal, [0, -1, 0], 1e-9, 'the lift plane faces the camera and is vertical');
});

test('exact numeric entry stays on the aim plane', () => {
  const wall = { pos: [2, 4, 1.4], normal: [0, -1, 0], up: [0, 0, 1] }, flat = { pos: [2, 2, 0.74], normal: [0, 0, 1], up: [0, 1, 0] };
  assert.deepEqual(N.exactPosition(wall, 0, '2.5'), { pos: [2.5, 4, 1.4] });
  assert.deepEqual(N.exactPosition(wall, 1, '3'), { pos: [2, 4, 1.4] }, 'the wall-normal axis cannot leave the wall');
  assert.deepEqual(N.exactPosition(flat, 2, '0.9'), { pos: [2, 2, 0.9] }, 'a flat aim takes an exact height');
  assert.equal(N.exactPosition(flat, 0, 'x'), null);
});
