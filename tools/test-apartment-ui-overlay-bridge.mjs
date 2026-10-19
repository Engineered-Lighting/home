// Real vendored Three math and canonical picking; authored DOM only. No WebGL,
// room assets, registration, transport, camera session or physical command.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';
import { createApartmentUiOverlayLayer } from '../app/src/home-3d/apartment-ui-overlay-bridge.mjs';

const vendor = new URL('../app/src/vendor/three-0.180.0/three.module.js', import.meta.url).href;
const pickingSource = fs.readFileSync(new URL('../app/src/home-3d/picking.js', import.meta.url), 'utf8');
const imports = /import \* as THREE from 'three';/g;
assert.equal([...pickingSource.matchAll(imports)].length, 1, 'only the actual picking bare import is resolved for Node');
const { createPicking } = await import('data:text/javascript;base64,' + Buffer.from(pickingSource.replace(imports,
  `import * as THREE from ${JSON.stringify(vendor)};`)).toString('base64'));

class AuthoredElement {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document;
    this.children = []; this.parentNode = null; this.style = {}; this.attributes = {};
    this.textContent = ''; this.className = ''; this.clientWidth = 800; this.clientHeight = 600;
  }
  get isConnected() { return this === this.ownerDocument.body || !!this.parentNode?.isConnected; }
  get parentElement() { return this.parentNode; }
  appendChild(child) { child.remove(); this.children.push(child); child.parentNode = this; return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  removeChild(child) { const index = this.children.indexOf(child); if (index >= 0) { this.children.splice(index, 1); child.parentNode = null; } return child; }
  remove() { this.parentNode?.removeChild(this); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  contains(other) { return this === other || this.children.some(child => child.contains(other)); }
  getBoundingClientRect() { return { left: 40, top: 60, width: this.clientWidth, height: this.clientHeight, right: 40 + this.clientWidth, bottom: 60 + this.clientHeight }; }
}

function fixture(overrides = {}) {
  const document = { createElement(tag) { return new AuthoredElement(tag, this); } };
  document.body = document.createElement('body');
  const host = document.createElement('div'); document.body.appendChild(host);
  const camera = new THREE.PerspectiveCamera(60, 800 / 600, .1, 20);
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true); camera.updateProjectionMatrix();
  const apartmentRoot = new THREE.Group(), overlayRoot = new THREE.Group(); apartmentRoot.add(overlayRoot); apartmentRoot.updateMatrixWorld(true);
  const abort = new AbortController(), state = { current: true, disposed: 0, picks: [], projections: [] };
  const options = {
    THREE, camera, apartmentRoot, overlayRoot, host,
    projectWorld: world => {
      state.projections.push(world.clone()); const p = world.clone().project(camera);
      return { x: (p.x * .5 + .5) * host.clientWidth, y: (-p.y * .5 + .5) * host.clientHeight, visible: true };
    },
    pickSurface: (x, y) => { state.picks.push([x, y]); return { point: [1, 2, 3], normal: [0, 0, 1], object: { name: ' Authored plane ' } }; },
    isCurrent: () => state.current, signal: abort.signal, onDispose: () => state.disposed++, ...overrides,
  };
  const layer = createApartmentUiOverlayLayer(options);
  return { document, host, camera, apartmentRoot, overlayRoot, abort, state, options, layer };
}
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-7, `${actual} != ${expected}`);

test('projection transforms canonical local meters once, reuses real Three camera math and never mutates the input', () => {
  const f = fixture();
  f.apartmentRoot.position.set(1, -.5, .25); f.apartmentRoot.rotation.z = .25; f.apartmentRoot.updateMatrixWorld(true);
  const point = Object.freeze([.5, -.5, 0]);
  const world = f.apartmentRoot.localToWorld(new THREE.Vector3(...point)), projected = world.clone().project(f.camera);
  const original = f.apartmentRoot.localToWorld.bind(f.apartmentRoot); let transforms = 0;
  f.apartmentRoot.localToWorld = value => { transforms++; return original(value); };
  const result = f.layer.project(point);
  assert.ok(result?.visible); close(result.x, (projected.x * .5 + .5) * 800); close(result.y, (-projected.y * .5 + .5) * 600);
  assert.equal(transforms, 1); assert.deepEqual(point, [.5, -.5, 0]);
  assert.deepEqual(f.state.projections[0].toArray(), world.toArray()); f.layer.dispose();
});

for (const point of [[0, 0, 11], [0, 0, 9.95], [0, 0, -11], [100, 0, 0], [0, 100, 0], [NaN, 0, 0], [0, Infinity, 0], null, [0, 0], ['0', 0, 0]]) {
  test('projection rejects invalid, behind-camera, near/far or off-viewport point ' + JSON.stringify(point), () => {
    const f = fixture(); assert.equal(f.layer.project(point), null); assert.equal(f.state.projections.length, 0);
    f.layer.dispose();
  });
}

test('anchors retain DOM identity across valid updates, safely render literal labels and withdraw invalid values', () => {
  const f = fixture(), anchor = f.layer.addAnchor({ point_m: [0, 0, 0], label: '<img src="authored.invalid">' });
  assert.ok(anchor); assert.equal(f.layer.update(), true);
  const container = f.host.children[0], marker = container.children[0];
  assert.ok(marker); const labelText = marker.textContent + marker.children.map(child => child.textContent).join('');
  assert.ok(labelText.includes('<img src="authored.invalid">'), 'labels remain literal text, never HTML');
  assert.equal(anchor.update({ point_m: [1, 0, 0], label: 'Changed authored label' }), true);
  assert.equal(container.children[0], marker); assert.equal(f.layer.update(), true);
  assert.equal(anchor.update({ point_m: [NaN, 0, 0] }), false);
  assert.ok(marker.hidden || marker.style.display === 'none' || marker.style.visibility === 'hidden');
  assert.equal(anchor.update({ point_m: [0, 0, 0], label: 'Restored' }), true); assert.equal(f.layer.update(), true);
  assert.equal(container.children[0], marker);
  assert.equal(anchor.dispose(), true); assert.equal(anchor.dispose(), false);
  assert.equal(container.children.length, 0); assert.equal(anchor.update({ label: 'Too late' }), false);
  f.layer.dispose();
});

test('picks delegate exact client coordinates and return copied canonical point/normal plus native surface name', () => {
  const f = fixture(), result = f.layer.pick(440, 360);
  assert.deepEqual(f.state.picks, [[440, 360]]);
  assert.deepEqual(result, { point: [1, 2, 3], normal: [0, 0, 1], surfaceName: 'Authored plane' });
  assert.equal(f.layer.pick(NaN, 360), null); assert.equal(f.layer.pick(440, Infinity), null);
  assert.equal(f.layer.pick(0, 0), null); assert.equal(f.state.picks.length, 1);
  f.layer.dispose();
});

test('surface picking uses the real canonical raycaster and names its actual scan mesh', () => {
  const f = fixture();
  const geometry = new THREE.PlaneGeometry(4, 4), material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
  const scan = new THREE.Mesh(geometry, material); scan.name = 'Authored scan face'; f.apartmentRoot.add(scan); f.apartmentRoot.updateMatrixWorld(true);
  const picking = createPicking(f.camera, f.host);
  f.layer.dispose();
  const layer = createApartmentUiOverlayLayer({ ...f.options, pickSurface: (x, y) => picking.surfaceHit(f.apartmentRoot, [scan], x, y) });
  const result = layer.pick(440, 360); assert.ok(result); result.point.forEach((value, i) => close(value, [0, 0, 0][i]));
  result.normal.forEach((value, i) => close(value, [0, 0, 1][i])); assert.equal(result.surfaceName, 'Authored scan face');
  scan.name = ''; assert.equal(layer.pick(440, 360).surfaceName, 'Scan surface');
  layer.dispose(); geometry.dispose(); material.dispose();
});

test('abort synchronously retires a paused layer and every later method refuses work', () => {
  const f = fixture(), anchor = f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Authored' });
  assert.equal(f.layer.group.parent, f.overlayRoot); assert.ok(f.host.children.length);
  f.abort.abort();
  assert.equal(f.host.children.length, 0); assert.equal(f.layer.group.parent, null); assert.equal(f.state.disposed, 1);
  assert.equal(f.layer.update(), false); assert.equal(f.layer.project([0, 0, 0]), null); assert.equal(f.layer.pick(440, 360), null);
  assert.equal(f.layer.addAnchor({ point_m: [0, 0, 0] }), null); assert.equal(anchor.update({ label: 'Retired' }), false);
  assert.equal(f.layer.dispose(), false); assert.equal(f.state.disposed, 1);
});

test('false currentness retires instead of borrowing a new host or allowing later work', () => {
  const f = fixture(); f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Before handoff' });
  f.state.current = false;
  assert.equal(f.layer.update(), false); assert.equal(f.host.children.length, 0); assert.equal(f.layer.group.parent, null);
  assert.equal(f.state.disposed, 1); assert.equal(f.layer.project([0, 0, 0]), null);
});

test('a throwing captured predicate retires on the next public call', () => {
  let throws = false;
  const f = fixture({ isCurrent: () => { if (throws) throw Error('Authored scope error'); return true; } });
  f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Before predicate failure' }); throws = true;
  assert.equal(f.layer.project([0, 0, 0]), null); assert.equal(f.host.children.length, 0); assert.equal(f.state.disposed, 1);
});

test('scope retirement during a projection callback cannot reattach its anchor', () => {
  let f;
  f = fixture({ projectWorld: () => { f.abort.abort(); return { x: 400, y: 300, visible: true }; } });
  const anchor = f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Authored' });
  assert.equal(f.layer.update(), false); assert.equal(f.host.children.length, 0); assert.equal(f.layer.group.parent, null);
  assert.equal(f.state.disposed, 1); assert.equal(anchor?.update({ label: 'Late' }) ?? false, false);
});

test('a predicate that aborts synchronously and then returns true cannot revive presentation', () => {
  let abortOnCheck = false;
  const f = fixture({ isCurrent: () => { if (abortOnCheck) f.abort.abort(); return true; } });
  f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Before predicate abort' }); abortOnCheck = true;
  assert.equal(f.layer.update(), false); assert.equal(f.layer.project([0, 0, 0]), null);
  assert.equal(f.host.children.length, 0); assert.equal(f.layer.group.parent, null); assert.equal(f.state.disposed, 1);
});

test('projection/pick failure is isolated, a later valid update recovers, and invalid picks never escape', () => {
  let broken = true;
  const f = fixture({ projectWorld: () => { if (broken) throw Error('Authored projection failure'); return { x: 400, y: 300, visible: true }; },
    pickSurface: () => { if (broken) throw Error('Authored pick failure'); return { point: [NaN, 0, 0], normal: [0, 0, 1] }; } });
  const anchor = f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Authored' });
  assert.equal(f.layer.project([0, 0, 0]), null); assert.equal(f.layer.pick(440, 360), null); assert.equal(f.layer.update(), false);
  assert.equal(f.state.disposed, 0); broken = false;
  assert.equal(anchor.update({ label: 'Recovered' }), true); assert.equal(f.layer.update(), true);
  assert.equal(f.layer.pick(440, 360), null); f.layer.dispose();
});

test('disposal detaches only owned containers and group, never disposes borrowed geometry/material, and isolates a throwing notification', () => {
  const f = fixture({ onDispose: () => { throw Error('Authored disposal notification'); } });
  const sibling = new THREE.Group(); f.overlayRoot.add(sibling);
  const geometry = new THREE.BoxGeometry(.1, .1, .1), material = new THREE.MeshBasicMaterial();
  let geometryDisposals = 0, materialDisposals = 0;
  geometry.addEventListener('dispose', () => geometryDisposals++); material.addEventListener('dispose', () => materialDisposals++);
  const borrowed = new THREE.Mesh(geometry, material); f.layer.group.add(borrowed);
  f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Authored' });
  const owned = f.layer.group.children.find(child => child !== borrowed);
  let ownedGeometryDisposals = 0, ownedMaterialDisposals = 0;
  owned.geometry.addEventListener('dispose', () => ownedGeometryDisposals++);
  owned.material.addEventListener('dispose', () => ownedMaterialDisposals++);
  assert.equal(f.layer.dispose(), true); assert.equal(f.layer.dispose(), false);
  assert.equal(f.host.children.length, 0); assert.equal(f.layer.group.parent, null); assert.equal(sibling.parent, f.overlayRoot);
  assert.equal(geometryDisposals, 0); assert.equal(materialDisposals, 0);
  assert.equal(ownedGeometryDisposals, 1); assert.equal(ownedMaterialDisposals, 1);
  geometry.dispose(); material.dispose(); assert.equal(geometryDisposals, 1); assert.equal(materialDisposals, 1);
});

test('required lifetime authorities fail closed and an already aborted signal attaches nothing', () => {
  for (const field of ['isCurrent', 'signal']) {
    const f = fixture(); f.layer.dispose();
    assert.throws(() => createApartmentUiOverlayLayer({ ...f.options, [field]: undefined }), /dependencies_required/);
    assert.equal(f.host.children.length, 0);
  }
  const f = fixture(); f.layer.dispose(); f.abort.abort();
  const before = f.state.disposed, retired = createApartmentUiOverlayLayer(f.options);
  assert.equal(retired.update(), false); assert.equal(retired.dispose(), false);
  assert.equal(f.state.disposed, before + 1); assert.equal(f.host.children.length, 0); assert.equal(retired.group.parent, null);
});

test('a disconnected old host retires and never attaches UI to a replacement host', () => {
  const f = fixture(), replacement = f.document.createElement('div'); f.document.body.appendChild(replacement);
  f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Old host' }); f.host.remove();
  assert.equal(f.layer.update(), false); assert.equal(f.state.disposed, 1); assert.equal(f.layer.group.parent, null);
  assert.equal(f.host.children.length, 0); assert.equal(replacement.children.length, 0);
});

test('one failed anchor update hides only that anchor while sibling presentation still updates', () => {
  const f = fixture({ projectWorld: world => {
    if (world.x < 0) throw Error('Authored one-anchor projection failure');
    return { x: 440, y: 300, visible: true };
  } });
  f.layer.addAnchor({ point_m: [-1, 0, 0], label: 'Failed anchor' });
  f.layer.addAnchor({ point_m: [1, 0, 0], label: 'Current anchor' });
  assert.equal(f.layer.update(), false);
  const [failed, current] = f.host.children[0].children;
  assert.equal(failed.hidden, true); assert.equal(current.hidden, false); assert.equal(current.style.left, '440px');
  assert.equal(f.state.disposed, 0); f.layer.dispose();
});

function projectionWorkFixture() {
  const work = { allocations: 0, clones: 0, viewports: 0, ownerChecks: 0, projections: 0 };
  class ObservedVector3 extends THREE.Vector3 {
    constructor(...args) { super(...args); work.allocations++; }
    clone() { work.clones++; return super.clone(); }
  }
  let f;
  f = fixture({
    THREE: { ...THREE, Vector3: ObservedVector3 },
    isCurrent: () => { work.ownerChecks++; return f ? f.state.current : true; },
    projectWorld: world => {
      work.projections++;
      // Observer math uses the original Three class: it does not contribute
      // to the leaf's constructor/clone counts or mutate the callback input.
      const point = new THREE.Vector3().copy(world).project(f.camera);
      return { x: (point.x * .5 + .5) * f.host.clientWidth,
        y: (-point.y * .5 + .5) * f.host.clientHeight, visible: true };
    },
  });
  const bounds = f.host.getBoundingClientRect.bind(f.host);
  f.host.getBoundingClientRect = () => { work.viewports++; return bounds(); };
  return { ...f, work, resetWork() { for (const key of Object.keys(work)) work[key] = 0; } };
}

for (const count of [0, 1, 4]) {
  test(`steady ${count}-anchor update keeps lifetime admissions and allocates only callback-owned world vectors`, t => {
    const f = projectionWorkFixture();
    for (let index = 0; index < count; index++) {
      assert.ok(f.layer.addAnchor({ point_m: [index * .1, 0, 0], label: `Authored ${index}` }));
    }
    f.resetWork();
    assert.equal(f.layer.update(), true);
    const measured = { ...f.work };
    t.diagnostic(JSON.stringify({ anchors: count, ...measured }));
    // Empty layers still check authority/visibility; occupied layers retain
    // their before-projection, after-callback and after-write admissions.
    assert.equal(measured.viewports, count ? 3 : 1);
    assert.equal(measured.ownerChecks, count ? 3 : 1);
    assert.equal(measured.projections, count);
    assert.equal(measured.allocations, count, 'depth and clip math must reuse private scratch, while each callback receives its own world vector');
    assert.equal(measured.clones, 0, 'projection math must not create transient depth/clip vector clones');
    f.layer.dispose();
  });
}

test('nested projection keeps outer math and each callback world vector independent and stable', () => {
  let f, nest = false, nested;
  const received = [];
  f = fixture({ projectWorld: world => {
    received.push({ world, expected: world.toArray() });
    if (nest) { nest = false; nested = f.layer.project([1, .5, 0]); }
    const point = new THREE.Vector3().copy(world).project(f.camera);
    return { x: (point.x * .5 + .5) * f.host.clientWidth,
      y: (-point.y * .5 + .5) * f.host.clientHeight, visible: true };
  } });
  nest = true;
  const outer = f.layer.project([0, 0, 0]);
  assert.deepEqual(outer, { x: 400, y: 300, visible: true });
  const expected = new THREE.Vector3(1, .5, 0).project(f.camera);
  close(nested.x, (expected.x * .5 + .5) * 800);
  close(nested.y, (-expected.y * .5 + .5) * 600);
  assert.equal(received.length, 2);
  assert.notEqual(received[0].world, received[1].world);
  for (const item of received) assert.deepEqual(item.world.toArray(), item.expected);
  // A later call must not reuse and overwrite an earlier callback argument.
  f.layer.project([-.5, -.5, 0]);
  for (const item of received) assert.deepEqual(item.world.toArray(), item.expected);
  f.layer.dispose();
});

for (const [name, mutate] of [
  ['hidden host', f => { f.host.hidden = true; }],
  ['inert host', f => { f.host.inert = true; }],
  ['disconnected host', f => { f.host.remove(); }],
  ['zero-width host', f => { f.host.clientWidth = 0; }],
  ['zero-height host', f => { f.host.clientHeight = 0; }],
  ['host without layout boxes', f => { f.host.getClientRects = () => []; }],
  ['retired owner', f => { f.state.current = false; }],
]) {
  test(`projection callback ${name} change retires before any anchor can be revived`, () => {
    let f, armed = false;
    f = fixture({ projectWorld: world => {
      const point = new THREE.Vector3().copy(world).project(f.camera);
      if (armed) mutate(f);
      return { x: (point.x * .5 + .5) * 800, y: (-point.y * .5 + .5) * 600, visible: true };
    } });
    const anchor = f.layer.addAnchor({ point_m: [0, 0, 0], label: 'Before callback mutation' });
    assert.ok(anchor);
    armed = true;
    assert.equal(f.layer.update(), false);
    assert.equal(f.host.children.length, 0);
    assert.equal(f.layer.group.parent, null);
    assert.equal(f.state.disposed, 1);
    assert.equal(anchor.update({ label: 'Late update' }), false);
    assert.equal(f.layer.project([0, 0, 0]), null);
    assert.equal(f.layer.dispose(), false);
  });
}

test('positive host resize during projection stays live and the next call uses current dimensions', () => {
  let f, resize = false;
  f = fixture({ projectWorld: world => {
    if (resize) { resize = false; f.host.clientWidth = 640; f.host.clientHeight = 480; }
    const point = new THREE.Vector3().copy(world).project(f.camera);
    return { x: (point.x * .5 + .5) * f.host.clientWidth,
      y: (-point.y * .5 + .5) * f.host.clientHeight, visible: true };
  } });
  resize = true;
  assert.deepEqual(f.layer.project([0, 0, 0]), { x: 320, y: 240, visible: true });
  assert.equal(f.state.disposed, 0);
  assert.deepEqual(f.layer.project([0, 0, 0]), { x: 320, y: 240, visible: true });
  f.layer.dispose();
});
