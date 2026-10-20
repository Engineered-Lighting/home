// Tier-0 Lit (APT-V01) on the real symbolic fixture factory and a resident
// mesh, with no WebGL: candela from SPEC's assumed lumens by beam angle,
// crisp spot and soft down shadows (cached per light), unshadowed radial
// lobes, reversible mesh materials and geometry, tone mapping confined to the
// lit mesh, disclosure, the declared reduced quality, and the L5 bounce bake
// (its CPU twin in-thread) landing in a built-in lightMap with modeled lux.
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
const spotlight = dataModule(replaceOne(fs.readFileSync(sourceFile('equipment-spotlight-visual.mjs'), 'utf8'), "from 'three'", 'from ' + JSON.stringify(threeURL)));
let fixturesSource = replaceOne(fs.readFileSync(sourceFile('equipment-virtual-fixtures.mjs'), 'utf8'), "from 'three'", 'from ' + JSON.stringify(threeURL));
fixturesSource = replaceOne(fixturesSource, 'import(`./equipment-spotlight-visual.mjs${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(spotlight) + ')');
for (const name of ['home-den-fixtures.js', 'home-apartment-aiming.js']) fixturesSource = replaceOne(fixturesSource,
  'import(`../' + name + '${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(pathToFileURL(path.join(ROOT, 'app/src', name)).href) + ')');
const { createEquipmentVirtualFixtures } = await import(dataModule(fixturesSource));
const Model = await import(pathToFileURL(sourceFile('lit-model.mjs')).href);
let litSource = replaceOne(fs.readFileSync(sourceFile('lit-view.mjs'), 'utf8'), "from 'three'", 'from ' + JSON.stringify(threeURL));
litSource = replaceOne(litSource, 'import(`./lit-model.mjs${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(pathToFileURL(sourceFile('lit-model.mjs')).href) + ')');
litSource = replaceOne(litSource, 'import(`./lit-bvh-bake.mjs${new URL(import.meta.url).search}`)', 'import(' + JSON.stringify(pathToFileURL(sourceFile('lit-bvh-bake.mjs')).href) + ')');
const Bake = await import(pathToFileURL(sourceFile('lit-bvh-bake.mjs')).href);
const { roomBox } = await import('./fixtures/lit-bake-scenes.mjs');
const Lit = await import(dataModule(litSource));
const EXPOSURE = .038;
// Every sync lands 10 s after the last, so the 160 ms ease has settled; the
// easing itself is proved below and in test-lit-model.mjs on a controlled clock.
const settledClock = () => { let t = 0; return () => (t += 10000); };
const SPEC_LUMENS = { spot: 450, down: 350, zone: 120 };
const binding = { content_revision: 'authored-victoria-lit-r1', registration_version: 1,
  manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };

function world(t) {
  const scene = new THREE.Scene(), root = new THREE.Group(); root.rotation.x = -Math.PI / 2; scene.add(root);
  const fixtures = createEquipmentVirtualFixtures(root, { binding });
  assert.equal(fixtures.setDocument(authoredDocument(binding, Core)).status, 'ready');
  const hook = shader => { shader.fragmentShader = shader.fragmentShader.replace('#include <common>', '#include <common>\n// display-hook'); };
  const display = new THREE.MeshBasicMaterial({ map: new THREE.Texture(), toneMapped: false });
  display.onBeforeCompile = hook; display.customProgramCacheKey = () => 'home-scan-display-floor-0-1';
  const displayGeometry = new THREE.PlaneGeometry(8, 8);
  const mesh = new THREE.Group(), face = new THREE.Mesh(displayGeometry, display); mesh.add(face); root.add(mesh);
  const renderer = { shadowMap: { enabled: false, type: THREE.BasicShadowMap, autoUpdate: true, needsUpdate: false }, toneMapping: THREE.NoToneMapping };
  let current = mesh;
  const lit = Lit.createLitView({ scene, renderer, getMesh: () => current, getFixtureRoots: () => fixtures.root.children, spotFwhmDeg: 20, now: settledClock() });
  t.after(() => { lit.disable(); fixtures.dispose(); });
  return { scene, root, fixtures, mesh, face, display, displayGeometry, hook, renderer, lit, setMesh: next => { current = next; } };
}
const lights = w => w.lit.group.children.filter(node => node.isSpotLight);

test('the light model is renderer-independent and pure', () => {
  const source = fs.readFileSync(sourceFile('lit-model.mjs'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  assert.doesNotMatch(source, /\bimport\b|three|THREE|document|window/, 'no imports, no three.js, no DOM');
  assert.deepEqual(Model.zoneDirection(0).map(v => +v.toFixed(5)), [.98503, 0, -.17238], 'outward and 0.35 down per 2 out, as in the prototype');
  assert.ok(Math.abs(Math.hypot(...Model.zoneDirection(137)) - 1) < 1e-12);
  assert.equal(Model.channelLevel({ on: true, brightness: .4 }), .4); assert.equal(Model.channelLevel({ on: false, brightness: 1 }), 0);
  assert.deepEqual(Model.kelvinRgb(6600), [1, 1, 1]); assert.ok(Model.kelvinRgb(2700)[2] < Model.kelvinRgb(2700)[0], 'warm white');
  const lights = Model.fixtureLights({ spot: { origin: [0, 0, 2], direction: [0, 0, -1], channel: { on: true, brightness: 1, color_temp_kelvin: 3000 } },
    down: { origin: [0, 0, 1.9], channel: { on: true, brightness: .5 } }, center: [0, 0, 2],
    zones: [1, 2, 3, 4, 5, 6].map(number => ({ number, azimuth_deg: number * 60, channel: { on: true, brightness: 1 } })) }, { spotFwhmDeg: 20 });
  assert.deepEqual(lights.map(light => light.kind), ['spot', 'down', 'zone', 'zone', 'zone', 'zone', 'zone', 'zone']);
  assert.ok(lights.every(light => Math.abs(light.intensity - light.candela * Model.LIT_EXPOSURE) < 1e-12));
  assert.deepEqual(lights.map(light => light.shadow), ['crisp', 'soft', null, null, null, null, null, null]);
  assert.ok(Math.abs(lights[1].candela - Model.candelaFromLumens(350, 1) * Model.litDimmingFraction(.5)) < 1e-12);
});

test('candela comes from the assumed lumens spread over the beam angle', () => {
  assert.deepEqual({ ...Lit.LIT_ASSUMED_LUMENS }, SPEC_LUMENS, 'SPEC Lit view ratings, unchanged');
  const theta = .5;
  assert.ok(Math.abs(Lit.candelaFromLumens(450, theta) - 450 / (2 * Math.PI * (1 - Math.cos(theta)))) < 1e-9);
  assert.ok(Lit.candelaFromLumens(450, .2) > Lit.candelaFromLumens(450, .6), 'a narrow beam is brighter than a wide wash of the same output');
  assert.equal(Lit.candelaFromLumens(450, 0), 0);
  assert.equal(Lit.LIT_EXPOSURE, .038, 'the fixed night exposure');
});

test('the disclosure says simulated, not photometric, assumed output, and never measured', () => {
  assert.equal(Lit.LIT_DISCLOSURE, 'Simulated · not photometric · assumed output');
  assert.match(Lit.LIT_DISCLOSURE_DETAIL, /Assumed output, not measured: spot 450 lm, down 350 lm, each radial zone 120 lm/);
  assert.doesNotMatch(Lit.LIT_DISCLOSURE + Lit.LIT_DISCLOSURE_DETAIL, /\bmeasured (?:output|flux|lumens)\b|photometric output/i);
});

test('enable relights the mesh from the four fixtures with crisp spot, soft down and unshadowed zone lights', t => {
  const w = world(t), state = w.lit.enable();
  assert.equal(state.active, true); assert.equal(state.measured, false);
  assert.equal(state.lights, 4 * 8, 'per fixture: one spot, one down, six radial lobes');
  assert.equal(state.shadowLights, 8, 'only spot and down cast shadows');
  const all = lights(w), spots = all.filter(light => light.userData.lit_source === 'spot');
  const downs = all.filter(light => light.userData.lit_source === 'down'), zones = all.filter(light => light.userData.lit_source === 'zone');
  assert.equal(spots.length, 4); assert.equal(downs.length, 4); assert.equal(zones.length, 24);
  for (const light of spots) {
    const theta = 20 * .8 * Math.PI / 180;
    assert.ok(Math.abs(light.angle - theta) < 1e-12);
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(450, theta) * 1 * EXPOSURE) < 1e-9, 'spot at its restore-default brightness 1');
    assert.equal(light.castShadow, true); assert.equal(light.shadow.radius, 2.5, 'crisp');
    assert.equal(light.shadow.mapSize.x, 2048, 'spot shadow maps are 2048'); assert.equal(light.decay, 2);
    assert.equal(light.shadow.autoUpdate, false, 'cached: re-rendered only when this light moves');
  }
  for (const light of downs) {
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(350, 1) * Model.litDimmingFraction(.75) * EXPOSURE) < 1e-9);
    assert.equal(light.castShadow, true); assert.equal(light.shadow.radius, 6, 'soft');
    assert.equal(light.shadow.mapSize.x, 1024, 'down shadow maps are 1024'); assert.equal(light.shadow.autoUpdate, false);
  }
  assert.deepEqual({ ...state.shadowMapSizes }, { crisp: 2048, soft: 1024 });
  assert.equal(all.filter(light => light.userData.lit_source === 'bounce').length, 0, "#319's instant-radiosity bounce lights are retired");
  assert.equal(state.bounceLimit, undefined, 'and so is their leak line');
  for (const light of zones) {
    assert.equal(light.castShadow, false, 'each radial zone is its own unshadowed lobe');
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(120, .95) * Model.litDimmingFraction(.75) * EXPOSURE) < 1e-9);
  }
  assert.ok(all.every(light => light.userData.measured === false));
  assert.equal(w.renderer.shadowMap.enabled, true); assert.equal(w.renderer.shadowMap.type, THREE.PCFShadowMap);
  assert.equal(w.renderer.shadowMap.autoUpdate, false, 'the static scene re-renders shadows only on change');
  assert.equal(w.renderer.toneMapping, THREE.NoToneMapping, 'renderer tone mapping is untouched: cloud, splat, orbs and overlays keep their look');
});

test('the spot light follows the fixture exact aim, and its colour follows Kelvin', t => {
  const w = world(t); w.lit.enable();
  const id = w.fixtures.readback().fixtures[0].fixture_id;
  assert.equal(w.fixtures.aimAtTarget(id, { point: [1.5, 2.5, 0], normal: [0, 0, 1], kind: 'authored_floor' }).ok, true);
  w.renderer.shadowMap.needsUpdate = false;
  for (const light of lights(w)) if (light.castShadow) light.shadow.needsUpdate = false;
  assert.equal(w.lit.sync(), true, 'an aim change is picked up');
  assert.equal(w.renderer.shadowMap.needsUpdate, true, 'and re-renders the shadow maps once');
  const spot = lights(w).find(light => light.userData.lit_source === 'spot' && light.userData.fixture_id === id);
  assert.deepEqual(lights(w).filter(light => light.castShadow && light.shadow.needsUpdate), [spot], 'only the moved spot re-renders its cached map');
  const target = w.root.localToWorld(new THREE.Vector3(1.5, 2.5, 0));
  const toTarget = target.clone().sub(spot.position).normalize(), aim = spot.target.position.clone().sub(spot.position).normalize();
  assert.ok(toTarget.dot(aim) > .99999, 'the light points at the set aim');
  w.renderer.shadowMap.needsUpdate = false;
  assert.equal(w.lit.sync(), false, 'nothing changed, nothing re-renders');
  assert.equal(w.renderer.shadowMap.needsUpdate, false);
  w.fixtures.setFixtureState(id, { spotlight: { color_temp_kelvin: 2700, brightness: .5 } }).ok || assert.fail('patch accepted'); w.lit.sync();
  const warm = Lit.kelvinColor(2700);
  assert.ok(spot.color.equals(warm)); assert.ok(Math.abs(spot.intensity - Lit.candelaFromLumens(450, 20 * .8 * Math.PI / 180) * Model.litDimmingFraction(.5) * EXPOSURE) < 1e-9);
  w.fixtures.setFixtureState(id, { spotlight: { on: false } }); w.lit.sync();
  assert.equal(spot.intensity, 0, 'an off channel adds no light');
});

test('mesh materials are lit copies that keep the display hooks and confine tone mapping, and come back exactly', t => {
  const w = world(t); w.lit.enable();
  const litMaterial = w.face.material, displayGeometry = w.displayGeometry;
  assert.ok(litMaterial.isMeshStandardMaterial); assert.equal(litMaterial.map, w.display.map, 'the scan colours stand in for albedo');
  assert.ok(litMaterial.lightMap?.isDataTexture && litMaterial.lightMap.channel === 1 && litMaterial.lightMap.type === THREE.HalfFloatType,
    'the bounce is a built-in half-float lightMap on uv1, no custom shader');
  assert.notEqual(w.face.geometry, w.displayGeometry, 'Lit draws its own non-indexed copy');
  assert.equal(w.face.geometry.index, null);
  assert.equal(w.face.geometry.attributes.uv1.count, w.face.geometry.attributes.position.count, 'one lightmap cell corner per vertex');
  assert.equal(w.face.castShadow, true); assert.equal(w.face.receiveShadow, true);
  assert.equal(w.face.customDepthMaterial, undefined, 'default depth: the camera cutaway never changes world shadowing');
  assert.equal(litMaterial.onBeforeCompile, THREE.Material.prototype.onBeforeCompile, 'no custom shader: built-in material only, WebGPU-portable');
  assert.equal(litMaterial.toneMapped, true); assert.equal(Object.hasOwn(litMaterial, 'customProgramCacheKey'), false);
  const state = w.lit.disable();
  assert.equal(state.active, false);
  assert.equal(w.face.material, w.display, 'the exact display material returns');
  assert.equal(w.face.geometry, displayGeometry, 'and the exact display geometry');
  assert.equal(w.face.castShadow, false); assert.equal(w.face.receiveShadow, false);
  assert.equal(w.renderer.shadowMap.enabled, false); assert.equal(w.renderer.shadowMap.type, THREE.BasicShadowMap); assert.equal(w.renderer.shadowMap.autoUpdate, true);
  assert.equal(w.lit.group.parent, null);
  assert.equal(lights(w).filter(light => light.userData.lit_source !== 'bounce').length, 0);
  assert.ok(lights(w).every(light => light.intensity === 0), 'the detached group keeps only its eight dark bounce lights');
  assert.equal(Object.hasOwn(w.scene, 'onBeforeRender') ? w.scene.onBeforeRender : THREE.Object3D.prototype.onBeforeRender, THREE.Object3D.prototype.onBeforeRender);
});

test('orbs, beams and the fixture readback are untouched by Lit', t => {
  const w = world(t);
  const snapshot = () => { const rows = []; w.fixtures.root.traverse(node => { if (node.material) rows.push([node.name, node.material.uuid, node.material.toneMapped, node.visible]); }); return rows; };
  const before = snapshot(), readback = JSON.stringify({ ...w.fixtures.readback(), depth: null });
  w.lit.enable(); w.lit.sync();
  assert.deepEqual(snapshot(), before, 'every fixture material, orb and halo included, is the same object with toneMapped false kept');
  assert.equal(JSON.stringify({ ...w.fixtures.readback(), depth: null }), readback, 'virtualFixturesReadback is unchanged');
  assert.ok(before.filter(row => /orb/.test(row[0])).every(row => row[2] === false), 'orb layers are never tone-mapped away');
});

test('a sustained frame rate under 30 fps declares reduced quality: half-size shadows and no radial lobes', t => {
  const w = world(t); w.lit.enable();
  for (let i = 0; i < 3; i++) assert.equal(w.lit.observeFrameRate(24).quality, 'full');
  assert.equal(w.lit.observeFrameRate(45).quality, 'full', 'a recovered sample resets the count');
  for (let i = 0; i < 3; i++) w.lit.observeFrameRate(20);
  const state = w.lit.observeFrameRate(20);
  assert.equal(state.quality, 'reduced');
  assert.ok(lights(w).filter(light => light.userData.lit_source === 'spot').every(light => light.shadow.mapSize.x === 1024), 'spot maps halve to 1024');
  assert.ok(lights(w).filter(light => light.userData.lit_source === 'down').every(light => light.shadow.mapSize.x === 512), 'down maps halve to 512');
  assert.ok(lights(w).filter(light => light.castShadow).every(light => light.shadow.needsUpdate), 'and each resized map re-renders once');
  assert.ok(lights(w).filter(light => light.userData.lit_source === 'zone').every(light => light.intensity === 0));
  assert.ok(lights(w).filter(light => light.userData.lit_source === 'spot').every(light => light.intensity > 0));
});

test('a scan without normals gets them for Lit only', t => {
  const w = world(t), bare = new THREE.BufferGeometry();
  bare.setAttribute('position', w.face.geometry.attributes.position.clone()); bare.setIndex(w.face.geometry.index.clone());
  const node = new THREE.Mesh(bare, new THREE.MeshBasicMaterial()); w.mesh.add(node);
  w.lit.enable();
  assert.ok(node.geometry.attributes.normal, 'lighting needs normals, so Lit derives them on its own copy');
  assert.equal(bare.attributes.normal, undefined, 'the display geometry never gains them');
  w.lit.disable();
  assert.equal(node.geometry, bare); assert.equal(bare.attributes.normal, undefined);
  assert.ok(w.face.geometry.attributes.normal, 'normals the scan already had are kept');
});

test('a replaced mesh is relit and the old one restored', t => {
  const w = world(t); w.lit.enable();
  const other = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial()), original = other.material;
  w.setMesh(other); w.lit.sync();
  assert.equal(w.face.material, w.display); assert.equal(w.face.geometry, w.displayGeometry); assert.ok(other.material.isMeshStandardMaterial);
  w.lit.disable(); assert.equal(other.material, original);
});

// A closed room around the four authored fixtures (root-local, Z up).
function roomWorld(t, extra = () => ({})) {
  const w = world(t), triangles = [], albedo = [];
  roomBox(triangles, albedo, [-6, -6, 0], [10, 10, 3.2], { step: 1 });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(triangles), 3));
  const room = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0xb0b0b0 }));
  const roomMesh = new THREE.Group(); roomMesh.add(room); w.root.add(roomMesh); w.root.updateMatrixWorld(true);
  w.lit.disable();
  const messages = [];
  const lit = Lit.createLitView({ scene: w.scene, renderer: w.renderer, getMesh: () => roomMesh, getFixtureRoots: () => w.fixtures.root.children,
    spotFwhmDeg: 20, now: settledClock(),
    createBakeEndpoint: () => {
      const endpoint = Bake.createLocalBakeEndpoint({ gpu: null, yieldTask: () => new Promise(resolve => setImmediate(resolve)) });
      const post = endpoint.postMessage.bind(endpoint);
      endpoint.postMessage = (data, transfer) => { messages.push(data); post(data, transfer); };
      return endpoint;
    }, ...extra(w) });
  t.after(() => lit.disable());
  return { ...w, lit, room, roomMesh, messages };
}
async function until(lit, test, label) {
  for (let i = 0; i < 4000; i++) { lit.sync(); if (test(lit.state())) return lit.state(); await new Promise(resolve => setImmediate(resolve)); }
  assert.fail('timed out waiting for ' + label + ': ' + JSON.stringify(lit.state().bounce));
}

test('the bounce is traced through a BVH over the scan mesh into the lightMap, with modeled lux at named aims', async t => {
  const changes = [];
  const w = roomWorld(t, w => ({ getNamedAims: () => [{ id: 'desk', label: 'Desk', point: [1, 1, 0], normal: [0, 0, 1], frame: w.root }],
    onStateChange: state => changes.push(state.bounce.status) }));
  const lit = w.lit;
  const first = lit.enable();
  assert.equal(first.bounce.method, 'compute-bvh-one-bounce'); assert.equal(first.bounce.status, 'building');
  const ready = await until(lit, state => state.bounce.status === 'ready' && state.lux.length === 1, 'the first bake and lux');
  assert.equal(ready.bounce.backend, 'cpu', 'no WebGPU adapter here: the CPU twin');
  assert.ok(ready.bounce.receivers > 100);
  assert.ok(ready.bounce.lastBake.spots === 4 && ready.bounce.lastBake.staticMs >= 0, 'four spot layers and the static layers');
  const node = w.roomMesh.children[0], map = node.material.lightMap, half = Bake.halfTableF32();
  assert.ok(map.image.data.some(value => half[value] > 0 && value !== 0x3c00), 'bounce irradiance landed in the lightmap');
  assert.ok(changes.includes('ready'), 'Lit reports the bake so the engine can emit lit');
  const [row] = ready.lux;
  assert.equal(row.id, 'desk'); assert.equal(row.modeled, true); assert.equal(row.measured, false);
  assert.ok(row.lux > 0 && row.direct >= 0 && row.bounce > 0, 'direct plus bounce, in lux');
  assert.equal(ready.luxLabel, 'Modeled lux');
  assert.equal(ready.measured, false); assert.equal(ready.disclosure, 'Simulated · not photometric · assumed output');
  lit.disable();
  assert.equal(node.geometry.index === null && !node.geometry.attributes.uv1, true, 'the display geometry is back, without Lit\'s uv1');
});

test('a slider only re-weights the bake; an aim re-bakes that spot, after the Set sweep lands', async t => {
  let drawing = false, clock = 0;
  const w = roomWorld(t, () => ({ isPresentationActive: () => drawing, now: () => (clock += 16) }));
  w.lit.enable();
  await until(w.lit, state => state.bounce.status === 'ready', 'the first bake');
  const posts = () => w.messages.filter(message => message.type === 'lights');
  const before = posts().length, id = w.fixtures.readback().fixtures[0].fixture_id;
  // The real fixtures say when a Set sweep is drawing; the engine passes this to Lit.
  assert.equal(w.fixtures.presentationActive(), false);
  assert.equal(w.fixtures.aimAtTarget(id, { point: [2, 2, 0], normal: [0, 0, 1], kind: 'authored_floor' }, { sweep: true }).ok, true);
  assert.equal(w.fixtures.presentationActive(), true, 'the 650 ms sweep is drawing');
  w.fixtures.advancePresentation(performance.now() + 1000);
  assert.equal(w.fixtures.presentationActive(), false, 'and lands');
  await until(w.lit, state => state.bounce.status === 'ready', 'the swept aim re-bake');
  const settled = posts().length;
  w.fixtures.setFixtureState(id, { downward: { brightness: .3 } });
  await until(w.lit, state => state.bounce.status === 'ready' && posts().length > settled, 'a re-weight');
  const weighted = posts().at(-1), spotKey = Object.keys(weighted.weights).find(key => key.startsWith(id + ':spot'));
  assert.deepEqual(posts().at(-2).lights, weighted.lights, 'a level change sends the same placements: nothing re-traces');
  // Set with the sweep: the fixture is drawing, so the re-bake waits.
  drawing = true;
  assert.equal(w.fixtures.aimAtTarget(id, { point: [1.5, 2.5, 0], normal: [0, 0, 1], kind: 'authored_floor' }).ok, true);
  const waiting = posts().length;
  for (let i = 0; i < 20; i++) w.lit.sync();
  assert.equal(posts().length, waiting, 'no re-bake while the 650 ms sweep draws');
  drawing = false; w.lit.sync();
  assert.equal(posts().length, waiting + 1, 'one re-bake once it lands');
  const moved = posts().at(-1);
  assert.notDeepEqual(moved.lights.find(light => light.key === spotKey).direction, weighted.lights.find(light => light.key === spotKey).direction);
  const final = await until(w.lit, state => state.bounce.status === 'ready', 'the spot re-bake');
  assert.equal(final.bounce.lastBake.spots, 1, 'only the aimed spot re-baked'); assert.equal(final.bounce.lastBake.staticMs, null);
  // A sweep that never ends does not hold the bounce back forever.
  drawing = true;
  w.fixtures.aimAtTarget(id, { point: [2.5, 1.5, 0], normal: [0, 0, 1], kind: 'authored_floor' });
  const held = posts().length;
  for (let i = 0; i < 200 && posts().length === held; i++) w.lit.sync();
  assert.equal(posts().length, held + 1, 'the re-bake goes ahead after 1.5 s');
});

test('Lit lights ease toward a new level on the 160 ms clock; the set level and the aim move at once', t => {
  const w = world(t);
  let clock = 0;
  w.lit.disable();
  const lit = Lit.createLitView({ scene: w.scene, renderer: w.renderer, getMesh: () => w.mesh, getFixtureRoots: () => w.fixtures.root.children,
    spotFwhmDeg: 20, now: () => clock });
  t.after(() => lit.disable());
  lit.enable();
  const id = w.fixtures.readback().fixtures[0].fixture_id;
  const spot = lit.group.children.find(light => light.isSpotLight && light.userData.lit_source === 'spot' && light.userData.fixture_id === id);
  const peak = Lit.candelaFromLumens(450, 20 * .8 * Math.PI / 180) * EXPOSURE;
  assert.ok(Math.abs(spot.intensity - peak) < 1e-9, 'enable shows the set level at once');
  assert.equal(lit.state().easing, false);
  w.fixtures.setFixtureState(id, { spotlight: { brightness: .2, color_temp_kelvin: 2700 } });
  w.renderer.shadowMap.needsUpdate = false;
  clock += 16; assert.equal(lit.sync(), true);
  const target = peak * Model.litDimmingFraction(.2);
  assert.ok(spot.intensity < peak && spot.intensity > target, 'one frame in, the light is between its old and new output');
  assert.equal(lit.state().easing, true);
  assert.equal(w.renderer.shadowMap.needsUpdate, false, 'an eased level re-renders the lights, never the shadow maps');
  assert.equal(w.fixtures.readback().fixtures[0].state.spotlight.brightness, .2, 'the set level updates at once');
  clock += 144; lit.sync();
  const level = 1 + (.2 - 1) * (1 - Math.exp(-1));
  assert.ok(Math.abs(spot.intensity - peak * Model.litDimmingFraction(level)) < 1e-9, 'one 160 ms time constant covers 63 % of the level change');
  for (let i = 0; i < 120 && lit.state().easing; i++) { clock += 16; lit.sync(); }
  assert.equal(lit.state().easing, false, 'settled');
  assert.ok(clock <= 160 + 16 * 80, 'settled within about 1.4 s for a 0.8 level step');
  assert.ok(Math.abs(spot.intensity - target) < 1e-9, 'and exactly on the dimmed set level');
  assert.ok(spot.color.equals(Lit.kelvinColor(2700)), 'Kelvin eases to the same constant-luminance colour');
  assert.equal(lit.sync(), false, 'nothing moving, nothing changes');
  assert.equal(w.fixtures.aimAtTarget(id, { point: [1.5, 2.5, 0], normal: [0, 0, 1], kind: 'authored_floor' }).ok, true);
  w.renderer.shadowMap.needsUpdate = false; clock += 16; lit.sync();
  const aim = spot.target.position.clone().sub(spot.position).normalize();
  const toTarget = w.root.localToWorld(new THREE.Vector3(1.5, 2.5, 0)).sub(spot.position).normalize();
  assert.ok(toTarget.dot(aim) > .99999, 'the aim follows the exact metadata, not an eased drawing');
  assert.equal(w.renderer.shadowMap.needsUpdate, true, 'a moved light re-renders the shadow maps');
});

test('reduced motion snaps Lit lights to their set level', t => {
  const w = world(t);
  w.lit.disable();
  const lit = Lit.createLitView({ scene: w.scene, renderer: w.renderer, getMesh: () => w.mesh, getFixtureRoots: () => w.fixtures.root.children,
    spotFwhmDeg: 20, now: () => 0, reducedMotion: { matches: true } });
  t.after(() => lit.disable());
  lit.enable();
  const id = w.fixtures.readback().fixtures[0].fixture_id;
  w.fixtures.setFixtureState(id, { spotlight: { brightness: .3 } }); lit.sync();
  const spot = lit.group.children.find(light => light.isSpotLight && light.userData.lit_source === 'spot' && light.userData.fixture_id === id);
  assert.ok(Math.abs(spot.intensity - Lit.candelaFromLumens(450, 20 * .8 * Math.PI / 180) * Model.litDimmingFraction(.3) * EXPOSURE) < 1e-9);
  assert.equal(lit.state().easing, false);
});

test('litState records the assumed lumen set with its source, under the unchanged disclosure', t => {
  const w = world(t), state = w.lit.enable();
  assert.deepEqual({ ...state.assumedLumens }, SPEC_LUMENS);
  assert.equal(state.assumedLumens.source.reference, 'docs/design/apartment-unified/SPEC.md › Lit view');
  assert.equal(state.assumedLumens.source.status, 'assumed'); assert.equal(state.assumedLumens.source.measured, false);
  assert.match(state.assumedLumens.source.supersedes, /max_flux_lm/);
  assert.equal(state.assumptionSource, 'SPEC Lit view assumed lumen ratings');
  assert.equal(state.measured, false); assert.equal(state.disclosure, 'Simulated · not photometric · assumed output');
  assert.equal(state.easeTauMs, 160); assert.equal(state.dimmingCurve, 'cie-lightness-inverse');
  const color = Lit.kelvinColor(2700);
  assert.deepEqual([color.r, color.g, color.b], Model.cctToLinearRgb(2700), 'Lit light colour is the constant-luminance CCT in the linear working space');
});
