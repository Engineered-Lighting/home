// Actual selected-fixture presentation with real vendored Three and authored
// DOM/canvas adapters. No WebGL, household assets, output, timer or browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';

const require = createRequire(import.meta.url);
const D = require('../app/src/home-apartment-directional.js');
const vendor = new URL('../app/src/vendor/three-0.180.0/three.module.js', import.meta.url).href;
function actualModule(name) {
  const source = fs.readFileSync(new URL('../app/src/home-3d/' + name, import.meta.url), 'utf8');
  const imports = /import \* as THREE from 'three';/g;
  assert.equal([...source.matchAll(imports)].length, 1, 'one exact bare Three resolution seam: ' + name);
  return import('data:text/javascript;base64,' + Buffer.from(source.replace(imports,
    `import * as THREE from ${JSON.stringify(vendor)};`)).toString('base64'));
}
const { createDirectionalPresentation, radialDisplayColor } = await actualModule('apartment-directional-presentation.mjs');
const { createEquipmentSpotlightDepth } = await actualModule('equipment-spotlight-visual.mjs');
const { createPicking } = await actualModule('picking.js');

class Element {
  constructor(tag, document) { this.tagName = tag; this.ownerDocument = document; this.children = []; this.parentNode = null; this.style = {}; this.attributes = {}; this.hidden = false; this.textContent = ''; }
  get isConnected() { return this === this.ownerDocument.body || !!this.parentNode?.isConnected; }
  appendChild(child) { child.remove(); this.children.push(child); child.parentNode = this; return child; }
  remove() { if (this.parentNode) { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; } }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, listener) { (this.listeners ||= new Map()).set(type, [...(this.listeners.get(type) || []), listener]); }
  removeEventListener(type, listener) { this.listeners?.set(type, (this.listeners.get(type) || []).filter(item => item !== listener)); }
  querySelectorAll(selector) {
    const match = /^\[([^=\]]+)(?:="([^"]+)")?\]$/.exec(selector); assert.ok(match, 'authored adapter only supports required attribute selectors');
    const found = [];
    for (const child of this.children) { if (child.getAttribute(match[1]) !== null && (match[2] === undefined || child.getAttribute(match[1]) === match[2])) found.push(child); found.push(...child.querySelectorAll(selector)); }
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getContext(kind) {
    assert.equal(kind, '2d'); return { createRadialGradient: () => ({ addColorStop() {} }), fillRect() {}, fillStyle: '' };
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 600 }; }
}
const close = (actual, expected, epsilon = 1e-6) => assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} != ${expected}`);
const deepFreeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
const radials = () => Array.from({ length: 6 }, (_, i) => ({ number: i + 1, azimuth_deg: i * 60, on: true, brightness: .5, color_temp_kelvin: 3000 }));
function descriptor() { return { fixture_id: 'authored-fixture', mount_id: 'authored-mount', generation: 7, equipment_revision: 3, equipment_digest: 'a'.repeat(64),
  binding: { content_revision: 'authored-r1', registration_version: 1, manifest_digest: 'b'.repeat(64), frame_digest: 'c'.repeat(64), canonical_collision_digest: 'd'.repeat(64) },
  display_anchor_m: [.2, .3, 2.2], label: 'Authored fixture', state: { radials: radials(), spot: { on: true, brightness: .4, pan_deg: 23, tilt_deg: 34 }, downward: { on: true, brightness: .3 } } }; }
function fixture(overrides = {}) {
  const document = { createElement(tag) { return new Element(tag, this); } }; document.body = document.createElement('body');
  const host = document.createElement('div'); host.clientWidth = 800; host.clientHeight = 600; document.body.appendChild(host);
  const root = document.createElement('svg'), pill = document.createElement('div'), readout = document.createElement('div'), plan = document.createElement('svg');
  host.appendChild(root); host.appendChild(pill); host.appendChild(readout); host.appendChild(plan);
  const paths = [], planPaths = [];
  for (let i = 1; i <= 6; i++) { const path = document.createElement('path'); path.setAttribute('data-apt-radial-number', i); root.appendChild(path); paths.push(path);
    const planPath = document.createElement('path'); planPath.setAttribute('data-apt-radial-plan-zone', i); plan.appendChild(planPath); planPaths.push(planPath); }
  for (const name of ['draw', 'hit']) { const hub = document.createElement('circle'); hub.setAttribute('data-apt-radial-hub', name); root.appendChild(hub); }
  const frame = { project: ([x, y]) => ({ x: 400 + x * 100, y: 300 - y * 100, visible: true }), unproject: (x, y) => [(x - 400) / 100, (300 - y) / 100] };
  const group = new THREE.Group(), borrowed = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); group.add(borrowed);
  const state = { current: true, mode: 'zones', reduced: false, projection: null, published: [], retired: 0 };
  const initial = { descriptor: descriptor(), frame, source: 'authored' }; state.projection = initial;
  const options = { group, host, api: D, initial, getProjection: () => state.projection, getMode: () => state.mode,
    publishPlane: value => state.published.push(value), isCurrent: () => state.current, onRetire: () => state.retired++, reducedMotion: () => state.reduced, ...overrides };
  const api = createDirectionalPresentation(options), elements = { root, pill, readout };
  const detach = api.attach(elements), detachPlan = api.attachPlan(plan);
  const scene = group.getObjectByName('apartment-directional-composer');
  return { api, options, state, document, host, group, borrowed, initial, frame, scene, elements, paths, planPaths, detach, detachPlan };
}
function collectResources(scene) {
  // Three sprites borrow one module-global quad; the presenter owns their
  // materials/texture, not that shared geometry.
  const result = new Set(); scene.traverse(node => { if (node.geometry && !node.isSprite) result.add(node.geometry); for (const material of [node.material].flat().filter(Boolean)) { result.add(material); if (material.map) result.add(material.map); } }); return [...result];
}

test('the real drawing is one continuous 240-segment fan with cosine contour, shared seam and radial shader', () => {
  const f = fixture(); f.state.reduced = true; f.initial.descriptor.state.radials[0].brightness = .25; f.initial.descriptor.state.radials[1].brightness = .75;
  assert.equal(f.api.frame({ nowMs: 0 }), true);
  const fill = f.scene.getObjectByName('directional-polar-fill'), edge = f.scene.getObjectByName('directional-polar-edge');
  assert.equal(fill.geometry.attributes.position.count, 242); assert.equal(fill.geometry.index.count, 720); assert.equal(edge.geometry.attributes.position.count, 241);
  assert.deepEqual(Array.from(fill.geometry.index.array.slice(0, 6)), [0, 1, 2, 0, 2, 3]);
  assert.deepEqual(Array.from(fill.geometry.index.array.slice(-3)), [0, 240, 241]);
  const position = fill.geometry.attributes.position;
  for (const [index, degrees] of [[1, 0], [11, 15], [21, 30], [41, 60]]) {
    const blend = (1 - Math.cos(Math.PI * degrees / 60)) / 2, expectedLevel = .25 + .5 * blend;
    close(Math.hypot(position.getX(index), position.getY(index)), .14 + expectedLevel * 1.16);
  }
  close(position.getX(1), position.getX(241)); close(position.getY(1), position.getY(241));
  assert.equal(fill.geometry.attributes.t.getX(0), 0); assert.equal(fill.geometry.attributes.t.getX(241), 1);
  assert.equal(fill.material.blending, THREE.AdditiveBlending); assert.equal(fill.material.depthWrite, false); assert.equal(fill.material.depthTest, false);
  assert.match(fill.material.fragmentShader, /pow\(1\.0-vT,1\.8\)/); assert.match(fill.material.fragmentShader, /smoothstep\(0\.8,1\.0,vT\)/);
  assert.ok(Array.from(fill.geometry.attributes.zoneTint.array).every(Number.isFinite)); f.api.dispose();
});

test('frame publishes only after native DOM hit paths and hub positions match its own projection', () => {
  const f = fixture(); let publications = 0;
  const prior = f.options.publishPlane;
  // Construct a fresh real presenter with the assertion at the ownership seam.
  f.api.dispose();
  const api = createDirectionalPresentation({ ...f.options, publishPlane: value => {
    prior(value); if (!value) return; publications++;
    assert.equal(f.elements.root.hidden, false);
    for (const zone of value.geometry.zones) assert.equal(f.paths[zone.number - 1].getAttribute('d'), zone.path);
    assert.equal(f.elements.root.querySelector('[data-apt-radial-hub="hit"]').getAttribute('cx'), String(value.geometry.hub.x));
    assert.equal(f.elements.root.querySelector('[data-apt-radial-hub="hit"]').getAttribute('data-apt-radial-start'), 'hub');
    assert.match(f.elements.pill.style.transform, /^translate3d\(400px,274px,0\)/);
  } });
  api.attach(f.elements); assert.equal(api.frame({ nowMs: 100 }), true); assert.equal(publications, 1); api.dispose();
});

test('the mode pill stays inside the room with its coarse hit area, sized by an observer rather than a frame-loop layout read', () => {
  const f = fixture(), observers = [];
  f.document.defaultView = { ResizeObserver: class { constructor(callback) { this.callback = callback; observers.push(this); }
    observe() {} disconnect() { this.disconnected = true; } } };
  f.api.attach(f.elements);
  Object.assign(f.elements.root, { clientWidth: 800, clientHeight: 600 }); Object.assign(f.elements.pill, { offsetWidth: 180, offsetHeight: 44 });
  observers.at(-1).callback();
  const project = f.frame.project;
  f.frame.project = point => { const p = project(point); return { ...p, x: p.x - 395, y: p.y - 292 }; };
  f.api.frame({ nowMs: 0 });
  assert.match(f.elements.pill.style.transform, /^translate3d\(95px,49px,0\)/, 'clamped 5 px inside the top-left edge');
  f.frame.project = point => { const p = project(point); return { ...p, x: p.x + 395, y: p.y + 330 }; };
  f.api.frame({ nowMs: 16 });
  assert.match(f.elements.pill.style.transform, /^translate3d\(705px,595px,0\)/, 'clamped 5 px inside the bottom-right edge');
  f.frame.project = project; f.api.frame({ nowMs: 32 });
  assert.match(f.elements.pill.style.transform, /^translate3d\(400px,274px,0\)/, 'an unclamped pill keeps the handoff position');
  f.api.dispose(); assert.equal(observers.at(-1).disconnected, true, 'disposal disconnects the observer');
});

test('every radial hit shape answers taps, so touch adjustment never moves a lobe press onto the mode pill', () => {
  const f = fixture(), hub = f.elements.root.querySelector('[data-apt-radial-hub="hit"]');
  for (const node of [...f.paths, hub]) assert.equal(node.listeners?.get('click')?.length, 1, 'marked while attached');
  f.detach();
  for (const node of [...f.paths, hub]) assert.equal(node.listeners.get('click').length, 0, 'unmarked on detach');
  f.api.dispose();
});

test('the handoff pill stays on whole pixels during fractional camera settling while the drawn input plane remains exact', () => {
  const f = fixture(), project = f.frame.project;
  let offset = 0;
  f.frame.project = point => { const p = project(point); return { ...p, x: p.x + offset, y: p.y + offset }; };
  f.api.frame({ nowMs: 0 }); const position = f.elements.pill.style.transform;
  for (const value of [.1, .2, .4]) {
    offset = value; f.api.frame({ nowMs: value * 100 });
    assert.equal(f.elements.pill.style.transform, position, 'subpixel settling must not keep a native button moving');
    assert.equal(+f.elements.root.querySelector('[data-apt-radial-hub="hit"]').getAttribute('cx'), 400 + value);
    assert.equal(f.state.published.at(-1).geometry.hub.x, 400 + value, 'input retains the full-precision drawn plane');
  }
  offset = .6; f.api.frame({ nowMs: 100 });
  assert.match(f.elements.pill.style.transform, /^translate3d\(401px,275px,0\)/); f.api.dispose();
});

test('pointer readout is immediate while only drawn brightness/opacity ease over 160ms and target channels stay exact', () => {
  const f = fixture(); const before = structuredClone(f.initial.descriptor); deepFreeze(f.initial.descriptor);
  f.api.frame({ nowMs: 0 }); const node = f.scene.getObjectByName('directional-zone-node-1'), priorRadius = node.position.length();
  assert.equal(f.api.pointer({ zone: 1, level: .92, x: 12, y: 34 }, true), true);
  assert.equal(f.elements.readout.hidden, false); assert.match(f.elements.readout.textContent, /Zone 1.*92%/);
  assert.equal(f.elements.readout.style.transform, 'translate3d(26px,-2px,0)'); assert.equal(f.elements.pill.hidden, true);
  close(node.position.length(), priorRadius); assert.deepEqual(f.initial.descriptor, before);
  const next = structuredClone(before); next.state.radials[0].brightness = 1; f.state.projection = { ...f.initial, descriptor: deepFreeze(next) };
  assert.equal(f.api.sync(next), true); assert.equal(f.api.getSnapshot().descriptor.state.radials[0].brightness, 1);
  f.api.frame({ nowMs: 160 }); const shown = .5 + .5 * (1 - Math.exp(-1));
  close(node.position.length(), .14 + shown * 1.16); close(Number(f.planPaths[0].getAttribute('fill-opacity')), (.25 + .5 / 1.3) + (.5 / 1.3) * (1 - Math.exp(-1)));
  assert.deepEqual(next.state.spot, before.state.spot); assert.deepEqual(next.state.downward, before.state.downward);
  assert.equal(f.api.readback().target[0].brightness, 1); assert.deepEqual(f.initial.descriptor, before);
  f.state.reduced = true; f.api.frame({ nowMs: 176 }); close(node.position.length(), 1.3); f.api.dispose();
});

test('canonical fixture-height geometry ignores spotlight pan and remains under the borrowed room frame', () => {
  const f = fixture(); f.api.frame({ nowMs: 0 });
  assert.deepEqual(f.scene.position.toArray(), [.2, .3, 2.2]);
  const fill = f.scene.getObjectByName('directional-polar-fill'), before = Array.from(fill.geometry.attributes.position.array);
  const next = structuredClone(f.initial.descriptor); next.state.spot.pan_deg = 175; next.state.spot.tilt_deg = 5;
  f.state.projection = { ...f.initial, descriptor: next }; f.api.frame({ nowMs: 160 });
  assert.deepEqual(Array.from(fill.geometry.attributes.position.array), before); assert.deepEqual(f.scene.rotation.toArray(), [0, 0, 0, 'XYZ']);
  assert.equal(f.borrowed.parent, f.group); f.api.dispose();
});

test('off nodes stay hidden until active, active nodes are white and enlarged, and guides exist only while dragging', () => {
  const f = fixture(); f.initial.descriptor.state.radials.forEach(zone => { zone.on = false; zone.brightness = 0; }); f.state.reduced = true;
  f.api.frame({ nowMs: 0 }); const fill = f.scene.getObjectByName('directional-polar-fill'), edge = f.scene.getObjectByName('directional-polar-edge');
  assert.equal(fill.visible, false); assert.equal(edge.visible, false);
  for (let i = 1; i <= 6; i++) { assert.equal(f.scene.getObjectByName('directional-zone-node-' + i).visible, false); assert.equal(f.scene.getObjectByName('directional-zone-tick-' + i).visible, true); assert.equal(f.paths[i - 1].getAttribute('data-apt-radial-start'), null); }
  const guides = f.scene.children.filter(node => node.isLine && node !== edge); assert.equal(guides.length, 3); assert.ok(guides.every(node => node.visible === false));
  f.api.pointer({ zone: 3, level: 0, x: 10, y: 10 }, true); f.api.frame({ nowMs: 16 });
  const active = f.scene.getObjectByName('directional-zone-node-3'); assert.equal(active.visible, true); assert.deepEqual(active.material.color.toArray(), [1, 1, 1]); close(active.scale.x, .17);
  assert.ok(guides.every(node => node.visible)); close(f.scene.getObjectByName('directional-zone-tick-3').scale.x, .075);
  f.api.pointer(null, false); f.api.frame({ nowMs: 32 }); assert.ok(guides.every(node => !node.visible)); assert.equal(active.visible, false); f.api.dispose();
});

test('Spot mode withdraws scene/hub painting and readout while retaining the native mode pill', () => {
  const f = fixture(); f.api.frame({ nowMs: 0 }); f.api.pointer({ zone: 1, level: .5, x: 10, y: 10 }, true);
  f.api.pointer(null, false); f.state.mode = 'spot'; f.api.frame({ nowMs: 16 });
  assert.equal(f.scene.visible, false); assert.equal(f.elements.pill.hidden, false); assert.equal(f.elements.readout.hidden, true);
  assert.equal(f.elements.root.querySelector('[data-apt-radial-hub="hit"]').getAttribute('data-apt-radial-start'), null);
  assert.ok(f.paths.every(path => path.getAttribute('data-apt-radial-start') === null)); f.api.dispose();
});

for (const [name, mutate] of [['fixture', d => { d.fixture_id += '-new'; }], ['generation', d => { d.generation++; }], ['binding', d => { d.binding.frame_digest = 'e'.repeat(64); }], ['anchor', d => { d.display_anchor_m[2]++; }], ['heading', d => { d.state.radials[0].azimuth_deg++; }]]) {
  test('a different ' + name + ' cannot publish or borrow the pinned presentation', () => {
    const f = fixture(); assert.equal(f.api.frame({ nowMs: 0 }), true);
    const next = structuredClone(f.initial.descriptor); mutate(next); f.state.projection = { ...f.initial, descriptor: next };
    assert.equal(f.api.frame({ nowMs: 16 }), false); assert.equal(f.state.published.at(-1), null); assert.equal(f.api.readback().drawn, false);
    assert.equal(f.scene.visible, false); assert.equal(f.elements.root.hidden, true); assert.equal(f.api.sync(next), false); f.api.dispose();
  });
}

for (const [name, retire] of [['false owner', f => { f.state.current = false; }], ['throwing owner', f => { f.options.ownerThrows = true; }]]) {
  test(name + ' retires all owned presentation before any further pointer/frame publication', () => {
    let f; f = fixture({ isCurrent: () => { if (f?.options.ownerThrows) throw Error('Authored owner'); return f?.state.current ?? true; } });
    f.api.frame({ nowMs: 0 }); retire(f); assert.equal(f.api.pointer({ zone: 1, level: 1, x: 0, y: 0 }, true), false);
    assert.equal(f.api.readback().disposed, true); assert.equal(f.scene.parent, null); assert.equal(f.elements.root.hidden, true); assert.equal(f.state.published.at(-1), null);
    assert.equal(f.api.frame({ nowMs: 16 }), false); assert.equal(f.state.retired, 1);
  });
}

test('publication failure cannot prevent synchronous retirement, hide/detach or exactly-once owned resource cleanup', () => {
  let fail = false;
  const f = fixture({ publishPlane: () => { if (fail) throw Error('Authored plane publication'); } }); f.api.frame({ nowMs: 0 });
  const resources = collectResources(f.scene), counts = new Map();
  for (const resource of resources) resource.addEventListener('dispose', () => counts.set(resource, (counts.get(resource) || 0) + 1));
  let borrowedDisposals = 0; f.borrowed.geometry.addEventListener('dispose', () => borrowedDisposals++); f.borrowed.material.addEventListener('dispose', () => borrowedDisposals++);
  fail = true; assert.doesNotThrow(() => f.api.dispose());
  assert.equal(f.api.readback().disposed, true); assert.equal(f.scene.parent, null); assert.equal(f.elements.root.hidden, true); assert.equal(f.elements.readout.hidden, true);
  assert.equal(f.state.retired, 1); assert.ok(resources.every(resource => counts.get(resource) === 1));
  assert.equal(f.borrowed.parent, f.group); assert.equal(borrowedDisposals, 0);
  assert.doesNotThrow(() => { f.api.dispose(); f.detach(); f.detachPlan(); }); assert.equal(f.state.retired, 1); assert.ok(resources.every(resource => counts.get(resource) === 1));
});

test('one throwing owned resource and retirement notification cannot strand siblings or borrowed resources', () => {
  const f = fixture({ onRetire: () => { throw Error('Authored retire observer'); } }); const resources = collectResources(f.scene), counts = new Map();
  resources[0].dispose = () => { counts.set(resources[0], 1); throw Error('Authored resource'); };
  for (const resource of resources.slice(1)) resource.addEventListener('dispose', () => counts.set(resource, (counts.get(resource) || 0) + 1));
  assert.doesNotThrow(() => f.api.dispose()); assert.equal(f.scene.parent, null); assert.equal(f.api.readback().disposed, true);
  assert.ok(resources.every(resource => counts.get(resource) === 1)); assert.equal(f.borrowed.parent, f.group); assert.doesNotThrow(() => f.api.dispose());
});

test('constructor canvas failure releases already-owned resources and publishes no live plane', () => {
  const f = fixture(); f.api.dispose(); let nulls = 0;
  const create = f.document.createElement; f.document.createElement = function (tag) { const node = create.call(this, tag); if (tag === 'canvas') node.getContext = () => null; return node; };
  assert.throws(() => createDirectionalPresentation({ ...f.options, publishPlane: value => { assert.equal(value, null); nulls++; } }), /directional_glow_canvas_unavailable/);
  assert.equal(f.group.getObjectByName('apartment-directional-composer'), undefined); assert.equal(f.borrowed.parent, f.group); assert.equal(nulls, 1);
});

test('the actual opaque-depth producer excludes every presentation object without relying on metadata', () => {
  const f = fixture(); f.api.frame({ nowMs: 0 }); const resident = new THREE.Scene(); resident.add(f.group);
  const opaque = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshBasicMaterial()); resident.add(opaque); resident.updateMatrixWorld(true);
  const depth = createEquipmentSpotlightDepth(new THREE.Group()), captures = [];
  const renderer = { info: { render: { frame: 1 } }, autoClear: false,
    getDrawingBufferSize: size => size.set(800, 600), getRenderTarget: () => null, setRenderTarget() {}, getScissorTest: () => false,
    getClearAlpha: () => 0, getClearColor: color => color.set(0), getViewport: value => value.set(0, 0, 800, 600), getScissor: value => value.set(0, 0, 800, 600),
    setViewport() {}, setScissor() {}, setScissorTest() {}, setClearColor() {}, render: scene => { captures.push(scene.children.map(node => node.geometry)); renderer.info.render.frame++; } };
  // The unrelated borrowed test mesh is opaque and should remain admitted.
  assert.ok(depth.capture(renderer, resident, new THREE.PerspectiveCamera()));
  assert.equal(depth.readback().opaque_mesh_count, 2); assert.equal(captures.length, 1);
  assert.deepEqual(new Set(captures[0]), new Set([opaque.geometry, f.borrowed.geometry]));
  assert.ok(!captures[0].includes(f.scene.getObjectByName('directional-polar-fill').geometry)); depth.dispose(); f.api.dispose(); opaque.geometry.dispose(); opaque.material.dispose();
});

test('the actual engine surface-hit producer raycasts only its collision mesh, never the nearer composer', () => {
  const f = fixture(); f.api.frame({ nowMs: 0 });
  const apartmentRoot = new THREE.Group(); apartmentRoot.add(f.group);
  const collision = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshBasicMaterial({ side: THREE.DoubleSide })); collision.name = 'Authored collision'; apartmentRoot.add(collision); apartmentRoot.updateMatrixWorld(true);
  const camera = new THREE.PerspectiveCamera(60, 4 / 3, .1, 20); camera.position.set(.2, .3, 8); camera.lookAt(.2, .3, 0); camera.updateMatrixWorld(true);
  const picking = createPicking(camera, f.host);
  assert.ok(picking.pick([apartmentRoot], 400, 300).some(hit => hit.object === f.scene.getObjectByName('directional-polar-fill')), 'the visible composer would be raycastable if indiscriminately included');
  const source = fs.readFileSync(new URL('../app/src/home-3d/engine.js', import.meta.url), 'utf8');
  const ast = require('../app/src/vendor/babel-7.29.0/babel.min.js').transform(source, { ast: true, code: false }).ast;
  const methods = [];
  function visit(node) { if (!node || typeof node !== 'object') return; if (node.type === 'ObjectMethod' && node.key?.name === 'displaySurfaceHit') methods.push(node); for (const value of Object.values(node)) { if (Array.isArray(value)) value.forEach(visit); else if (value && typeof value === 'object') visit(value); } }
  visit(ast); assert.equal(methods.length, 1);
  const method = source.slice(methods[0].start, methods[0].end), context = vm.createContext({ picking, apartmentRoot, modes: { getCollision: () => collision, debugInfo: () => ({ dollhouseCutaway: {} }) }, cutawayM: { dollhouseKeep: () => 1 } });
  vm.runInContext('globalThis.engine = {' + method + '};', context);
  const hit = context.engine.displaySurfaceHit(400, 300); assert.equal(hit.object, collision); close(hit.point[2], 0); assert.equal(f.api.readback().drawn, true);
  f.api.dispose(); collision.geometry.dispose(); collision.material.dispose();
});

test('qualitative temperature colors remain finite and distinguish warm from cool', () => {
  const warm = radialDisplayColor(2200), cool = radialDisplayColor(5000);
  assert.ok([...warm.toArray(), ...cool.toArray()].every(value => Number.isFinite(value) && value >= 0 && value <= 1)); assert.ok(warm.b < cool.b);
});

test('the cursor readout says Off at zero and whole percents otherwise', () => {
  const f = fixture(); f.api.frame({ nowMs: 0 });
  for (const [level, text] of [[0, 'Zone 3 · Off'], [.004, 'Zone 3 · Off'], [.01, 'Zone 3 · 1%'], [.5, 'Zone 3 · 50%'], [1, 'Zone 3 · 100%']]) {
    assert.equal(f.api.pointer({ zone: 3, level, x: 12, y: 34 }, true), true);
    assert.equal(f.elements.readout.hidden, false); assert.equal(f.elements.readout.textContent, text);
  }
  f.api.dispose();
});

test('composer hit paths admit a lit lobe 0.12 m past its drawn edge while the drawn shape stays exact', () => {
  const f = fixture(); f.initial.descriptor.state.radials[1].on = false; f.state.reduced = true;
  assert.equal(f.api.frame({ nowMs: 0 }), true);
  const published = f.state.published.at(-1).geometry, radials = f.initial.descriptor.state.radials;
  const drawn = D.radialSectorPaths({ radials, frame: f.frame }), wide = D.radialSectorPaths({ radials, frame: f.frame, hitMargin: .12 });
  assert.deepEqual(published.zones.map(zone => zone.path), wide.zones.map(zone => zone.path));
  for (const zone of published.zones) assert.equal(f.paths[zone.number - 1].getAttribute('d'), zone.path, 'native hit paths equal the published plane');
  assert.equal(published.contour, drawn.contour); assert.deepEqual(published.hub, drawn.hub);
  assert.notEqual(wide.zones[0].path, drawn.zones[0].path);
  assert.equal(f.paths[0].getAttribute('data-apt-radial-start'), 'zone:1'); assert.equal(f.paths[1].getAttribute('data-apt-radial-start'), null,
    'the margin never admits an unlit zone');
  const position = f.scene.getObjectByName('directional-polar-fill').geometry.attributes.position;
  close(Math.hypot(position.getX(1), position.getY(1)), .14 + .5 * 1.16, 1e-6);
  f.api.dispose();
});
