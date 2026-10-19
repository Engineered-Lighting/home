import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createDeskTargetCapture, sealRegisteredDeskTarget, validateRegisteredDeskTarget } from './home-3d/registered-desk-target.mjs';
import { calculateDeskAims } from '../../tools/den-room/desk-target.mjs';

// Execute the unchanged plain-JS handler/effect block from the actual JSX host,
// following home-apartment-journey.test.mjs's existing handler seam. No React,
// browser, WebGL, assets, network, storage or device service is started.
const host = readFileSync(new URL('./home-apartment.jsx', import.meta.url), 'utf8');
const Core = createRequire(import.meta.url)('./home-equipment-registration.js');
const start = host.indexOf('  const deskTargetContextCurrent = ');
const end = host.indexOf('  const restoreVirtualAimHistory = ', start);
assert.ok(start >= 0 && end > start, 'the actual desk-target handler/effect block exists');
const handlerSource = host.slice(start, end);
const copy = value => JSON.parse(JSON.stringify(value));

class Surface {
  constructor() { this.listeners = new Map(); this.captured = new Set(); }
  addEventListener(name, callback, capture) { this.listeners.set(name, { callback, capture }); }
  removeEventListener(name, callback, capture) {
    const old = this.listeners.get(name);
    if (old?.callback === callback && old.capture === capture) this.listeners.delete(name);
  }
  setPointerCapture(id) { this.captured.add(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
  emit(name, values = {}) {
    const event = { pointerId: 1, button: 0, isPrimary: true, clientX: 10, clientY: 10,
      target: { closest: () => null }, preventDefault() { this.prevented = true; },
      stopImmediatePropagation() { this.stopped = true; }, ...values };
    this.listeners.get(name)?.callback(event); return event;
  }
}

function fixture(registration = null) {
  const element = new Surface(), windowTarget = new Surface();
  const state = { selection: null, targets: [], downloads: [], revoked: [], timers: [], effects: [], aimCanceled: 0, notices: [] };
  const binding = registration?.binding || { content_revision: 'authored-room', registration_version: 2,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  const document = registration || { revision: 4, digest: 'd'.repeat(64), binding };
  const corners = [[1, 2, 1], [3, 2, 1], [3, 3, 1], [1, 3, 1]];
  let hitIndex = 0;
  const engine = { renderer: { domElement: element }, sceneIdentity: { siteId: 'victoria',
    contentRevision: binding.content_revision, registrationVersion: binding.registration_version, frameDigest: binding.frame_digest },
    overlay: { createDeskTargetCapture, sealRegisteredDeskTarget, setTargets: values => state.targets.push(copy(values)) },
    displaySurfaceHit: () => ({ point: corners[hitIndex++], normal: [0, 0, 1] }) };
  windowTarget.HomeEquipmentRegistration = Core;
  const env = { open: true, phase: 'ready', editing: false, calibCam: false, liveOn: false,
    virtualSpotlightsVisible: true, spatialScale: 'apartment', rehearsalIsolated: false,
    engineRef: { current: engine }, workspaceAuthorityRef: { current: { key: 'authored-authority' } },
    apartmentAuthorityKey: 'authored-authority', virtualFixtureDocument: document,
    virtualFixtureBindingKey: JSON.stringify(binding), deskTargetCaptureRef: { current: null }, denDeskAimPreviewRef: { current: null },
    virtualAimGestureRef: { current: { cancel: () => state.aimCanceled++ } }, model: { targets: [{ id: 'original', pos: [0, 0, 0] }] },
    setDeskTargetSelection: next => { state.selection = typeof next === 'function' ? next(state.selection) : next; },
    setVirtualAimNotice: value => state.notices.push(value),
    useEffect: (effect, dependencies) => state.effects.push({ effect, dependencies }), window: windowTarget,
    document: { createElement: tag => { assert.equal(tag, 'a'); return { click() { state.downloads.push({ href: this.href, filename: this.download }); } }; } },
    Blob, URL: { createObjectURL: blob => { state.blob = blob; return 'blob:authored-desk'; }, revokeObjectURL: url => state.revoked.push(url) },
    setTimeout: (callback, milliseconds) => state.timers.push({ callback, milliseconds }) };
  function render() {
    const scope = { ...env, deskTargetSelection: state.selection };
    return new Function(...Object.keys(scope), `${handlerSource}\nreturn { deskTargetContextCurrent, startDeskTargetSelection, cancelDeskTargetSelection, saveDeskTargetSelection };`)(...Object.values(scope));
  }
  function click() { element.emit('pointerdown'); return element.emit('pointerup'); }
  return { env, state, element, windowTarget, engine, binding, document, corners, render, click };
}

function displayMeshFixture() {
  const manifest = { siteId: 'victoria', revision: 'authored-display-room', registrationVersion: 2,
    assets: { 'frame.json': { sha256: 'b'.repeat(64) }, 'collision.glb': { sha256: 'c'.repeat(64) },
      'mesh.glb': { sha256: 'e'.repeat(64) } } };
  const binding = { content_revision: manifest.revision, registration_version: manifest.registrationVersion,
    manifest_digest: Core.digest(manifest), frame_digest: manifest.assets['frame.json'].sha256,
    canonical_collision_digest: manifest.assets['collision.glb'].sha256 };
  const f = fixture({ revision: 4, digest: 'd'.repeat(64), binding });
  const surface = { asset_name: 'mesh.glb', asset_sha256: manifest.assets['mesh.glb'].sha256,
    manifest_digest: binding.manifest_digest };
  let resolve, current = null, prepares = 0, hits = 0, legacyHits = 0;
  const pending = new Promise(done => { resolve = done; });
  f.engine.displaySurfaceHit = () => { legacyHits++; return null; }; // authored simplified-proxy hole
  f.engine.prepareDeskTargetSurface = async supplied => {
    assert.deepEqual(supplied, binding); prepares++;
    const value = await pending; current = copy(value); return copy(value);
  };
  f.engine.deskTargetSurfaceReference = supplied => {
    assert.deepEqual(supplied, binding); return copy(current);
  };
  f.engine.deskTargetSurfaceHit = (x, y, supplied) => {
    assert.deepEqual([x, y], [10, 10]); assert.deepEqual(supplied, binding);
    assert.ok(current, 'detailed mesh must be prepared before a surface read');
    const point = f.corners[hits++]; assert.ok(point, 'only four successful mesh reads');
    return { point: copy(point), normal: [0, 0, 1], surface_ref: copy(current) };
  };
  return { ...f, manifest, surface, resolve: () => resolve(copy(surface)),
    changeSource: value => { current = copy(value); }, counts: () => ({ prepares, hits, legacyHits }) };
}

const microtasks = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };

test('actual host prepares the registered display mesh before four corners and saves independently validated version 2', async () => {
  const f = displayMeshFixture();
  const pending = f.render().startDeskTargetSelection();
  try {
    assert.equal(f.counts().prepares, 1, 'the actual host must request its detailed registered surface');
    assert.equal(f.element.listeners.size, 0, 'pending mesh preparation cannot admit pointer capture');
    assert.equal(f.state.downloads.length, 0);
    f.resolve(); await pending; await microtasks();
    assert.equal(f.state.selection.status, 'selecting');
    for (let index = 0; index < 4; index++) f.click();
    assert.equal(f.state.selection.status, 'complete');
    assert.equal(f.counts().hits, 4); assert.equal(f.counts().legacyHits, 0, 'a proxy hole is not substituted for the detailed surface');
    f.render().saveDeskTargetSelection(); assert.equal(f.state.downloads.length, 0);
    f.state.selection = { ...f.state.selection, confirmed: true }; f.render().saveDeskTargetSelection();
    assert.equal(f.state.downloads.length, 1);
    const record = JSON.parse(await f.state.blob.text());
    assert.equal(record.schema, 'home-registered-desk-target-2'); assert.equal(record.version, 2);
    assert.deepEqual(record.selection.surface_ref, f.surface);
    assert.equal(record.selection.method, 'owner_selected_display_mesh');
    assert.deepEqual(record.selection.raw_corners, f.corners.map(point => ({ point_m: point, normal_unit: [0, 0, 1] })));
    assert.deepEqual(record.target.vertices_m, f.corners);
    assert.deepEqual(validateRegisteredDeskTarget(record, { binding: f.binding,
      equipment_ref: { revision: f.document.revision, digest: f.document.digest }, manifest: f.manifest }, Core.digest), record);
    assert.throws(() => validateRegisteredDeskTarget(record, { binding: f.binding,
      equipment_ref: { revision: f.document.revision, digest: f.document.digest } }, Core.digest), /manifest/);
    assert.equal(record.physical_commands, false); assert.equal(record.qualification, null);
  } finally { f.render().cancelDeskTargetSelection(); }
});

test('actual host cancellation retires a pending detailed mesh load before listeners markers or downloads can return', async () => {
  const f = displayMeshFixture(), pending = f.render().startDeskTargetSelection();
  assert.equal(f.counts().prepares, 1); assert.equal(f.element.listeners.size, 0);
  f.render().cancelDeskTargetSelection();
  const markers = f.state.targets.length, downloads = f.state.downloads.length;
  f.resolve(); await pending; await microtasks();
  assert.equal(f.element.listeners.size, 0); assert.equal(f.windowTarget.listeners.size, 0);
  assert.equal(f.state.targets.length, markers, 'late preparation cannot publish markers after cancellation');
  assert.equal(f.state.downloads.length, downloads); assert.equal(f.state.selection, null);
  f.render().saveDeskTargetSelection(); assert.equal(f.state.downloads.length, 0);
  assert.equal(f.counts().hits, 0); assert.equal(f.counts().legacyHits, 0);
});

test('actual host retires changed detailed mesh identity before the fourth corner can complete or save', async () => {
  const f = displayMeshFixture(), pending = f.render().startDeskTargetSelection();
  try {
    assert.equal(f.counts().prepares, 1); f.resolve(); await pending; await microtasks();
    for (let index = 0; index < 3; index++) f.click();
    assert.equal(f.state.selection.corners.length, 3); assert.equal(f.counts().hits, 3);
    f.changeSource({ ...f.surface, asset_sha256: 'f'.repeat(64) });
    f.click();
    assert.equal(f.state.selection.status, 'retired'); assert.deepEqual(f.state.selection.corners, []);
    assert.equal(f.counts().hits, 3, 'the foreign fourth source never becomes a retained point');
    f.render().saveDeskTargetSelection(); assert.equal(f.state.downloads.length, 0);
    assert.equal(f.counts().legacyHits, 0);
  } finally { f.render().cancelDeskTargetSelection(); }
});

test('actual host handlers select four scan corners and save the exact confirmed private target', async () => {
  const f = fixture(); const initial = f.render(); initial.startDeskTargetSelection();
  assert.equal(f.state.aimCanceled, 1); assert.equal(f.state.selection.status, 'selecting');
  for (let index = 0; index < 4; index++) assert.equal(f.click().stopped, true);
  assert.equal(f.state.selection.status, 'complete'); assert.equal(f.state.selection.confirmed, false);
  f.render().saveDeskTargetSelection(); assert.equal(f.state.downloads.length, 0, 'whole-desktop confirmation is required');
  f.state.selection = { ...f.state.selection, confirmed: true }; f.render().saveDeskTargetSelection();
  assert.deepEqual(f.state.downloads, [{ href: 'blob:authored-desk', filename: 'victoria-den-desk-target.json' }]);
  const raw = await f.state.blob.text(); assert.ok(raw.endsWith('\n'));
  const record = JSON.parse(raw);
  assert.deepEqual(record.target.vertices_m, f.corners);
  validateRegisteredDeskTarget(record, { binding: f.binding, equipment_ref: { revision: f.document.revision, digest: f.document.digest } }, Core.digest);
  assert.equal(record.physical_commands, false); assert.equal(record.qualification, null);
  assert.equal(f.state.selection.status, 'saved');
  assert.equal(f.state.timers.length, 1); assert.equal(f.state.timers[0].milliseconds, 1000);
  f.state.timers[0].callback(); assert.deepEqual(f.state.revoked, ['blob:authored-desk']);
  f.render().cancelDeskTargetSelection();
  assert.equal(f.element.listeners.size, 0); assert.equal(f.windowTarget.listeners.size, 0);
  assert.deepEqual(f.state.targets.at(-1), f.env.model.targets);
});

test('actual start/save fences refuse mode, engine, authority and equipment changes', () => {
  for (const changes of [{ open: false }, { phase: 'loading' }, { editing: true }, { calibCam: true }, { liveOn: true },
    { virtualSpotlightsVisible: false }, { spatialScale: 'world' }, { rehearsalIsolated: true }]) {
    const f = fixture(); Object.assign(f.env, changes); f.render().startDeskTargetSelection();
    assert.equal(f.env.deskTargetCaptureRef.current, null, JSON.stringify(changes)); assert.equal(f.element.listeners.size, 0);
  }
  for (const change of ['engine', 'authority', 'equipment']) {
    const f = fixture(); f.render().startDeskTargetSelection(); for (let index = 0; index < 4; index++) f.click();
    f.state.selection = { ...f.state.selection, confirmed: true };
    if (change === 'engine') f.env.engineRef.current = { ...f.engine };
    if (change === 'authority') f.env.workspaceAuthorityRef.current.key = 'different-authority';
    if (change === 'equipment') f.env.virtualFixtureDocument = { ...f.document, revision: 5 };
    f.render().saveDeskTargetSelection(); assert.equal(f.state.downloads.length, 0, change);
    f.render().cancelDeskTargetSelection(); assert.equal(f.element.listeners.size, 0, change);
  }
});

test('actual cleanup releases a held pointer and cannot write markers into a replacement scope', () => {
  const f = fixture(); f.render().startDeskTargetSelection(); f.click(); f.element.emit('pointerdown');
  const cleanup = f.state.effects[0].effect(); const before = f.state.targets.length;
  f.env.engineRef.current = { ...f.engine }; f.env.workspaceAuthorityRef.current.key = 'new-scope';
  cleanup();
  assert.equal(f.element.captured.size, 0); assert.equal(f.element.listeners.size, 0); assert.equal(f.windowTarget.listeners.size, 0);
  assert.equal(f.env.deskTargetCaptureRef.current, null); assert.equal(f.state.selection, null);
  assert.equal(f.state.targets.length, before, 'old overlay receives no cleanup marker update after scope replacement');
});

test('both actual overlay Escape handlers cancel desk selection before closing or virtual aim', () => {
  // Both overlay layers route Escape through the shared apartment handler.
  for (const key of ['apartment', 'apartment-embedded']) {
    const layer = host.indexOf(`key: "${key}"`);
    const escape = host.indexOf('onEscape:', layer);
    assert.ok(layer >= 0 && escape > layer && host.slice(escape, host.indexOf(String.fromCharCode(10), escape)).includes('handleApartmentEscape'), key);
  }
  const from = host.indexOf('const handleApartmentEscape = () => {');
  const desk = host.indexOf('if (deskTargetCaptureRef.current) {', from);
  const aim = host.indexOf('if (virtualAimGestureRef.current?.readback().active) {', from);
  assert.ok(from >= 0 && desk > from && aim > desk, 'desk selection owns Escape before virtual aim');
  assert.ok(host.slice(desk, aim).includes('cancelDeskTargetSelection();'));
});

test('the complete actual Apartment JSX compiles with the existing pinned React compiler', () => {
  const Babel = createRequire(import.meta.url)('./vendor/babel-7.29.0/babel.min.js');
  const compiled = Babel.transform(host, { presets: ['react'], filename: 'home-apartment.jsx', sourceMaps: false });
  assert.ok(compiled.code.includes('Select desk surface'));
});

test('actual checkbox confirmation and Save survive descendant focus blur and download focus loss', async () => {
  const f=fixture();f.render().startDeskTargetSelection();for(let i=0;i<4;i++)f.click();
  assert.equal(f.state.selection.status,'complete');
  // Window capture receives the start button's non-bubbling blur when the user
  // focuses the checkbox. Execute that transition before the real JSX handler.
  f.windowTarget.emit('blur',{target:{tagName:'BUTTON'}});
  assert.equal(f.state.selection.status,'complete');
  const marker='onChange={event => setDeskTargetSelection(value => ({ ...value, confirmed: event.target.checked }))}';
  // Main renders the confirmation in both the shell panel and the legacy aside.
  assert.ok(host.split(marker).length>=2);
  const expression=marker.slice('onChange={'.length,-1);
  const confirm=new Function('setDeskTargetSelection',`return (${expression});`)(f.env.setDeskTargetSelection);
  confirm({target:{checked:true}});
  assert.equal(f.state.selection.confirmed,true);
  f.render().saveDeskTargetSelection();
  assert.equal(f.state.downloads.length,1);assert.equal(f.state.selection.status,'saved');
  f.windowTarget.emit('blur',{target:f.windowTarget});
  assert.equal(f.state.selection.status,'saved');
  assert.deepEqual(JSON.parse(await f.state.blob.text()).target.vertices_m,f.corners);
  f.render().cancelDeskTargetSelection();
  assert.equal(f.state.selection,null);assert.equal(f.windowTarget.listeners.size,0);
});

test('actual selecting status explains rejected clicks while retaining the first desk corner', () => {
  const section = host.indexOf('<section aria-label="Desk target"');
  const from = host.indexOf('<p role="status"', section), to = host.indexOf('</p>', from) + 4;
  assert.ok(section >= 0 && from > section && to > from, 'the actual desk selection status paragraph exists');
  const Babel = createRequire(import.meta.url)('./vendor/babel-7.29.0/babel.min.js');
  const compiled = Babel.transform(`const renderStatus = (deskTargetSelection) => (${host.slice(from, to)});`,
    { presets: ['react'], filename: 'actual-desk-selection-status.jsx', sourceMaps: false }).code;
  const React = { createElement: (type, props, ...children) => ({ type, props, children }) };
  const render = new Function('React', `${compiled}\nreturn renderStatus;`)(React);
  const text = value => value == null || value === false ? '' : Array.isArray(value)
    ? value.map(text).join('') : typeof value === 'object' ? text(value.children) : String(value);
  const cases = [
    { name: 'no-hit', reason: 'registered_surface_unavailable', message: 'No registered scan surface at that point.',
      reject: f => { f.engine.displaySurfaceHit = () => null; f.click(); } },
    { name: 'drag', reason: 'drag_is_not_a_corner', message: 'Drag ignored. Click without moving.',
      reject: f => { f.element.emit('pointerdown'); f.element.emit('pointermove', { clientX: 16 }); f.element.emit('pointerup'); } },
    { name: 'invalid-surface', reason: 'registered_desk_surface_corner_required', message: 'This surface cannot be used as a desktop corner.',
      reject: f => { f.engine.displaySurfaceHit = () => ({ point: [2, 2, 1], normal: [0, 0, -1] }); f.click(); } },
  ];
  for (const row of cases) {
    const f = fixture(); f.render().startDeskTargetSelection(); f.click();
    const first = copy(f.state.selection.corners);
    try {
      row.reject(f);
      assert.equal(f.state.selection.status, 'selecting', row.name);
      assert.equal(f.state.selection.reason, row.reason, row.name);
      assert.deepEqual(f.state.selection.corners, first, row.name);
      const paragraph = render(f.state.selection), message = text(paragraph);
      assert.equal(paragraph.props.role, 'status', row.name);
      assert.ok(message.includes('1/4'), `retained corner count remains visible: ${row.name}`);
      assert.ok(message.includes(row.message), `visible rejection feedback for ${row.name}: ${message}`);
    } finally { f.render().cancelDeskTargetSelection(); }
  }
});

test('actual host distinguishes missing collision, an empty ray and an unselected raw hit only after failure', () => {
  const section = host.indexOf('<section aria-label="Desk target"');
  const from = host.indexOf('<p role="status"', section), to = host.indexOf('</p>', from) + 4;
  assert.ok(section >= 0 && from > section && to > from);
  const Babel = createRequire(import.meta.url)('./vendor/babel-7.29.0/babel.min.js');
  const compiled = Babel.transform(`const renderStatus = (deskTargetSelection) => (${host.slice(from, to)});`,
    { presets: ['react'], filename: 'actual-desk-failure-status.jsx', sourceMaps: false }).code;
  const React = { createElement: (type, props, ...children) => ({ type, props, children }) };
  const render = new Function('React', `${compiled}\nreturn renderStatus;`)(React);
  const text = value => value == null || value === false ? '' : Array.isArray(value)
    ? value.map(text).join('') : typeof value === 'object' ? text(value.children) : String(value);
  const rows = [
    { name: 'collision-absent', collision: false, hits: [], calls: 0,
      reason: 'registered_collision_unavailable', message: 'Registered scan geometry is not available yet.' },
    { name: 'raw-empty', collision: true, hits: [], calls: 1,
      reason: 'registered_surface_no_hit', message: 'No registered scan triangle at that point.' },
    { name: 'raw-hit-unselected', collision: true, hits: [{ point: { x: 1, y: 2, z: 0 } }], calls: 1,
      reason: 'registered_surface_raw_hit_unselected',
      message: 'Scan geometry is under this point, but the displayed surface could not be selected.' },
  ];
  for (const row of rows) {
    const f = fixture(), collision = { name: 'authored-collision-proxy' }; let calls = 0;
    f.engine.modes = { getCollision: () => row.collision ? collision : null };
    f.engine.picking = { pick: (objects, x, y) => {
      calls++; assert.deepEqual(objects, [collision]); assert.equal(x, 10); assert.equal(y, 10);
      return row.hits;
    } };
    f.render().startDeskTargetSelection(); f.click();
    assert.equal(calls, 0, 'a successful surface pick never issues the diagnostic ray');
    const first = copy(f.state.selection.corners);
    try {
      f.engine.displaySurfaceHit = () => null; f.click();
      assert.equal(f.state.selection.status, 'selecting', row.name);
      assert.deepEqual(f.state.selection.corners, first, row.name);
      assert.equal(f.state.selection.reason, row.reason, row.name);
      assert.equal(calls, row.calls, row.name);
      const paragraph = render(f.state.selection), message = text(paragraph);
      assert.equal(paragraph.props.role, 'status');
      assert.ok(message.includes('1/4'), row.name);
      assert.ok(message.includes(row.message), `visible diagnostic reason: ${row.name}: ${message}`);
    } finally { f.render().cancelDeskTargetSelection(); }
  }
});

test('the actual Home download feeds the actual four-fixture aim consumer without a camera', async () => {
  const binding = { content_revision: 'authored-linked-room', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  const references = { frame: 'z_up_metric_floor0', units: 'm', confirmed: true,
    provenance: { kind: 'authored_test' }, floor: { id: 'floor', normal: [0,0,1], offset_m: 0 },
    walls: [[1,0,0],[0,1,0],[-1,0,-6],[0,-1,-6]].map(([x,y,offset_m],index) =>
      ({ id: 'wall-'+index, label: 'Authored wall '+index, normal_xy: [x,y], offset_m })) };
  let model = Core.toModel(Core.createDocument(binding,references));
  model = Core.addSocket(Core.addSocket(model));
  for (let index = 1; index <= 4; index++) model = Core.updateModelEquipment(model,'victoria-den-mount-0'+index, {
    physical_kind: 'socket', mount_type: 'ceiling', measurements: {
      wall_distances: [{ wall_id: 'wall-0', distance_m: index % 2 ? 1 : 3 },
        { wall_id: 'wall-1', distance_m: index <= 2 ? 1 : 4 }],
      height_above_floor_m: 3, method: 'laser', uncertainty_m: .005 } });
  const registration = Core.toDocument(model);registration.revision = 2;
  const unsigned = { ...registration };delete unsigned.digest;registration.digest = Core.digest(unsigned);
  assert.equal(Core.validateDocument(registration).ok,true);
  const f = fixture(registration);f.render().startDeskTargetSelection();
  for (let index = 0; index < 4; index++) f.click();
  f.state.selection = { ...f.state.selection, confirmed: true };f.render().saveDeskTargetSelection();
  const record = JSON.parse(await f.state.blob.text()), result = calculateDeskAims(record,registration);
  assert.equal(result.target_ref.digest,record.digest);
  assert.deepEqual(result.target.vertices_m,f.corners);
  assert.equal(result.fixtures.filter(row => row.status === 'calculated').length,4);
  assert.deepEqual(result.equipment_ref,{ revision: 2,digest: registration.digest });
  assert.equal(result.qualification,null);assert.equal(result.allocation,null);
  assert.equal(result.physical_commands,false);
  f.render().cancelDeskTargetSelection();
});

