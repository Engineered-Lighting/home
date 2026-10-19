// Actual Apartment selection callbacks/effects and the real direct-aim owner,
// with authored event/engine adapters. No React or full Home mount is claimed.
// No WebGL, room, transport, equipment save, camera or model session is opened.
// Native event generation, responsive layout and home-return tab behavior
// belong to the separately mounted full-Home regression.

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createDirectAimGestureController } from '../app/src/home-3d/direct-aim-gesture.mjs';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE_PATH = 'app/src/home-apartment.jsx';
const babel = require(path.join(ROOT, 'app/src/vendor/babel-7.29.0/babel.min.js'));
const copy = value => JSON.parse(JSON.stringify(value));

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}

function actualSource(source) {
  const ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
  const owners = ast.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === 'HomeApartmentLiveView');
  assert.equal(owners.length, 1, 'one actual Apartment owner');
  const owner = owners[0], text = node => source.slice(node.start, node.end), declarations = new Map();
  for (const statement of owner.body.body) {
    if (statement.type !== 'VariableDeclaration') continue;
    for (const declaration of statement.declarations) {
      if (declaration.id.type === 'Identifier') declarations.set(declaration.id.name, declaration);
    }
  }
  const effects = owner.body.body.filter(node => node.type === 'ExpressionStatement'
    && node.expression?.callee?.name === 'useEffect').map(node => node.expression.arguments[0]);
  const findEffect = signature => {
    const found = effects.filter(node => text(node).includes(signature));
    assert.equal(found.length, 1, 'one actual effect: ' + signature); return found[0];
  };
  const inspectors = [];
  walk(owner, node => {
    if (node.type === 'JSXOpeningElement' && node.name?.name === 'AptUnifiedVirtualInspector') inspectors.push(node);
  });
  assert.equal(inspectors.length, 1, 'one actual Victoria inspector callsite');
  const listSelect = inspectors[0].attributes.find(node => node.name?.name === 'onSelect')?.value?.expression;
  assert.ok(listSelect, 'actual inspector owns the selection callback');

  // Install actual callback dependencies, not copies of their decision logic.
  // Evaluate the actual viewport/lifecycle captures once per authored mount.
  function capture(name, context) {
    const initializer = declarations.get(name)?.init;
    assert.ok(initializer, 'actual selection capture exists: ' + name);
    context[name] = vm.runInContext(text(initializer), context, { timeout: 1000 });
  }
  function callback(node, context, installing = new Set()) {
    if (node.type === 'Identifier' && !Object.prototype.hasOwnProperty.call(context, node.name)) {
      const init = declarations.get(node.name)?.init;
      assert.ok(init, 'actual callback declaration exists: ' + node.name);
      const fn = init.type === 'CallExpression' && init.callee?.name === 'useCallback' ? init.arguments[0] : init;
      context[node.name] = callback(fn, context, installing);
    }
    walk(node, child => {
      if (child.type !== 'CallExpression' || child.callee?.type !== 'Identifier') return;
      const name = child.callee.name;
      if (Object.prototype.hasOwnProperty.call(context, name) || installing.has(name)) return;
      const declaration = declarations.get(name), init = declaration?.init;
      let fn = init;
      if (init?.type === 'CallExpression' && init.callee?.name === 'useCallback') fn = init.arguments[0];
      if (!['ArrowFunctionExpression', 'FunctionExpression'].includes(fn?.type)) return;
      installing.add(name); context[name] = callback(fn, context, installing); installing.delete(name);
    });
    return vm.runInContext('(' + text(node) + ')', context, { timeout: 1000 });
  }

  return { text, capture, callback, listSelect,
    gestureEffect: findEffect('overlay.createVirtualFixtureAimGesture'),
    keyboardEffect: findEffect('const semanticCycleFromCard'),
    echoPickEffect: findEffect('engine.overlay.setTargetHover?.(targetId'),
    emptyClickEffect: findEffect('const fixtureAt = event =>'),
  };
}

class AuthoredTarget {
  constructor(tag = 'div', parent = null) {
    this.tagName = tag.toUpperCase(); this.parentElement = parent; this.style = {};
    this.listeners = new Map(); this.attributes = new Map(); this.captures = new Set();
    this.isContentEditable = false; this.isConnected = true;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  hasAttribute(name) { return this.attributes.has(name); }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) {
      for (const part of selector.split(',')) {
        const name = part.trim(), attribute = /^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/.exec(name);
        if (name.toUpperCase() === node.tagName || attribute && node.hasAttribute(attribute[1])
          && (attribute[2] === undefined || node.getAttribute(attribute[1]) === attribute[2])) return node;
      }
    }
    return null;
  }
  contains(node) { for (; node; node = node.parentElement) if (node === this) return true; return false; }
  addEventListener(type, callback, options) {
    const capture = options === true || options?.capture === true;
    const rows = this.listeners.get(type) || []; rows.push({ callback, capture }); this.listeners.set(type, rows);
  }
  removeEventListener(type, callback, options) {
    const capture = options === true || options?.capture === true;
    this.listeners.set(type, (this.listeners.get(type) || []).filter(row => row.callback !== callback || row.capture !== capture));
  }
  setPointerCapture(id) { this.captures.add(id); }
  hasPointerCapture(id) { return this.captures.has(id); }
  releasePointerCapture(id) { this.captures.delete(id); }
}

// An explicit capture/bubble adapter, not a claim about browser-generated
// compatibility click behavior. Tests send that final click deliberately;
// real browser generation belongs to the integration matrix above.
function emit(target, type, detail = {}) {
  const event = { type, target, button: 0, isPrimary: true, pointerId: 1,
    clientX: 100, clientY: 100, key: '', ctrlKey: false, metaKey: false, altKey: false,
    shiftKey: false, repeat: false, defaultPrevented: false, ...detail,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    stopImmediatePropagation() { this.stopped = true; this.immediate = true; } };
  const chain = []; for (let node = target; node; node = node.parentElement) chain.push(node);
  for (const [nodes, capture] of [[chain.slice().reverse(), true], [chain, false]]) {
    for (const node of nodes) {
      event.currentTarget = node;
      for (const row of (node.listeners.get(type) || []).slice()) {
        if (row.capture === capture) row.callback(event);
        if (event.immediate) break;
      }
      if (event.stopped) return event;
    }
  }
  return event;
}

function fixture(actual, { site = 'victoria' } = {}) {
  const window = new AuthoredTarget('window'), host = new AuthoredTarget('div', window), canvas = new AuthoredTarget('canvas', host);
  const ids = ['authored-vic-3', 'authored-vic-1', 'authored-vic-4', 'authored-vic-2'];
  // This is the existing five-field descriptor binding, with authored hashes.
  const binding = { content_revision: 'authored-victoria-r1', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  const document = { binding, revision: 7, digest: 'd'.repeat(64) };
  const descriptors = new Map(ids.map((id, index) => [id, { schema: 'home-virtual-fixture-interaction-1',
    generation: 1, fixture_id: id, mount_id: 'authored-mount-' + index,
    binding: copy(binding), equipment_revision: 7, equipment_digest: document.digest,
    display_anchor_m: [index, 0, 2], display_origin_m: [index, 0, 1.86], horizontal_world_reference: [0, 1, 0],
    origin_role: 'mount_anchor', optical_origin_m: null,
    state: { spotlight: { on: false, brightness: .5, color_temp_kelvin: 3000 },
      downward: { on: false, brightness: .5, color_temp_kelvin: 3000 }, pan_deg: 0, tilt_deg: 45,
      radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, on: false, brightness: .5, color_temp_kelvin: 3000 })) },
    target: null, truth_role: 'virtual_preview', physical_commands: false }]));
  const state = { selectedSite: site, selectedId: '', open: false, channel: 'spotlight', notice: '',
    calls: [], services: [], hit: null, shellReveals: 0, descriptorOrder: ids.slice(), visible: true,
    previewRevision: 7, previewDigest: document.digest, previewStatus: 'ready', pickErrorOnce: false,
    elementAtPoint: canvas, pointLookups: [] };
  const inputDocument = { elementFromPoint(x, y) { state.pointLookups.push([x, y]); return state.elementAtPoint; } };
  host.ownerDocument = inputDocument; canvas.ownerDocument = inputDocument;
  const snapshot = () => state.descriptorOrder.map(id => ({ fixture_id: id, label: id, state: copy(descriptors.get(id).state) }));
  const overlay = {
    virtualFixtureInteractionDescriptor: id => descriptors.has(id) ? copy(descriptors.get(id)) : null,
    virtualFixturesReadback: () => ({ status: state.previewStatus, fixture_count: ids.length,
      revision: state.previewRevision, digest: state.previewDigest, visible: state.visible, fixtures: snapshot() }),
    setVirtualFixtureSelected: id => { state.calls.push(['overlay-select', id]); return overlay.virtualFixtureInteractionDescriptor(id); },
    virtualFixturePickObjects: () => [], pickObjects: () => [], targetPickObjects: () => [],
    setHover() {}, setTargetHover() {},
    aimVirtualFixtureAtTarget: (id, target) => { descriptors.get(id).target = copy(target); return { ok: true, descriptor: overlay.virtualFixtureInteractionDescriptor(id) }; },
    restoreVirtualFixtureAim: (id, before) => { descriptors.set(id, copy(before)); return { ok: true }; },
  };
  const engine = { siteId: site, contentRevision: 'authored-' + site + '-r1',
    sceneIdentity: { siteId: site, contentRevision: 'authored-' + site + '-r1', registrationVersion: 1, frameDigest: binding.frame_digest },
    renderer: { domElement: canvas }, overlay,
    picking: { pick: () => {
      if (state.pickErrorOnce) { state.pickErrorOnce = false; throw Error('Authored unavailable pick'); }
      return state.hit ? [{ object: { userData: { fixture_id: state.hit, deviceId: state.hit } } }] : [];
    } },
    displaySurfaceHit: () => ({ point: [1, 2, 0], normal: [0, 0, 1] }),
    rig: { inCameraPose: () => false, stepAzimuth() {}, stepElevation() {}, snapHome() {} } };
  let frameId = 0; const queuedFrames = new Map();
  const requestFrame = callback => { const id = ++frameId; queuedFrames.set(id, callback); return id; };
  const cancelFrame = id => queuedFrames.delete(id);
  overlay.createVirtualFixtureAimGesture = options => createDirectAimGestureController({ ...options, requestFrame, cancelFrame });
  window.HomeSpatialViewport = { getSelectedSiteId: () => state.selectedSite,
    getSite: id => ({ id, contentRevision: 'authored-' + id + '-r1', sceneRegistration: { version: 1 } }) };
  window.HomeContentPacks = { get: id => ({ manifest: { revision: 'authored-' + id + '-r1', registrationVersion: 1,
    assets: { 'frame.json': { sha256: binding.frame_digest } } } }) };
  window.HomeApartmentAiming = { ENGINEERED_KIND: 'engineered_gimbal_v1' };
  const values = { window, open: true, phase: 'ready', editing: false, calibCam: null, liveOn: false,
    virtualSpotlightsVisible: true, spatialScale: 'apartment', rehearsalIsolated: false, unifiedShell: true,
    selectedSiteId: site, selectedContentRevision: 'authored-' + site + '-r1', selectedSiteInterior: true,
    apartmentSceneControlsEnabled: true, apartmentPlacementDisplayEnabled: true, apartmentDeviceControlsEnabled: site === 'echo',
    apartmentAuthorityKey: 'authored-authority', workspaceAuthorityRef: { current: { key: 'authored-authority' } },
    modelSiteRef: { current: site }, modelScopeRef: { current: { siteId: site, contentRevision: 'authored-' + site + '-r1',
      registrationVersion: 1, frameDigest: binding.frame_digest, authorityKey: 'authored-authority' } },
    engineRef: { current: engine }, hostRef: { current: host }, viewOpenRef: { current: true },
    apartmentSceneControlsEnabledRef: { current: true }, spatialLifecycleRevisionRef: { current: 1 },
    spatialTravelIntentRef: { current: 1 }, travellingRef: { current: false },
    virtualFixtureDocument: document, virtualFixtureBindingKey: JSON.stringify(['authored-authority', binding]),
    virtualFixtureItems: snapshot(), virtualFixtureId: '', virtualFixtureControlsOpen: false,
    virtualAimHistoryRef: { current: { undo: [], redo: [] } }, virtualAimGestureRef: { current: null },
    virtualFixtureStatesRef: { current: { key: 'authored-binding', states: new Map(), targets: new Map() } },
    deskTargetCaptureRef: { current: null }, calibApiRef: { current: null }, calibPickRef: { current: false },
    model: { devices: [{ id: 'authored-echo-light', type: 'light', ha_entity_id: 'light.authored_echo' }], targets: [], zones: [] },
    aimMode: false, aimDestination: null, simActive: false, semanticDevicesForRoom: [],
    requestAnimationFrame: requestFrame, cancelAnimationFrame: cancelFrame,
    showToast: (...args) => state.calls.push(['toast', copy(args)]),
    callSvc: (...args) => state.services.push(copy(args)),
    onShowShellControls: () => state.shellReveals++,
    setVirtualFixtureId: id => { state.selectedId = id; values.virtualFixtureId = id; state.calls.push(['id', id]); },
    setVirtualFixtureControlsOpen: value => { state.open = value; values.virtualFixtureControlsOpen = value; state.calls.push(['open', value]); },
    setVirtualFixtureChannel: value => { state.channel = value; }, setVirtualAimNotice: value => { state.notice = value; },
    setVirtualFixturePreview: value => { state.preview = value; }, setVirtualAimHistoryVersion() {},
    setHoverId() {}, setCardId: id => state.calls.push(['card', id]), setAimFixtureId() {}, setAimDestination() {}, setAimMode() {},
    stepSemanticDevice: () => { state.calls.push(['echo-semantic-cycle']); return 'authored-echo-light'; },
    zoomRequestRef: { current: () => {} },
  };
  const context = vm.createContext(values), cleanups = [];
  for (const name of ['selectionEngine', 'selectionLifecycle']) actual.capture(name, context);
  const mount = node => { const cleanup = actual.callback(node, context)(); if (typeof cleanup === 'function') cleanups.push(cleanup); };
  const listSelect = actual.callback(actual.listSelect, context);
  return { actual, ids, binding, document, descriptors, state, values, context, engine, host, canvas, window,
    select: id => listSelect(id), mount,
    mountKeyboard: () => mount(actual.keyboardEffect),
    mountPointer: () => { mount(actual.emptyClickEffect); mount(actual.gestureEffect); },
    dispose() { for (const cleanup of cleanups.reverse()) cleanup(); },
    key(key, target = host, detail = {}) { return emit(target, 'keydown', { key, ...detail }); },
    fingerprint: () => JSON.stringify([...descriptors]),
  };
}

const current = actualSource(fs.readFileSync(path.join(ROOT, SOURCE_PATH), 'utf8'));

test('real inspector callback selects only an admitted fixture without changing channels, targets or history', () => {
  const f = fixture(current), before = f.fingerprint();
  f.select(f.ids[1]); assert.equal(f.state.selectedId, f.ids[1]); assert.equal(f.state.open, true);
  assert.equal(f.fingerprint(), before); assert.deepEqual(f.state.services, []);
  const history = f.values.virtualAimHistoryRef.current;
  f.state.calls.length = 0; f.select('authored-missing-fixture');
  assert.deepEqual(f.state.calls, [], 'unknown fixture must not call overlay or React setters');
  assert.equal(f.values.virtualAimHistoryRef.current, history); f.dispose();
});

for (const [name, retire] of [
  ['selected home', f => { f.state.selectedSite = 'echo'; }],
  ['authority', f => { f.values.workspaceAuthorityRef.current.key = 'authored-other-authority'; }],
  ['model owner', f => { f.values.modelSiteRef.current = 'echo'; }],
  ['model authority', f => { f.values.modelScopeRef.current.authorityKey = 'authored-other-authority'; }],
  ['model frame', f => { f.values.modelScopeRef.current.frameDigest = 'e'.repeat(64); }],
  ['model registration', f => { f.values.modelScopeRef.current.registrationVersion++; }],
  ['model content revision', f => { f.values.modelScopeRef.current.contentRevision = 'authored-victoria-r2'; }],
  ['lifecycle', f => { f.values.spatialLifecycleRevisionRef.current++; }],
  ['closed view', f => { f.values.viewOpenRef.current = false; }],
  ['replaced same-binding engine', f => { f.values.engineRef.current = { ...f.engine, sceneIdentity: { ...f.engine.sceneIdentity } }; }],
  ['changed content revision', f => { f.engine.contentRevision = 'authored-victoria-r2'; }],
  ['changed registration', f => { f.engine.sceneIdentity = { ...f.engine.sceneIdentity, registrationVersion: 2 }; }],
  ['changed frame', f => { f.engine.sceneIdentity = { ...f.engine.sceneIdentity, frameDigest: 'authored-new-frame' }; }],
  ['changed equipment revision', f => { f.document.revision++; }],
  ['changed equipment digest', f => { f.document.digest = 'authored-new-survey'; }],
  ['missing admitted descriptor', f => { f.descriptors.delete(f.ids[0]); }],
  ['unavailable preview', f => { f.state.previewStatus = 'unavailable'; }],
  ['active placement', f => { f.values.deskTargetCaptureRef.current = { active: true }; }],
  ['paused input owner', f => { f.values.apartmentSceneControlsEnabledRef.current = false; }],
  ['A-to-B-to-A with cached engine', f => {
    f.state.selectedSite = 'echo'; f.values.spatialLifecycleRevisionRef.current++;
    f.state.selectedSite = 'victoria'; f.values.spatialLifecycleRevisionRef.current++;
  }],
]) test('candidate: retained actual selection callback refuses retired ' + name, () => {
  const f = fixture(current); retire(f); f.state.calls.length = 0; f.select(f.ids[0]);
  assert.deepEqual(f.state.calls, []); assert.equal(f.state.open, false); assert.deepEqual(f.state.services, []); f.dispose();
});

test('hidden fixture display retains existing list and keyboard selection', () => {
  const f = fixture(current); f.state.visible = false; f.values.virtualSpotlightsVisible = false;
  f.select(f.ids[0]); assert.equal(f.state.open, true); assert.equal(f.state.selectedId, f.ids[0]);
  f.mountKeyboard(); f.key('n'); assert.equal(f.state.selectedId, f.ids[1]); assert.deepEqual(f.state.services, []); f.dispose();
});

test('hidden fixture display refuses canvas selection and direct aim', () => {
  const f = fixture(current); f.state.visible = false; f.values.virtualSpotlightsVisible = false;
  f.select(f.ids[0]); f.state.hit = f.ids[1]; const before = f.fingerprint(); f.mountPointer();
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointermove', { clientX: 135 }); emit(f.canvas, 'pointerup', { clientX: 135 });
  emit(f.canvas, 'click', { clientX: 135 });
  assert.equal(f.state.selectedId, f.ids[0]); assert.equal(f.fingerprint(), before);
  assert.equal(f.values.virtualAimGestureRef.current, null); assert.deepEqual(f.state.services, []); f.dispose();
});

for (const [name, change] of [
  ['changed manifest', descriptor => { descriptor.binding.manifest_digest = 'e'.repeat(64); }],
  ['changed collision', descriptor => { descriptor.binding.canonical_collision_digest = 'e'.repeat(64); }],
  ['extra binding field', descriptor => { descriptor.binding.authority = 'authored-unrecognized'; }],
  ['missing binding field', descriptor => { delete descriptor.binding.manifest_digest; }],
]) test('actual selection callsite refuses a descriptor with ' + name, () => {
  const f = fixture(current); change(f.descriptors.get(f.ids[0])); f.select(f.ids[0]);
  assert.deepEqual(f.state.calls, []); assert.equal(f.state.open, false); assert.deepEqual(f.state.services, []); f.dispose();
});

test('selection result must name the requested fixture before UI publication', () => {
  const f = fixture(current); f.engine.overlay.setVirtualFixtureSelected = () => ({ fixture_id: f.ids[1] });
  f.select(f.ids[0]); assert.equal(f.state.open, false); assert.equal(f.state.selectedId, ''); assert.deepEqual(f.state.services, []); f.dispose();
});

test('scope retired synchronously by the selection setter cannot publish the inspector', () => {
  const f = fixture(current), original = f.engine.overlay.setVirtualFixtureSelected;
  f.engine.overlay.setVirtualFixtureSelected = id => {
    const result = original(id); f.values.workspaceAuthorityRef.current.key = 'authored-retired'; return result;
  };
  f.select(f.ids[0]); assert.equal(f.state.open, false); assert.equal(f.state.selectedId, ''); assert.equal(f.state.shellReveals, 0);
  assert.deepEqual(f.state.services, []); f.dispose();
});

test('candidate: actual keyboard owner cycles Victoria in admitted order and wraps', () => {
  const f = fixture(current); f.mountKeyboard();
  for (const id of [...f.ids, f.ids[0]]) { const event = f.key('n'); assert.equal(event.defaultPrevented, true); assert.equal(f.state.selectedId, id); }
  f.key('p'); assert.equal(f.state.selectedId, f.ids.at(-1)); assert.deepEqual(f.state.services, []); f.dispose();
});

for (const tag of ['input', 'textarea', 'select']) test('candidate: ' + tag + ' retains N/P key ownership', () => {
  const f = fixture(current); f.mountKeyboard(); const control = new AuthoredTarget(tag, f.host);
  const event = f.key('n', control); assert.equal(event.defaultPrevented, false); assert.deepEqual(f.state.calls, []); f.dispose();
});

test('candidate: a contenteditable field retains N/P key ownership', () => {
  const f = fixture(current); f.mountKeyboard(); const control = new AuthoredTarget('div', f.host); control.isContentEditable = true;
  const event = f.key('n', control); assert.equal(event.defaultPrevented, false); assert.deepEqual(f.state.calls, []); f.dispose();
});

test('candidate: N/P continues from an existing Victoria inspector button', () => {
  const f = fixture(current); f.select(f.ids[0]); f.mountKeyboard();
  const inspector = new AuthoredTarget('aside', f.host); inspector.setAttribute('data-victoria-virtual-fixtures', '1');
  const button = new AuthoredTarget('button', inspector); f.key('n', button);
  assert.equal(f.state.selectedId, f.ids[1]); assert.deepEqual(f.state.services, []); f.dispose();
});

test('candidate: Echo keeps its existing semantic cycle owner', () => {
  const f = fixture(current, { site: 'echo' }); f.values.semanticDevicesForRoom = [{ id: 'authored-echo-light' }]; f.mountKeyboard();
  f.key('n'); assert.deepEqual(f.state.calls, [['echo-semantic-cycle']]); assert.deepEqual(f.state.services, []); f.dispose();
});

for (const modifier of ['ctrlKey', 'metaKey', 'altKey', 'shiftKey']) test('candidate: modified N/P does not select under ' + modifier, () => {
  const f = fixture(current); f.mountKeyboard(); const event = f.key('n', f.host, { [modifier]: true });
  assert.equal(event.defaultPrevented, false); assert.deepEqual(f.state.calls, []); f.dispose();
});

test('candidate: an active aim gesture owns selection while keyboard cycling is attempted', () => {
  const f = fixture(current); f.mountPointer(); f.mountKeyboard(); f.state.hit = f.ids[0];
  emit(f.canvas, 'pointerdown'); const selected = f.state.selectedId;
  assert.equal(f.values.virtualAimGestureRef.current.readback().active, true);
  f.key('n'); assert.equal(f.state.selectedId, selected); emit(f.canvas, 'pointercancel'); f.dispose();
});

test('candidate: an owned empty click clears, while an empty drag and canceled pointer do not', () => {
  const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointermove', { clientX: 130 }); emit(f.canvas, 'pointerup', { clientX: 130 }); emit(f.canvas, 'click', { clientX: 130 });
  assert.equal(f.state.open, true, 'empty drag must only orbit');
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointercancel'); emit(f.canvas, 'click');
  assert.equal(f.state.open, true, 'canceled pointer cannot become an owned click');
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointerup'); emit(f.canvas, 'click');
  assert.equal(f.state.open, false, 'a current, unmoved, unclaimed empty click clears selection');
  assert.deepEqual(f.state.services, []); f.dispose();
});

test('candidate: explicit post-aim compatibility click never clears selection or its retained target/history', () => {
  const f = fixture(current); f.mountPointer(); f.state.hit = f.ids[0];
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointermove', { clientX: 135 }); emit(f.canvas, 'pointerup', { clientX: 135 });
  const before = f.fingerprint(), undo = copy(f.values.virtualAimHistoryRef.current.undo);
  assert.equal(f.state.open, true); assert.ok(undo.length > 0, 'real gesture commit callback must have run');
  emit(f.canvas, 'click', { clientX: 135 });
  assert.equal(f.state.open, true); assert.equal(f.state.selectedId, f.ids[0]);
  assert.equal(f.fingerprint(), before); assert.deepEqual(copy(f.values.virtualAimHistoryRef.current.undo), undo);
  assert.deepEqual(f.state.services, []); f.dispose();
});

test('candidate: an empty pointer started before retirement cannot clear the retained selection', () => {
  const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
  emit(f.canvas, 'pointerdown'); f.values.spatialLifecycleRevisionRef.current++;
  f.state.calls.length = 0; emit(f.canvas, 'pointerup'); emit(f.canvas, 'click');
  assert.equal(f.state.open, true); assert.deepEqual(f.state.calls, []); assert.deepEqual(f.state.services, []); f.dispose();
});

for (const disturbance of ['wheel', 'pinch', 'unavailable pick', 'UI click']) {
  test('empty-click ownership is withdrawn or refused for ' + disturbance, () => {
    const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
    if (disturbance === 'unavailable pick') f.state.pickErrorOnce = true;
    emit(f.canvas, 'pointerdown');
    if (disturbance === 'wheel') emit(f.canvas, 'wheel');
    if (disturbance === 'pinch') emit(f.canvas, 'pointerdown', { pointerId: 2, isPrimary: false });
    emit(f.canvas, 'pointerup');
    emit(disturbance === 'UI click' ? new AuthoredTarget('button', f.host) : f.canvas, 'click');
    assert.equal(f.state.open, true); assert.deepEqual(f.state.services, []); f.dispose();
  });
}

test('a claimed fixture click remains claimed after pointerup and lost capture', () => {
  const f = fixture(current); f.mountPointer(); f.state.hit = f.ids[0];
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointerup'); emit(f.canvas, 'lostpointercapture'); emit(f.canvas, 'click');
  assert.equal(f.state.open, true); assert.equal(f.state.selectedId, f.ids[0]); assert.deepEqual(f.state.services, []); f.dispose();
});

test('an empty click with a different pointer identity cannot clear selection', () => {
  const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointerup'); emit(f.canvas, 'click', { pointerId: 2 });
  assert.equal(f.state.open, true); assert.equal(f.state.selectedId, f.ids[0]); assert.deepEqual(f.state.services, []); f.dispose();
});

test('native host-retargeted compatibility click clears only its stationary canvas-origin empty pointer', () => {
  const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
  const before = f.fingerprint();
  emit(f.canvas, 'pointerdown'); f.host.setPointerCapture(1);
  emit(f.host, 'pointerup'); f.host.releasePointerCapture(1);
  emit(f.host, 'lostpointercapture'); emit(f.host, 'click');
  assert.equal(f.state.open, false, 'real orbit host capture can retarget the canvas-origin empty click to its host');
  assert.deepEqual(f.state.pointLookups.at(-1), [100, 100], 'actual source verifies the canvas under the compatibility click');
  assert.equal(f.fingerprint(), before); assert.deepEqual(f.state.services, []); f.dispose();
});

for (const surface of ['covered UI', 'offcanvas']) test('host-retargeted empty release refuses a final point on ' + surface, () => {
  const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
  const before = f.fingerprint();
  emit(f.canvas, 'pointerdown');
  f.state.elementAtPoint = surface === 'covered UI' ? new AuthoredTarget('button', f.host) : null;
  emit(f.host, 'pointerup'); emit(f.host, 'lostpointercapture'); emit(f.host, 'click');
  assert.equal(f.state.open, true); assert.equal(f.fingerprint(), before); assert.deepEqual(f.state.services, []); f.dispose();
});

for (const disturbance of ['orbit', 'canceled pointer', 'claimed fixture', 'host-origin UI']) {
  test('host-retargeted release preserves selection after ' + disturbance, () => {
    const f = fixture(current); f.mountPointer(); f.select(f.ids[0]); f.state.hit = null;
    if (disturbance === 'claimed fixture') f.state.hit = f.ids[0];
    emit(disturbance === 'host-origin UI' ? f.host : f.canvas, 'pointerdown');
    if (disturbance === 'orbit') emit(f.host, 'pointermove', { clientX: 130 });
    if (disturbance === 'canceled pointer') emit(f.canvas, 'pointercancel');
    // A fixture gesture retains its native canvas owner. Its eventual click
    // still cannot turn into an empty clear merely because the hit disappears.
    emit(disturbance === 'claimed fixture' ? f.canvas : f.host, 'pointerup', { clientX: disturbance === 'orbit' ? 130 : 100 });
    f.state.hit = null; const before = f.fingerprint();
    emit(f.host, 'lostpointercapture'); emit(f.host, 'click', { clientX: disturbance === 'orbit' ? 130 : 100 });
    assert.equal(f.state.open, true); assert.equal(f.state.selectedId, f.ids[0]);
    assert.equal(f.fingerprint(), before); assert.deepEqual(f.state.services, []); f.dispose();
  });
}

test('disposing the actual event owners removes keyboard and empty-click listeners', () => {
  const f = fixture(current); f.mountPointer(); f.mountKeyboard(); f.select(f.ids[0]); f.state.hit = null;
  f.dispose(); f.state.calls.length = 0;
  emit(f.canvas, 'pointerdown'); emit(f.canvas, 'pointerup'); emit(f.canvas, 'click'); f.key('n');
  assert.equal(f.state.open, true); assert.equal(f.state.selectedId, f.ids[0]);
  assert.deepEqual(f.state.calls, []); assert.deepEqual(f.state.services, []);
});

test('candidate: actual Echo view listener has no dblclick service activation', () => {
  const f = fixture(current, { site: 'echo' }); f.state.hit = 'authored-echo-light'; f.mount(f.actual.echoPickEffect);
  emit(f.canvas, 'dblclick'); assert.deepEqual(f.state.services, []); f.dispose();
});
