// The Set-aim beam sweep (SPEC Aim lifecycle: 650 ms, instant under reduced
// motion) on the real symbolic fixture factory with resident Three objects.
// Drawing only: state, targets, readback, descriptor, pick and aim handle take
// the new aim at once, and callers that do not ask for a sweep are unchanged.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';
import Core from '../app/src/home-equipment-registration.js';
import { authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceFile = name => path.join(ROOT, 'app/src/home-3d', name);
const dataModule = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const threeURL = pathToFileURL(path.join(ROOT, 'app/src/vendor/three-0.180.0/three.module.js')).href;
function replaceOne(source, from, to) {
  assert.equal(source.split(from).length - 1, 1, 'one actual module-resolution seam: ' + from);
  return source.replace(from, to);
}
const spotlight = dataModule(replaceOne(fs.readFileSync(sourceFile('equipment-spotlight-visual.mjs'), 'utf8'),
  "from 'three'", 'from ' + JSON.stringify(threeURL)));
let actual = replaceOne(fs.readFileSync(sourceFile('equipment-virtual-fixtures.mjs'), 'utf8'), "from 'three'", 'from ' + JSON.stringify(threeURL));
actual = replaceOne(actual, 'import(`./equipment-spotlight-visual.mjs${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(spotlight) + ')');
for (const name of ['home-den-fixtures.js', 'home-apartment-aiming.js']) actual = replaceOne(actual,
  'import(`../' + name + '${new URL(import.meta.url).search}`)',
  'import(' + JSON.stringify(pathToFileURL(path.join(ROOT, 'app/src', name)).href) + ')');
const { createEquipmentVirtualFixtures, AIM_SWEEP_MS } = await import(dataModule(actual));
const binding = { content_revision: 'authored-victoria-aim-sweep-r1', registration_version: 1,
  manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
const FIRST = { point: [2, 2, .5], normal: [0, 0, 1], kind: 'authored_floor' };
const SECOND = { point: [-1, 2.5, 0], normal: [0, 0, 1], kind: 'authored_floor' };

function clock(t, start = 1000) {
  const value = { now: start }; t.mock.method(performance, 'now', () => value.now); return value;
}
function globalAdapter(t, name, value) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  t.after(() => { if (previous) Object.defineProperty(globalThis, name, previous); else delete globalThis[name]; });
}
function fixture(t) {
  const parent = new THREE.Group(), api = createEquipmentVirtualFixtures(parent, { binding });
  const loaded = api.setDocument(authoredDocument(binding, Core));
  assert.equal(loaded.status, 'ready');
  const id = loaded.fixtures[0].fixture_id, root = api.root.children.find(node => node.userData.fixture_id === id);
  api.setSelected(id);
  assert.equal(api.aimAtTarget(id, FIRST).ok, true);
  t.after(() => api.dispose());
  return { api, id, root };
}
// The drawn spot endpoint in the fixture's frame, from the beam volume.
function drawnEndpoint(f) {
  const volume = f.root.getObjectByName('modeled-aim-volume');
  const direction = new THREE.Vector3(0, -1, 0).applyQuaternion(volume.quaternion);
  return volume.position.clone().addScaledVector(direction, volume.scale.y / 2).add(f.root.position);
}
const truth = f => f.root.getObjectByName('robotic-spotlight').userData;
const near = (a, b, eps = 1e-6) => a.distanceTo(b) <= eps;
function drawing(f) {
  const read = name => { const node = f.root.getObjectByName(name);
    return { position: node.position.toArray(), quaternion: node.quaternion.toArray(), scale: node.scale.toArray(), visible: node.visible,
      positions: node.geometry?.attributes?.position ? [...node.geometry.attributes.position.array] : null }; };
  return ['modeled-aim-volume', 'modeled-aim-source-glow', 'modeled-aim-receiver-glow'].map(read);
}

test('the sweep constant is the SPEC 650 ms', () => assert.equal(AIM_SWEEP_MS, 650));

test('without sweep, aimAtTarget draws the new aim at once exactly as before', t => {
  const time = clock(t), f = fixture(t);
  assert.equal(f.api.aimAtTarget(f.id, SECOND).ok, true);
  assert.ok(near(drawnEndpoint(f), new THREE.Vector3(...SECOND.point), 1e-5), 'the drawn beam ends on the new target');
  assert.equal(f.api.advancePresentation(time.now + 16), false, 'nothing is left to animate');
});

test('Set aim sweeps only the drawing over 650 ms with an ease-out cubic', t => {
  const time = clock(t), f = fixture(t), reference = fixture(t);
  assert.equal(reference.api.aimAtTarget(reference.id, SECOND).ok, true);
  const before = drawnEndpoint(f);
  const result = f.api.aimAtTarget(f.id, SECOND, { sweep: true });
  assert.equal(result.ok, true);
  assert.deepEqual(result.descriptor.target, reference.api.interactionDescriptor(reference.id).target, 'the descriptor takes the new target at once');
  assert.deepEqual(result.descriptor.state, reference.api.interactionDescriptor(reference.id).state, 'the state takes the new aim at once');
  const strip = readback => ({ ...readback, depth: null });
  assert.deepEqual(strip(f.api.readback()), strip(reference.api.readback()), 'readback is identical to an unswept aim, shape included');
  assert.deepEqual(truth(f).endpoint_m, truth(reference).endpoint_m, 'metadata describes the exact aim, never the eased drawing');
  for (const name of ['virtual-fixture-aim-handle', 'virtual-fixture-aim-pick', 'virtual-fixture-pick'])
    assert.deepEqual(f.root.getObjectByName(name).position.toArray(), reference.root.getObjectByName(name).position.toArray(), name + ' is at the true aim');
  assert.ok(near(drawnEndpoint(f), before, 1e-5), 'the sweep starts from what was drawn');

  time.now += AIM_SWEEP_MS / 2;
  assert.equal(f.api.advancePresentation(time.now), true);
  const halfway = drawnEndpoint(f), expected = before.clone().lerp(new THREE.Vector3(...SECOND.point), 1 - Math.pow(.5, 3));
  assert.ok(near(halfway, expected, 1e-4), 'halfway in time is 87.5% of the way (ease-out cubic): ' + halfway.toArray() + ' vs ' + expected.toArray());
  assert.deepEqual(truth(f).endpoint_m, truth(reference).endpoint_m, 'metadata is re-asserted after every eased draw');

  time.now += AIM_SWEEP_MS / 2;
  assert.equal(f.api.advancePresentation(time.now), true);
  assert.deepEqual(drawing(f), drawing(reference), 'the finished sweep draws exactly what an unswept aim draws');
  assert.equal(f.api.advancePresentation(time.now + 16), false, 'the sweep then rests');
});

test('reduced motion makes the sweep instant', t => {
  clock(t); globalAdapter(t, 'matchMedia', () => ({ matches: true }));
  const f = fixture(t), reference = fixture(t);
  reference.api.aimAtTarget(reference.id, SECOND);
  f.api.aimAtTarget(f.id, SECOND, { sweep: true });
  assert.deepEqual(drawing(f), drawing(reference));
});

test('a hidden layer snaps the sweep, and a later change lands it on the exact aim', t => {
  const time = clock(t), f = fixture(t), reference = fixture(t);
  reference.api.aimAtTarget(reference.id, SECOND);
  f.api.aimAtTarget(f.id, SECOND, { sweep: true });
  time.now += 100; f.api.advancePresentation(time.now);
  f.api.setVisible(false);
  assert.deepEqual(drawing(f), drawing(reference), 'hiding snaps to the exact aim');
  f.api.setVisible(true);
  f.api.aimAtTarget(f.id, FIRST, { sweep: true }); time.now += 100; f.api.advancePresentation(time.now);
  f.api.setFixtureState(f.id, { spotlight: { brightness: .5 } });
  reference.api.aimAtTarget(reference.id, FIRST); reference.api.setFixtureState(reference.id, { spotlight: { brightness: .5 } });
  assert.deepEqual(drawing(f), drawing(reference), 'a channel change mid-sweep draws the exact aim');
});

test('restoreAim and the restore defaults are unchanged by the sweep option', t => {
  clock(t); const f = fixture(t);
  const snapshot = f.api.interactionDescriptor(f.id);
  f.api.aimAtTarget(f.id, SECOND, { sweep: true });
  assert.equal(f.api.restoreAim(f.id, snapshot).ok, true);
  assert.deepEqual(f.api.interactionDescriptor(f.id).target, snapshot.target);
  assert.ok(near(drawnEndpoint(f), new THREE.Vector3(...FIRST.point), 1e-5), 'restore draws at once');
  const fresh = createEquipmentVirtualFixtures(new THREE.Group(), { binding });
  t.after(() => fresh.dispose());
  const loaded = fresh.setDocument(authoredDocument(binding, Core));
  const spot = loaded.fixtures[0].state.spotlight;
  assert.equal(spot.brightness, 1); assert.equal(spot.color_temp_kelvin, 3000);
});
