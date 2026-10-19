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
const Lit = await import(dataModule(replaceOne(fs.readFileSync(sourceFile('lit-view.mjs'), 'utf8'), "from 'three'", 'from ' + JSON.stringify(threeURL))));
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
  const lit = Lit.createLitView({ scene, renderer, getMesh: () => current, getFixtureRoots: () => fixtures.root.children, spotFwhmDeg: 20 });
  t.after(() => { lit.disable(); fixtures.dispose(); });
  return { scene, root, fixtures, mesh, face, display, hook, renderer, lit, setMesh: next => { current = next; } };
}
const lights = w => w.lit.group.children.filter(node => node.isSpotLight);

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
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(450, theta) * 1) < 1e-9, 'spot at its restore-default brightness 1');
    assert.equal(light.castShadow, true); assert.equal(light.shadow.radius, 2.5, 'crisp');
    assert.equal(light.shadow.mapSize.x, 1024); assert.equal(light.decay, 2);
  }
  for (const light of downs) {
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(350, 1) * .75) < 1e-9);
    assert.equal(light.castShadow, true); assert.equal(light.shadow.radius, 6, 'soft');
  }
  for (const light of zones) {
    assert.equal(light.castShadow, false, 'each radial zone is its own unshadowed lobe');
    assert.ok(Math.abs(light.intensity - Lit.candelaFromLumens(120, .95) * .75) < 1e-9);
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
  assert.ok(spot.color.equals(warm)); assert.ok(Math.abs(spot.intensity - Lit.candelaFromLumens(450, 20 * .8 * Math.PI / 180) * .5) < 1e-9);
  w.fixtures.setFixtureState(id, { spotlight: { on: false } }); w.lit.sync();
  assert.equal(spot.intensity, 0, 'an off channel adds no light');
});

test('mesh materials are lit copies that keep the display hooks and confine tone mapping, and come back exactly', t => {
  const w = world(t); w.lit.enable();
  const litMaterial = w.face.material;
  assert.ok(litMaterial.isMeshStandardMaterial); assert.equal(litMaterial.map, w.display.map, 'the scan colours stand in for albedo');
  assert.equal(w.face.castShadow, true); assert.equal(w.face.receiveShadow, true);
  assert.equal(w.face.customDepthMaterial, undefined, 'default depth: the camera cutaway never changes world shadowing');
  const shader = { uniforms: {}, vertexShader: '#include <common>\nvoid main(){}', fragmentShader: '#include <common>\nvoid main(){\n#include <tonemapping_fragment>\n}' };
  litMaterial.onBeforeCompile(shader);
  assert.match(shader.fragmentShader, /display-hook/, 'the display material hook (cutaway, near cut) still runs');
  assert.match(shader.fragmentShader, /gl_FragColor\.rgb = litACES\(gl_FragColor\.rgb\)/); assert.doesNotMatch(shader.fragmentShader, /#include <tonemapping_fragment>/);
  assert.equal(shader.uniforms.uLitExposure.value, .038);
  assert.match(litMaterial.customProgramCacheKey(), /^lit-view-1\|home-scan-display-floor-0-1$/);
  const state = w.lit.disable();
  assert.equal(state.active, false);
  assert.equal(w.face.material, w.display, 'the exact display material returns');
  assert.equal(w.face.castShadow, false); assert.equal(w.face.receiveShadow, false);
  assert.equal(w.renderer.shadowMap.enabled, false); assert.equal(w.renderer.shadowMap.type, THREE.BasicShadowMap); assert.equal(w.renderer.shadowMap.autoUpdate, true);
  assert.equal(w.lit.group.parent, null); assert.equal(lights(w).length, 0);
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
