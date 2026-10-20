// Tier-0 Lit (APT-V01) on the real symbolic fixture factory and a resident
// mesh, with no WebGL: candela from SPEC's assumed lumens by beam angle,
// crisp spot and soft down shadows, unshadowed radial lobes, reversible mesh
// materials, tone mapping confined to the lit mesh, disclosure, and the
// declared reduced quality.
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
  const mesh = new THREE.Group(), face = new THREE.Mesh(new THREE.PlaneGeometry(8, 8), display); mesh.add(face); root.add(mesh);
  const renderer = { shadowMap: { enabled: false, type: THREE.BasicShadowMap, autoUpdate: true, needsUpdate: false }, toneMapping: THREE.NoToneMapping };
  let current = mesh;
  const lit = Lit.createLitView({ scene, renderer, getMesh: () => current, getFixtureRoots: () => fixtures.root.children, spotFwhmDeg: 20, now: settledClock() });
  t.after(() => { lit.disable(); fixtures.dispose(); });
  return { scene, root, fixtures, mesh, face, display, hook, renderer, lit, setMesh: next => { current = next; } };
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
    assert.equal(light.shadow.mapSize.x, 1024); assert.equal(light.decay, 2);
  }
  for (const light of downs) {
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(350, 1) * Model.litDimmingFraction(.75) * EXPOSURE) < 1e-9);
    assert.equal(light.castShadow, true); assert.equal(light.shadow.radius, 6, 'soft');
  }
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
  assert.equal(w.lit.sync(), true, 'an aim change is picked up');
  assert.equal(w.renderer.shadowMap.needsUpdate, true, 'and re-renders the shadow maps once');
  const spot = lights(w).find(light => light.userData.lit_source === 'spot' && light.userData.fixture_id === id);
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
  const litMaterial = w.face.material;
  assert.ok(litMaterial.isMeshStandardMaterial); assert.equal(litMaterial.map, w.display.map, 'the scan colours stand in for albedo');
  assert.equal(w.face.castShadow, true); assert.equal(w.face.receiveShadow, true);
  assert.equal(w.face.customDepthMaterial, undefined, 'default depth: the camera cutaway never changes world shadowing');
  assert.equal(litMaterial.onBeforeCompile, THREE.Material.prototype.onBeforeCompile, 'no custom shader: built-in material only, WebGPU-portable');
  assert.equal(litMaterial.toneMapped, true); assert.equal(Object.hasOwn(litMaterial, 'customProgramCacheKey'), false);
  const state = w.lit.disable();
  assert.equal(state.active, false);
  assert.equal(w.face.material, w.display, 'the exact display material returns');
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
  assert.ok(lights(w).filter(light => light.castShadow).every(light => light.shadow.mapSize.x === 512));
  assert.ok(lights(w).filter(light => light.userData.lit_source === 'zone').every(light => light.intensity === 0));
  assert.ok(lights(w).filter(light => light.userData.lit_source === 'spot').every(light => light.intensity > 0));
});

test('a scan without normals gets them for Lit only', t => {
  const w = world(t), bare = new THREE.BufferGeometry();
  bare.setAttribute('position', w.face.geometry.attributes.position.clone()); bare.setIndex(w.face.geometry.index.clone());
  const node = new THREE.Mesh(bare, new THREE.MeshBasicMaterial()); w.mesh.add(node);
  w.lit.enable();
  assert.ok(bare.attributes.normal, 'lighting needs normals, so Lit derives them');
  w.lit.disable();
  assert.equal(bare.attributes.normal, undefined, 'and removes them on exit');
  assert.ok(w.face.geometry.attributes.normal, 'normals the scan already had are kept');
});

test('a replaced mesh is relit and the old one restored', t => {
  const w = world(t); w.lit.enable();
  const other = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial()), original = other.material;
  w.setMesh(other); w.lit.sync();
  assert.equal(w.face.material, w.display); assert.ok(other.material.isMeshStandardMaterial);
  w.lit.disable(); assert.equal(other.material, original);
});

test('one bounce: the eight strongest lit patches become hemispherical bounce lights with assumed albedo', t => {
  const w = world(t);
  // A closed room for the rays: floor, four walls (the display face is the floor).
  const room = new THREE.Group(), side = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(12, 12), side); room.add(floor);
  for (const [x, y, rz] of [[6, 0, Math.PI / 2], [-6, 0, Math.PI / 2], [0, 6, 0], [0, -6, 0]]) {
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(12, 4), side);
    wall.rotation.set(Math.PI / 2, rz, 0, 'ZXY'); wall.position.set(x, y, 2); room.add(wall);
  }
  w.root.add(room); w.root.updateMatrixWorld(true);
  w.lit.disable();
  const lit = Lit.createLitView({ scene: w.scene, renderer: w.renderer, getMesh: () => w.mesh, getFixtureRoots: () => w.fixtures.root.children,
    getCollision: () => room, spotFwhmDeg: 20, now: settledClock() });
  t.after(() => lit.disable());
  const state = lit.enable();
  assert.equal(state.bounceLights, 8, 'a fixed set of eight bounce lights');
  assert.equal(state.bounceSources, 8, 'four fixtures give more than eight lit patches; the eight strongest are kept');
  assert.equal(state.bounceRangeM, 4.5);
  assert.deepEqual({ ...state.assumedAlbedos }, { spotFlat: .35, spotVertical: .6, down: .35, zoneWall: .65, zoneOther: .4 }, 'the assumed albedos are reported with the view');
  assert.equal(state.bounceLimit, 'One bounce · casts no shadows · can leak up to 4.5 m through walls');
  const bounce = lit.group.children.filter(node => node.isSpotLight && node.userData.lit_source === 'bounce');
  assert.equal(bounce.length, 8);
  for (const light of bounce) {
    assert.equal(light.castShadow, false, 'bounce lights cast no shadows');
    assert.equal(light.distance, 4.5); assert.ok(Math.abs(light.angle - 1.45) < 1e-12); assert.equal(light.penumbra, 1);
    assert.ok(light.intensity > 0 && light.userData.patch.assumed === true && light.userData.measured === false);
    assert.ok(Math.abs(light.intensity - light.userData.patch.reflected_lm / Math.PI * .9 * EXPOSURE) < 1e-9, 'intensity is reflected flux / pi x 0.9 x the night exposure');
    const off = light.target.position.clone().sub(light.position);
    assert.ok(Math.abs(off.length() - 1) < 1e-9, 'points along the patch normal');
  }
  const reflected = bounce.map(light => light.userData.patch.reflected_lm);
  assert.deepEqual(reflected, [...reflected].sort((a, b) => b - a), 'strongest first');
  // Spots: 450 x 1 x 0.8 x albedo; they outrank every zone patch.
  assert.ok(bounce.slice(0, 4).every(light => light.userData.patch.kind === 'spot'));
  for (const light of bounce.filter(item => item.userData.patch.kind === 'spot'))
    assert.ok([.35, .6].includes(light.userData.patch.albedo) && Math.abs(light.userData.patch.reflected_lm - 450 * .8 * light.userData.patch.albedo) < 1e-9);
});

test('bounce follows the channels and drops a patch whose light is off', t => {
  const w = world(t);
  const room = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  w.root.add(room); w.root.updateMatrixWorld(true); w.lit.disable();
  const lit = Lit.createLitView({ scene: w.scene, renderer: w.renderer, getMesh: () => w.mesh, getFixtureRoots: () => w.fixtures.root.children,
    getCollision: () => room, spotFwhmDeg: 20, now: settledClock() });
  t.after(() => lit.disable());
  lit.enable();
  const before = lit.state().bounceSources;
  for (const item of w.fixtures.readback().fixtures) w.fixtures.setFixtureState(item.fixture_id, { spotlight: { on: false }, downward: { on: false },
    radials: [1, 2, 3, 4, 5, 6].map(number => ({ number, on: false })) });
  lit.sync();
  assert.ok(before > 0); assert.equal(lit.state().bounceSources, 0, 'no lit patch, no bounce');
  assert.ok(lit.group.children.filter(node => node.userData.lit_source === 'bounce').every(light => light.intensity === 0));
});

test('bouncePatches (pure model) uses the assumed albedos and ignores rays that find no surface (an opening)', () => {
  const light = (kind, level = 1) => ({ kind, level, kelvin: 3000, number: null });
  const lights = [light('spot'), light('spot'), light('down'), light('zone'), light('zone'), light('zone'), light('zone', 0)];
  const hits = [{ point: [1, 0, 0], normal: [0, 1, 0] }, { point: [1, 0, 0], normal: [-1, 0, 0] }, { point: [0, 0, 0], normal: [0, 1, 0] },
    { point: [1, 0, 0], normal: [-1, 0, 0] }, { point: [1, 0, 0], normal: [-.6, .8, 0] }, null, { point: [1, 0, 0], normal: [-1, 0, 0] }];
  const patches = Model.bouncePatches(lights, hits, { up: [0, 1, 0] });
  const by = kind => patches.filter(patch => patch.kind === kind).map(patch => patch.albedo).sort();
  assert.deepEqual(by('spot'), [.35, .6]); assert.deepEqual(by('down'), [.35]); assert.deepEqual(by('zone'), [.4, .65]);
  assert.equal(patches.length, 5, 'the zone with no surface and the zone that is off add nothing');
  assert.ok(Math.abs(patches.find(patch => patch.kind === 'down').reflected - 350 * .8 * .35) < 1e-9);
  assert.ok(Math.abs(patches.find(patch => patch.kind === 'zone' && patch.albedo === .65).reflected - 120 * .7 * .65) < 1e-9);
  const strongest = patches[0];
  assert.deepEqual(strongest.position, strongest.point.map((v, i) => v + strongest.normal[i] * .25), '0.25 m off the patch');
  assert.ok(Math.abs(strongest.intensity - strongest.reflected / Math.PI * .9 * Model.LIT_EXPOSURE) < 1e-12);
  assert.deepEqual({ ...Model.LIT_BOUNCE_ALBEDO }, { spotFlat: .35, spotVertical: .6, down: .35, zoneWall: .65, zoneOther: .4 });
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
