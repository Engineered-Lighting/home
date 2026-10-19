// Real symbolic fixture factory with authored registration and resident Three
// objects. No WebGL frame, household assets, physical output or save is opened.
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
// Node resolves the browser's bare Three import to its exact vendored build.
// All factory, channel, geometry and spotlight code executes unchanged.
const spotlight = dataModule(replaceOne(fs.readFileSync(sourceFile('equipment-spotlight-visual.mjs'), 'utf8'),
  "from 'three'", 'from ' + JSON.stringify(threeURL)));
let actual = replaceOne(fs.readFileSync(sourceFile('equipment-virtual-fixtures.mjs'), 'utf8'),
  "from 'three'", 'from ' + JSON.stringify(threeURL));
actual = replaceOne(actual, 'import(`./equipment-spotlight-visual.mjs${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(spotlight) + ')');
for (const name of ['home-den-fixtures.js', 'home-apartment-aiming.js']) actual = replaceOne(actual,
  'import(`../' + name + '${new URL(import.meta.url).search}`)',
  'import(' + JSON.stringify(pathToFileURL(path.join(ROOT, 'app/src', name)).href) + ')');
const { createEquipmentVirtualFixtures } = await import(dataModule(actual));
const { createEquipmentSpotlightDepth } = await import(spotlight);
const { createPicking } = await import(dataModule(replaceOne(fs.readFileSync(sourceFile('picking.js'), 'utf8'),
  "from 'three'", 'from ' + JSON.stringify(threeURL))));
const clone = value => JSON.parse(JSON.stringify(value));
const binding = { content_revision: 'authored-victoria-radial-visual-r1', registration_version: 1,
  manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };

function fixture(t, { aim = true } = {}) {
  const parent = new THREE.Group(), api = createEquipmentVirtualFixtures(parent, { binding });
  const document = authoredDocument(binding, Core), loaded = api.setDocument(document);
  assert.equal(loaded.status, 'ready'); assert.equal(loaded.fixture_count, 4);
  const id = loaded.fixtures[0].fixture_id, root = api.root.children.find(node => node.userData.fixture_id === id);
  assert.ok(root); api.setSelected(id);
  const target = { point: [2, 2, .5], normal: [0, 0, 1], kind: 'authored_floor' };
  if (aim) assert.equal(api.aimAtTarget(id, target).ok, true);
  t.after(() => api.dispose());
  return { api, id, root, document };
}

function valueSnapshot(value) {
  if (value?.isTexture) return { uuid: value.uuid, version: value.version };
  if (value?.toArray) return value.toArray();
  if (Array.isArray(value)) return value.map(valueSnapshot);
  return value;
}
function visualSnapshot(node) {
  const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : [];
  return { position: node.position.toArray(), quaternion: node.quaternion.toArray(), scale: node.scale.toArray(),
    visible: node.visible, userData: clone(node.userData),
    materials: materials.map(material => ({ uuid: material.uuid, version: material.version, opacity: material.opacity,
      visible: material.visible, color: material.color?.toArray(),
      uniforms: material.uniforms && Object.fromEntries(Object.entries(material.uniforms).map(([name, uniform]) => [name, valueSnapshot(uniform.value)])) })),
    drawRange: node.geometry && { ...node.geometry.drawRange },
    bounds: node.geometry?.boundingSphere && { center: node.geometry.boundingSphere.center.toArray(), radius: node.geometry.boundingSphere.radius } };
}
function nonRadialSnapshot(root) {
  const rows = [];
  root.traverse(node => {
    for (let ancestor = node; ancestor && ancestor !== root; ancestor = ancestor.parent) if (/^radial-zone-[1-6]$/.test(ancestor.name)) return;
    rows.push({ node, geometry: node.geometry, material: node.material, snapshot: visualSnapshot(node),
      attributes: Object.entries({ ...node.geometry?.attributes, ...(node.geometry?.index ? { index: node.geometry.index } : {}) })
        .map(([name, attribute]) => ({ name, attribute, version: attribute.version,
          bytes: Buffer.from(Buffer.from(attribute.array.buffer, attribute.array.byteOffset, attribute.array.byteLength)) })) });
  });
  return rows;
}
function assertNonRadialUnchanged(rows) {
  for (const row of rows) {
    assert.equal(row.node.geometry, row.geometry, row.node.name + ' keeps its geometry');
    assert.equal(row.node.material, row.material, row.node.name + ' keeps its material');
    for (const saved of row.attributes) {
      const current = saved.name === 'index' ? row.node.geometry.index : row.node.geometry.attributes[saved.name];
      assert.equal(current, saved.attribute, row.node.name + ' keeps BufferAttribute ' + saved.name);
      assert.equal(current.version, saved.version, row.node.name + ' does not regenerate unrelated ' + saved.name);
      assert.deepEqual(Buffer.from(current.array.buffer, current.array.byteOffset, current.array.byteLength), saved.bytes,
        row.node.name + ' keeps exact receiver/geometry bytes');
    }
    assert.deepEqual(visualSnapshot(row.node), row.snapshot, row.node.name + ' keeps transforms, channel/aim metadata and uniforms');
  }
}
const withoutRadials = descriptor => {
  const result = clone(descriptor); delete result.state.radials; return result;
};

test('a radial-only modeled change updates exact target state without recalculating Spot, Down or receiver buffers', t => {
  const f = fixture(t), before = f.api.interactionDescriptor(f.id), untouched = nonRadialSnapshot(f.root);
  const result = f.api.setFixtureState(f.id, { radials: [{ number: 2, on: true, brightness: .37, color_temp_kelvin: 4200 }] });
  assert.equal(result.ok, true); assert.equal(result.physical_commands, false);
  const after = f.api.interactionDescriptor(f.id);
  assert.equal(after.state.radials[1].brightness, .37); assert.equal(after.state.radials[1].color_temp_kelvin, 4200);
  assert.deepEqual(after.state.radials.filter(zone => zone.number !== 2), before.state.radials.filter(zone => zone.number !== 2));
  assert.deepEqual(withoutRadials(after), withoutRadials(before));
  assert.deepEqual(f.root.getObjectByName('radial-zone-2').userData.channel, after.state.radials[1]);
  assertNonRadialUnchanged(untouched);
});

const receiver = f => f.root.getObjectByName('modeled-aim-receiver-glow');
const radialPatch = () => ({ radials: [{ number: 3, on: true, brightness: .21 }] });
for (const inherited of [false, true]) for (const field of ['pan_deg', 'tilt_deg', 'downward', 'spotlight']) {
  test(`${inherited ? 'inherited' : 'own'} ${field} presence retains the full update even when its value is unchanged`, t => {
    const f = fixture(t), before = f.api.interactionDescriptor(f.id), version = receiver(f).geometry.attributes.position.version;
    const extra = { [field]: clone(before.state[field]) };
    const patch = inherited ? Object.assign(Object.create(extra), radialPatch()) : { ...radialPatch(), ...extra };
    assert.equal(f.api.setFixtureState(f.id, patch).ok, true);
    const after = f.api.interactionDescriptor(f.id);
    assert.equal(after.state.radials[2].brightness, .21); assert.deepEqual(after.state[field], before.state[field]);
    assert.ok(receiver(f).geometry.attributes.position.version > version, 'presence takes the existing full receiver update');
    if (field === 'pan_deg' || field === 'tilt_deg') assert.equal(after.target, null, 'even unchanged/inherited aim fields retire the target');
    else assert.deepEqual(after.target, before.target);
  });
}

test('a non-enumerable aim property is not mistaken for an explicit radial-only patch', t => {
  const f = fixture(t), before = f.api.interactionDescriptor(f.id), version = receiver(f).geometry.attributes.position.version;
  const patch = Object.defineProperty(radialPatch(), 'pan_deg', { value: before.state.pan_deg });
  assert.deepEqual(Object.keys(patch), ['radials']); assert.equal('pan_deg' in patch, true);
  assert.equal(f.api.setFixtureState(f.id, patch).ok, true); assert.equal(f.api.interactionDescriptor(f.id).target, null);
  assert.ok(receiver(f).geometry.attributes.position.version > version);
});

test('an inherited radial array retains existing behavior through the full path', t => {
  const f = fixture(t), version = receiver(f).geometry.attributes.position.version;
  const patch = Object.create(radialPatch()); assert.deepEqual(Object.keys(patch), []);
  assert.equal(f.api.setFixtureState(f.id, patch).ok, true);
  assert.equal(f.api.interactionDescriptor(f.id).state.radials[2].brightness, .21);
  assert.ok(receiver(f).geometry.attributes.position.version > version);
});

test('invalid radial patches fail before either logical state or resident visual state changes', t => {
  const f = fixture(t), before = f.api.interactionDescriptor(f.id), allVisuals = [];
  f.root.traverse(node => allVisuals.push({ node, snapshot: visualSnapshot(node) }));
  const buffers = nonRadialSnapshot(f.root);
  for (const patch of [
    { radials: [{ number: 1, brightness: .2 }, { number: 2, brightness: 2 }] },
    { radials: [{ number: 1, color_temp_kelvin: 1000 }] },
    { radials: [{ number: 1, brightness: .2 }, { number: 1, brightness: .3 }] },
    { radials: [{ number: 0, on: true }] }, { radials: [{ number: 1, pan_deg: 0 }] },
    { radials: [], invented_channel: true },
  ]) {
    assert.equal(f.api.setFixtureState(f.id, patch).ok, false);
    assert.deepEqual(f.api.interactionDescriptor(f.id), before);
    for (const row of allVisuals) assert.deepEqual(visualSnapshot(row.node), row.snapshot, row.node.name);
    assertNonRadialUnchanged(buffers);
  }
});

test('initialization and saved full-state reconciliation retain the full Spot and Down presentation', t => {
  const f = fixture(t, { aim: false }), before = f.api.interactionDescriptor(f.id);
  assert.ok(receiver(f).geometry.attributes.position.version >= 2, 'initial full update populates the receiver');
  assert.deepEqual(f.root.getObjectByName('robotic-spotlight').userData.channel, before.state.spotlight);
  assert.deepEqual(f.root.getObjectByName('downward-zone').userData.channel, before.state.downward);
  assert.equal(f.root.getObjectByName('modeled-aim-volume').material.uniforms.uLevel.value, before.state.spotlight.brightness);
  const version = receiver(f).geometry.attributes.position.version;
  const state = clone(before.state); state.radials = state.radials.map(({ number, on, brightness, color_temp_kelvin }) => ({ number, on, brightness, color_temp_kelvin }));
  assert.equal(f.api.setFixtureState(f.id, state).ok, true);
  assert.ok(receiver(f).geometry.attributes.position.version > version, 'saved full-state reconciliation is not a radial-only shortcut');
  assert.deepEqual(f.api.interactionDescriptor(f.id), before);
});

test('aim and restore keep their full update and exact target/handle behavior after radial-only painting', t => {
  const f = fixture(t), saved = f.api.interactionDescriptor(f.id);
  assert.equal(f.api.setFixtureState(f.id, radialPatch()).ok, true);
  let version = receiver(f).geometry.attributes.position.version;
  assert.equal(f.api.aimAtTarget(f.id, { point: [1, 3, .7], normal: [0, 0, 1], kind: 'authored_floor' }).ok, true);
  assert.ok(receiver(f).geometry.attributes.position.version > version); version = receiver(f).geometry.attributes.position.version;
  assert.notDeepEqual(f.api.interactionDescriptor(f.id).target, saved.target);
  assert.equal(f.api.restoreAim(f.id, saved).ok, true);
  assert.ok(receiver(f).geometry.attributes.position.version > version);
  assert.deepEqual(f.api.interactionDescriptor(f.id), saved);
  const handle = f.root.getObjectByName('virtual-fixture-aim-handle');
  assert.equal(handle.visible, true);
  assert.deepEqual(handle.position.clone().add(f.root.position).toArray(), saved.target.point);
});

const shownLevel = (f, number = 1) => f.root.getObjectByName('radial-diffuser-sector-' + number).material.opacity / .1;
function clock(t, start = 1000) {
  const value = { now: start }; t.mock.method(performance, 'now', () => value.now); return value;
}
test('radial targets and metadata are immediate while only drawing eases with 160ms monotonic elapsed time', t => {
  const time = clock(t), f = fixture(t), untouched = nonRadialSnapshot(f.root);
  assert.equal(f.api.setFixtureState(f.id, { radials: [{ number: 1, on: true, brightness: .2 }] }).ok, true);
  assert.equal(f.api.interactionDescriptor(f.id).state.radials[0].brightness, .2);
  assert.equal(f.root.getObjectByName('radial-zone-1').userData.channel.brightness, .2);
  assert.ok(Math.abs(shownLevel(f) - .75) < 1e-12, 'setting a target does not jump the drawing');
  assert.equal(typeof f.api.advancePresentation, 'function');
  time.now += 160; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - (.2 + .55 / Math.E)) < 1e-12, 'one time constant leaves exactly exp(-1) of the visual gap');
  time.now += 640; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - (.2 + .55 * Math.exp(-5))) < 1e-12, 'a stalled frame uses all elapsed time rather than capped engine dt');
  time.now += 800; f.api.advancePresentation(time.now);
  assert.equal(f.root.getObjectByName('radial-diffuser-sector-1').material.opacity, .1 * .2);
  assert.equal(f.api.advancePresentation(time.now + 16), false);
  assertNonRadialUnchanged(untouched);
});

test('Off fades visibly to zero while its exact saved brightness and target metadata stay unchanged', t => {
  const time = clock(t), f = fixture(t), before = f.api.interactionDescriptor(f.id);
  assert.equal(f.api.setFixtureState(f.id, { radials: [{ number: 1, on: false }] }).ok, true);
  const target = f.api.interactionDescriptor(f.id);
  assert.equal(target.state.radials[0].on, false); assert.equal(target.state.radials[0].brightness, .75);
  const sector = f.root.getObjectByName('radial-diffuser-sector-1'), patch = f.root.getObjectByName('radial-receiver-patch-1');
  assert.equal(sector.visible, true); assert.equal(patch.visible, true);
  time.now += 160; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - .75 / Math.E) < 1e-12); assert.equal(sector.visible, true);
  assert.ok(Math.abs(patch.material.opacity - .06 * .75 / Math.E) < 1e-12);
  time.now += 1600; f.api.advancePresentation(time.now);
  assert.equal(sector.visible, false); assert.equal(patch.visible, false); assert.equal(sector.material.opacity, 0);
  assert.deepEqual(f.api.interactionDescriptor(f.id), target); assert.deepEqual(withoutRadials(target), withoutRadials(before));
});

test('unrelated Spot/Down updates preserve a fade and repeated retargets cannot starve its last-draw clock', t => {
  const time = clock(t), f = fixture(t);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .25 }] });
  time.now = 1080; f.api.advancePresentation(time.now); const at80 = shownLevel(f);
  time.now = 1120; f.api.setFixtureState(f.id, { spotlight: { brightness: .4 } }); assert.equal(shownLevel(f), at80);
  time.now = 1140; f.api.setFixtureState(f.id, { downward: { brightness: .3 } }); assert.equal(shownLevel(f), at80);
  time.now = 1160; f.api.advancePresentation(time.now); const at160 = .25 + .5 / Math.E;
  assert.ok(Math.abs(shownLevel(f) - at160) < 1e-12, 'unrelated full updates do not snap or reset the radial clock');
  for (const [now, brightness] of [[1180, .5], [1240, .8], [1319, 1]]) {
    time.now = now; f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness }] });
    assert.ok(Math.abs(shownLevel(f) - at160) < 1e-12, 'retargeting starts from the actual last drawing');
  }
  time.now = 1320; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - (1 + (at160 - 1) / Math.E)) < 1e-12, 'all 160ms since the last draw count despite frequent input');
  time.now = 10000; f.api.advancePresentation(time.now); assert.equal(shownLevel(f), 1);
  time.now = 100000; f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: 0, on: false }] });
  time.now += 16; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - Math.exp(-.1)) < 1e-12, 'a new transition after idle starts at the change time');
});

function globalAdapter(t, name, value) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  t.after(() => { if (previous) Object.defineProperty(globalThis, name, previous); else delete globalThis[name]; });
}
test('the current reduced-motion preference snaps both a new target and an in-flight fade', t => {
  const time = clock(t), motion = { matches: true };
  globalAdapter(t, 'matchMedia', query => { assert.equal(query, '(prefers-reduced-motion: reduce)'); return motion; });
  const f = fixture(t); f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .2 }] });
  assert.ok(Math.abs(shownLevel(f) - .2) < 1e-12); assert.equal(f.api.advancePresentation(time.now + 16), false);
  motion.matches = false; f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .8 }] });
  time.now += 80; f.api.advancePresentation(time.now); assert.ok(shownLevel(f) > .2 && shownLevel(f) < .8);
  motion.matches = true; time.now += 10; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - .8) < 1e-12); assert.equal(f.api.advancePresentation(time.now + 16), false);
});

test('layer and document hide/resume snap the drawing and dispose removes its owned visibility listener', t => {
  const time = clock(t), document = new EventTarget(); document.hidden = false;
  let added = 0, removed = 0;
  const add = document.addEventListener, remove = document.removeEventListener;
  t.mock.method(document, 'addEventListener', function (...args) { added++; return add.apply(this, args); });
  t.mock.method(document, 'removeEventListener', function (...args) { removed++; return remove.apply(this, args); });
  globalAdapter(t, 'document', document); const f = fixture(t);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .2 }] });
  time.now += 80; f.api.advancePresentation(time.now); assert.ok(shownLevel(f) > .2);
  f.api.setVisible(false); assert.ok(Math.abs(shownLevel(f) - .2) < 1e-12);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .9 }] }); assert.ok(Math.abs(shownLevel(f) - .9) < 1e-12);
  f.api.setVisible(true); assert.equal(f.api.advancePresentation(time.now + 16), false);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .3 }] });
  time.now += 80; f.api.advancePresentation(time.now); assert.ok(shownLevel(f) > .3);
  document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); assert.ok(Math.abs(shownLevel(f) - .3) < 1e-12);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .6 }] }); assert.ok(Math.abs(shownLevel(f) - .6) < 1e-12);
  document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); assert.equal(f.api.advancePresentation(time.now + 16), false);
  f.api.dispose(); assert.equal(added, 1); assert.equal(removed, 1);
  document.dispatchEvent(new Event('visibilitychange')); assert.equal(f.api.advancePresentation(time.now + 1000), false);
});

test('a rebuilt or rejected binding retires every previous drawing transition', t => {
  const time = clock(t), f = fixture(t), oldRoot = f.root;
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .2 }] });
  const next = clone(f.document); next.revision++; next.digest = 'd'.repeat(64);
  assert.equal(f.api.setDocument(next).status, 'ready'); assert.equal(oldRoot.parent, null);
  const current = f.api.root.children.find(node => node.userData.fixture_id === f.id);
  assert.notEqual(current, oldRoot);
  assert.ok(Math.abs(current.getObjectByName('radial-diffuser-sector-1').material.opacity / .1 - .2) < 1e-12);
  assert.equal(f.api.advancePresentation(time.now + 1000), false);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .9 }] });
  const wrong = clone(next); wrong.binding.frame_digest = 'e'.repeat(64);
  assert.equal(f.api.setDocument(wrong).status, 'unavailable'); assert.equal(f.api.root.children.length, 0);
  assert.equal(f.api.advancePresentation(time.now + 1000), false);
});

test('settled frames perform no radial drawing writes and invalid/backward clocks cannot move a target', t => {
  const time = clock(t), f = fixture(t); let writes = 0;
  for (let i = 1; i <= 6; i++) {
    const color = f.root.getObjectByName('radial-diffuser-sector-' + i).material.color, original = color.copy;
    t.mock.method(color, 'copy', function (...args) { writes++; return original.apply(this, args); });
  }
  for (let i = 0; i < 100; i++) assert.equal(f.api.advancePresentation(time.now + i * 16), false);
  assert.equal(writes, 0);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, brightness: .2 }] }); writes = 0;
  for (const invalid of [NaN, Infinity, -1, time.now - 1, time.now]) assert.equal(f.api.advancePresentation(invalid), false);
  assert.equal(writes, 0); assert.ok(Math.abs(shownLevel(f) - .75) < 1e-12);
});

test('radial fading leaves the canonical fixture/handle pick inventory and real ray hits unchanged', t => {
  const time = clock(t), f = fixture(t), objects = f.api.pickObjects();
  assert.equal(objects.length, 5);
  assert.ok(objects.every(node => ['fixture', 'aim_handle'].includes(node.userData.interaction_role)));
  const housing = objects.find(node => node.userData.fixture_id === f.id && node.userData.interaction_role === 'fixture');
  f.api.root.updateWorldMatrix(true, true);
  const center = housing.getWorldPosition(new THREE.Vector3());
  const camera = new THREE.PerspectiveCamera(50, 4 / 3, .01, 100);
  camera.position.copy(center).add(new THREE.Vector3(.7, -.9, .4)); camera.up.set(0, 0, 1); camera.lookAt(center); camera.updateMatrixWorld(true);
  const host = { clientWidth: 800, clientHeight: 600, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }) };
  const picking = createPicking(camera, host), before = picking.pick(objects, 400, 300).map(hit => hit.object);
  assert.ok(before.length); assert.equal(before[0], housing);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, on: false }] }); time.now += 160; f.api.advancePresentation(time.now);
  assert.deepEqual(f.api.pickObjects(), objects);
  assert.deepEqual(picking.pick(f.api.pickObjects(), 400, 300).map(hit => hit.object), before);
});

test('the real opaque-depth filter continues excluding every fixture while radial drawing fades', t => {
  const time = clock(t), f = fixture(t), scene = new THREE.Scene(); scene.add(f.api.root);
  const geometry = new THREE.BoxGeometry(1, 1, .1), material = new THREE.MeshBasicMaterial({ color: 0x808080 });
  const opaque = new THREE.Mesh(geometry, material), transparentMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: .5 });
  const transparent = new THREE.Mesh(geometry, transparentMaterial); scene.add(opaque, transparent); scene.updateMatrixWorld(true);
  let opaqueFixtureMeshes = 0; f.api.root.traverse(node => { if (node.isMesh && !node.material.transparent && node.material.depthWrite) opaqueFixtureMeshes++; });
  assert.ok(opaqueFixtureMeshes > 0, 'the exclusion covers real opaque cylinder parts as well as translucent lobes');
  const depth = createEquipmentSpotlightDepth(f.api.root), camera = new THREE.PerspectiveCamera();
  t.after(() => { depth.dispose(); geometry.dispose(); material.dispose(); transparentMaterial.dispose(); });
  let target = null; const viewport = new THREE.Vector4(0, 0, 320, 240), scissor = viewport.clone(), color = new THREE.Color();
  const rendered = [], renderer = {
    info: { render: { frame: 1 } }, autoClear: false,
    getDrawingBufferSize: value => value.set(320, 240), getRenderTarget: () => target,
    setRenderTarget: value => { target = value; }, getScissorTest: () => false, setScissorTest() {},
    getClearAlpha: () => 1, getClearColor: value => value.copy(color), setClearColor() {},
    getViewport: value => value.copy(viewport), getScissor: value => value.copy(scissor),
    setViewport() {}, setScissor() {},
    render(view) { rendered.push(view.children.map(node => ({ geometry: node.geometry, material: node.material }))); this.info.render.frame++; },
  };
  assert.ok(depth.capture(renderer, scene, camera));
  f.api.setFixtureState(f.id, { radials: [{ number: 1, on: false }] }); time.now += 160; f.api.advancePresentation(time.now);
  renderer.info.render.frame++; assert.ok(depth.capture(renderer, scene, camera));
  assert.equal(rendered.length, 2);
  for (const pass of rendered) { assert.equal(pass.length, 1); assert.equal(pass[0].geometry, geometry); assert.equal(pass[0].material, material); }
  assert.equal(depth.readback().opaque_mesh_count, 1); assert.equal(depth.readback().passes, 2);
});

function temperatureFixture(t) {
  const preference = { current: { matches: false } };
  globalAdapter(t, 'matchMedia', () => preference.current);
  const f = fixture(t), motion = preference.current;
  preference.current = { matches: true }; const reference = fixture(t);
  return { f, motion, reference };
}
function assertShownTemperatures(f, reference, kelvins) {
  assert.equal(reference.api.setFixtureState(reference.id, { radials: kelvins.map((color_temp_kelvin, i) => ({ number: i + 1, color_temp_kelvin })) }).ok, true);
  for (let i = 0; i < kelvins.length; i++) for (const name of ['radial-diffuser-sector-', 'radial-receiver-patch-']) {
    const actual = f.root.getObjectByName(name + (i + 1)).material.color.toArray();
    const expected = reference.root.getObjectByName(name + (i + 1)).material.color.toArray();
    assert.ok(actual.every((channel, index) => Math.abs(channel - expected[index]) < 1e-12),
      name + (i + 1) + ' shows the analytical Kelvin through the real producer color conversion');
  }
}
test('each radial CCT target is immediate while diffuser and pool colors follow the analytical 160ms curve', t => {
  const time = clock(t), { f, reference } = temperatureFixture(t), untouched = nonRadialSnapshot(f.root);
  const before = f.root.getObjectByName('radial-diffuser-sector-1').material.color.toArray();
  f.api.setFixtureState(f.id, { radials: [{ number: 1, color_temp_kelvin: 5000 }, { number: 2, color_temp_kelvin: 2200 }] });
  assert.deepEqual(f.api.interactionDescriptor(f.id).state.radials.map(zone => zone.color_temp_kelvin), [5000, 2200, 3000, 3000, 3000, 3000]);
  assert.equal(f.root.getObjectByName('radial-zone-1').userData.channel.color_temp_kelvin, 5000);
  assert.deepEqual(f.root.getObjectByName('radial-diffuser-sector-1').material.color.toArray(), before, 'target temperature does not jump the drawing');
  time.now += 160; f.api.advancePresentation(time.now);
  assertShownTemperatures(f, reference, [5000 - 2000 / Math.E, 2200 + 800 / Math.E, 3000, 3000, 3000, 3000]);
  time.now += 640; f.api.advancePresentation(time.now);
  assertShownTemperatures(f, reference, [5000 - 2000 * Math.exp(-5), 2200 + 800 * Math.exp(-5), 3000, 3000, 3000, 3000]);
  time.now += 800; f.api.advancePresentation(time.now);
  assertShownTemperatures(f, reference, [5000, 2200, 3000, 3000, 3000, 3000]); assert.equal(f.api.advancePresentation(time.now + 16), false);
  assertNonRadialUnchanged(untouched);
});

test('CCT easing survives unrelated Spot/Down updates and retargets from the last drawing without losing elapsed time', t => {
  const time = clock(t), { f, reference } = temperatureFixture(t);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, color_temp_kelvin: 5000 }] });
  time.now = 1080; f.api.advancePresentation(time.now);
  const at80 = f.root.getObjectByName('radial-diffuser-sector-1').material.color.toArray();
  time.now = 1120; f.api.setFixtureState(f.id, { spotlight: { brightness: .4 } });
  time.now = 1140; f.api.setFixtureState(f.id, { downward: { brightness: .3 } });
  assert.deepEqual(f.root.getObjectByName('radial-diffuser-sector-1').material.color.toArray(), at80);
  time.now = 1160; f.api.advancePresentation(time.now); const at160 = 5000 - 2000 / Math.E;
  assertShownTemperatures(f, reference, [at160, 3000, 3000, 3000, 3000, 3000]);
  const drawn = f.root.getObjectByName('radial-diffuser-sector-1').material.color.toArray();
  for (const [now, color_temp_kelvin] of [[1180, 2200], [1240, 4200], [1319, 3000]]) {
    time.now = now; f.api.setFixtureState(f.id, { radials: [{ number: 1, color_temp_kelvin }] });
    assert.deepEqual(f.root.getObjectByName('radial-diffuser-sector-1').material.color.toArray(), drawn);
    assert.equal(f.api.interactionDescriptor(f.id).state.radials[0].color_temp_kelvin, color_temp_kelvin);
  }
  time.now = 1320; f.api.advancePresentation(time.now);
  assertShownTemperatures(f, reference, [3000 + (at160 - 3000) / Math.E, 3000, 3000, 3000, 3000, 3000]);
});

test('Off fades CCT alongside level and hidden/resume or current reduced motion snap each private color', t => {
  const time = clock(t), document = new EventTarget(); document.hidden = false; globalAdapter(t, 'document', document);
  const { f, motion, reference } = temperatureFixture(t);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, on: false, color_temp_kelvin: 5000 }] });
  time.now += 160; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - .75 / Math.E) < 1e-12);
  assert.equal(f.root.getObjectByName('radial-diffuser-sector-1').visible, true);
  assertShownTemperatures(f, reference, [5000 - 2000 / Math.E, 3000, 3000, 3000, 3000, 3000]);
  document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(shownLevel(f), 0); assertShownTemperatures(f, reference, [5000, 3000, 3000, 3000, 3000, 3000]);
  document.hidden = false; document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(f.api.advancePresentation(time.now + 16), false);
  f.api.setFixtureState(f.id, { radials: [{ number: 1, on: true, brightness: .5, color_temp_kelvin: 2200 }] });
  motion.matches = true; time.now += 16; f.api.advancePresentation(time.now);
  assert.ok(Math.abs(shownLevel(f) - .5) < 1e-12); assertShownTemperatures(f, reference, [2200, 3000, 3000, 3000, 3000, 3000]);
  motion.matches = false; f.api.setFixtureState(f.id, { radials: [{ number: 1, color_temp_kelvin: 4200 }] });
  f.api.setVisible(false); f.api.setVisible(true);
  assertShownTemperatures(f, reference, [4200, 3000, 3000, 3000, 3000, 3000]);
  assert.equal(f.api.advancePresentation(time.now + 16), false);
});
