// Actual Apartment callbacks/effects with authored viewport and event adapters.
// This is not a React mount, native browser gesture or actual-room acceptance.
// No renderer, transport, camera/model session or equipment save is opened.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';
import { createDirectAimGestureController } from '../app/src/home-3d/direct-aim-gesture.mjs';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const babel = require(path.join(ROOT, 'app/src/vendor/babel-7.29.0/babel.min.js'));
const Directional = require('../app/src/home-apartment-directional.js');
const copy = value => JSON.parse(JSON.stringify(value));

// Run the real rig module; Node needs the browser's bare `three` import
// resolved to the same vendored build. No navigation or settling logic is copied.
const rigSource = fs.readFileSync(path.join(ROOT, 'app/src/home-3d/rig.js'), 'utf8');
assert.equal((rigSource.match(/from 'three'/g) || []).length, 1);
const { createRig } = await import('data:text/javascript;base64,' + Buffer.from(rigSource.replace("from 'three'",
  'from ' + JSON.stringify(pathToFileURL(path.join(ROOT, 'app/src/vendor/three-0.180.0/three.module.js')).href))).toString('base64'));

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}

function apartmentSource() {
  const source = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment.jsx'), 'utf8');
  const program = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
  const matches = program.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === 'HomeApartmentLiveView');
  assert.equal(matches.length, 1, 'one actual Apartment owner');
  const owner = matches[0], text = node => source.slice(node.start, node.end), declarations = new Map();
  for (const statement of owner.body.body) if (statement.type === 'VariableDeclaration') {
    for (const declaration of statement.declarations) if (declaration.id.type === 'Identifier') declarations.set(declaration.id.name, declaration.init);
  }
  function initializer(name, context) {
    const node = declarations.get(name); assert.ok(node, 'actual owner declaration: ' + name);
    return vm.runInContext('(' + text(node) + ')', context, { timeout: 1000 });
  }
  function initialState(name, context) {
    const matches = owner.body.body.filter(node => node.type === 'VariableDeclaration').flatMap(node => node.declarations)
      .filter(node => node.id.type === 'ArrayPattern' && node.id.elements[0]?.name === name);
    assert.equal(matches.length, 1, 'one actual state initializer: ' + name);
    const call = matches[0].init; assert.equal(call.callee?.name, 'useState');
    return vm.runInContext('(' + text(call.arguments[0]) + ')', context, { timeout: 1000 });
  }
  function install(name, context, pending = new Set()) {
    if (Object.hasOwn(context, name)) return context[name];
    const node = declarations.get(name); assert.ok(node, 'actual callback declaration: ' + name);
    const fn = node.type === 'CallExpression' && node.callee?.name === 'useCallback' ? node.arguments[0] : node;
    assert.ok(['ArrowFunctionExpression', 'FunctionExpression'].includes(fn.type), name + ' is an actual callback');
    pending.add(name);
    walk(fn, dependency => {
      if (dependency.type !== 'Identifier' || Object.hasOwn(context, dependency.name) || pending.has(dependency.name)) return;
      const value = declarations.get(dependency.name), callback = value?.type === 'CallExpression' && value.callee?.name === 'useCallback' ? value.arguments[0] : value;
      if (['ArrowFunctionExpression', 'FunctionExpression'].includes(callback?.type)) install(dependency.name, context, pending);
    });
    context[name] = vm.runInContext('(' + text(fn) + ')', context, { timeout: 1000 });
    pending.delete(name); return context[name];
  }
  function effect(signature) {
    const candidates = owner.body.body.filter(node => node.type === 'ExpressionStatement'
      && (node.expression?.callee?.name === 'useEffect' || node.expression?.callee?.object?.name === 'React'
        && node.expression?.callee?.property?.name === 'useLayoutEffect')).map(node => node.expression.arguments[0])
      .filter(node => text(node).includes(signature));
    assert.equal(candidates.length, 1, 'one actual effect: ' + signature); return candidates[0];
  }
  function component(name) {
    const found = program.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
    assert.equal(found.length, 1); return found[0];
  }
  function prop(name, attribute) {
    const found = [];
    walk(owner, node => {
      if (node.type !== 'JSXOpeningElement' || node.name?.name !== name) return;
      const value = node.attributes.find(item => item.name?.name === attribute)?.value?.expression;
      if (value) found.push(value);
    });
    assert.equal(found.length, 1, 'one actual ' + name + '.' + attribute); return found[0];
  }
  function portal(name) {
    const found = [];
    walk(owner, node => {
      if (node.type === 'JSXExpressionContainer' && text(node).includes('ReactDOM.createPortal')
          && text(node).includes('<window.' + name)) found.push(node.expression);
    });
    assert.equal(found.length, 1, 'one actual portal gate for ' + name); return found[0];
  }
  function renderExpression(node, context) {
    walk(node, dependency => {
      const value = dependency.type === 'Identifier' && declarations.get(dependency.name);
      const callback = value?.type === 'CallExpression' && value.callee?.name === 'useCallback' ? value.arguments[0] : value;
      if (!Object.hasOwn(context, dependency.name) && ['ArrowFunctionExpression', 'FunctionExpression'].includes(callback?.type)) install(dependency.name, context);
    });
    const compiled = babel.transform('(' + text(node) + ')', { presets: ['react'] }).code;
    return vm.runInContext(compiled, context, { timeout: 1000 });
  }
  return { owner, text, initializer, initialState, install, effect, component, prop, portal, renderExpression,
    evaluate: (node, context) => vm.runInContext('(' + text(node) + ')', context, { timeout: 1000 }) };
}

class AuthoredTarget {
  constructor(tag = 'div', parent = null) {
    this.tagName = tag.toUpperCase(); this.parentElement = parent; this.style = {};
    this.listeners = new Map(); this.attributes = new Map(); this.captures = new Set();
    this.isContentEditable = false; this.isConnected = true;
    this.clientWidth = 400; this.clientHeight = 400;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  hasAttribute(name) { return this.attributes.has(name); }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) for (const part of selector.split(',')) {
      const name = part.trim(), attribute = /^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/.exec(name);
      if (name.toUpperCase() === node.tagName || attribute && node.hasAttribute(attribute[1])
        && (attribute[2] === undefined || node.getAttribute(attribute[1]) === attribute[2])) return node;
    }
    return null;
  }
  contains(node) { for (; node; node = node.parentElement) if (node === this) return true; return false; }
  addEventListener(type, callback, options) {
    const rows = this.listeners.get(type) || [];
    rows.push({ callback, capture: options === true || options?.capture === true }); this.listeners.set(type, rows);
  }
  removeEventListener(type, callback, options) {
    const capture = options === true || options?.capture === true;
    this.listeners.set(type, (this.listeners.get(type) || []).filter(row => row.callback !== callback || row.capture !== capture));
  }
  setPointerCapture(id) { this.captures.add(id); }
  hasPointerCapture(id) { return this.captures.has(id); }
  releasePointerCapture(id) { this.captures.delete(id); }
  getBoundingClientRect() { return { left: 40, top: 60, width: this.clientWidth, height: this.clientHeight }; }
}

// Deliberately generated adapter events exercise the actual source listeners.
// Browser capture retargeting and compatibility-click generation are separate QA.
function emit(target, type, detail = {}) {
  const event = { type, target, button: 0, isPrimary: true, pointerId: 1,
    clientX: 100, clientY: 100, key: '', ctrlKey: false, metaKey: false,
    altKey: false, shiftKey: false, repeat: false, defaultPrevented: false, ...detail,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    stopImmediatePropagation() { this.stopped = true; this.immediate = true; } };
  const chain = []; for (let node = target; node; node = node.parentElement) chain.push(node);
  for (const [nodes, capture] of [[chain.slice().reverse(), true], [chain, false]]) for (const node of nodes) {
    event.currentTarget = node;
    for (const row of (node.listeners.get(type) || []).slice()) {
      if (row.capture === capture) row.callback(event);
      if (event.immediate) break;
    }
    if (event.stopped) return event;
  }
  return event;
}

const actual = apartmentSource();

// Reuse the actual producer's channel transition. The authored adapter records
// output intent but does not reproduce merge, range or restoration decisions.
function producerTransition() {
  const source = fs.readFileSync(path.join(ROOT, 'app/src/home-3d/equipment-virtual-fixtures.mjs'), 'utf8');
  const program = babel.transform(source, { ast: true, code: false }).ast.program;
  const names = new Set(['PROFILE', 'copy', 'finite', 'VIRTUAL_LIMITS']);
  const declarations = program.body.filter(node => node.type === 'VariableDeclaration'
    && node.declarations.some(declaration => names.has(declaration.id?.name)));
  assert.equal(declarations.length, 4);
  const transition = program.body.find(node => node.type === 'FunctionDeclaration' && node.id?.name === 'nextState');
  assert.ok(transition);
  const context = vm.createContext({ Den: require(path.join(ROOT, 'app/src/home-den-fixtures.js')) });
  return vm.runInContext('(() => {' + declarations.map(node => source.slice(node.start, node.end)).join('\n')
    + '\n' + source.slice(transition.start, transition.end) + '\nreturn nextState;})()', context, { timeout: 1000 });
}
const nextState = producerTransition();

function fixture({ mode = 'zones' } = {}) {
  const window = new AuthoredTarget('window'), host = new AuthoredTarget('div', window), canvas = new AuthoredTarget('canvas', host);
  const hub = new AuthoredTarget('circle', host); hub.setAttribute('data-apt-radial-start', 'hub');
  const binding = { content_revision: 'authored-victoria-r1', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
  const document = { binding, revision: 7, digest: 'd'.repeat(64) }, ids = ['authored-fixture-one', 'authored-fixture-two'];
  const descriptors = new Map(ids.map((id, index) => [id, {
    schema: 'home-virtual-fixture-interaction-1', fixture_id: id, mount_id: 'authored-mount-' + index, generation: 3,
    equipment_revision: document.revision, equipment_digest: document.digest, binding: copy(binding),
    display_anchor_m: [index * .6, index * .3, 1], display_origin_m: [index * .6, index * .3, .86],
    horizontal_world_reference: [0, 1, 0], origin_role: 'mount_anchor', optical_origin_m: null,
    state: { spotlight: { on: true, brightness: .7, color_temp_kelvin: 3000 },
      downward: { on: false, brightness: .3, color_temp_kelvin: 2700 }, pan_deg: 17, tilt_deg: 35,
      radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, azimuth_deg: index * 60,
        on: true, brightness: .5, color_temp_kelvin: 3000 })) },
    target: { point: [1, 2, 0], normal: [0, 0, 1], kind: 'registered_mesh' }, truth_role: 'virtual_preview', physical_commands: false,
  }]));
  const state = { patches: [], calls: [], intervals: [], selectedSite: 'victoria', pickId: ids[0],
    previewRevision: 7, previewDigest: document.digest, previewVisible: true, previewStatus: 'ready', readout: null, projection: null };
  const preview = () => ({ status: state.previewStatus, visible: state.previewVisible, revision: state.previewRevision,
    digest: state.previewDigest, fixtures: ids.map(id => ({ fixture_id: id, label: id, state: copy(descriptors.get(id).state) })) });
  const overlay = {
    virtualFixturesReadback: preview, virtualFixtureInteractionDescriptor: id => descriptors.has(id) ? copy(descriptors.get(id)) : null,
    virtualFixturePickObjects: () => [], personGroup: { visible: false }, markersById: new Map(),
    setVirtualFixtureSelected: id => { state.calls.push(['selected', id]); return overlay.virtualFixtureInteractionDescriptor(id); },
    setVirtualFixtureState(id, patch) {
      state.patches.push({ id, patch: copy(patch) });
      const before = descriptors.get(id); before.state = nextState(before.state, patch); return { ok: true };
    },
    restoreVirtualFixtureAim: () => ({ ok: true }),
    aimVirtualFixtureAtTarget: () => assert.fail('Zones routing must never invoke the aim producer'),
    setVirtualFixtures: () => {}, setVirtualFixturesVisible: visible => { state.previewVisible = visible; },
  };
  const camera = new THREE.OrthographicCamera(-2, 2, 2, -2, .1, 20);
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 1); camera.updateMatrixWorld(true); camera.updateProjectionMatrix();
  const apartmentRoot = new THREE.Group(); apartmentRoot.updateMatrixWorld(true);
  const engine = { siteId: 'victoria', contentRevision: binding.content_revision,
    sceneIdentity: { siteId: 'victoria', contentRevision: binding.content_revision, registrationVersion: 1, frameDigest: binding.frame_digest },
    camera, apartmentRoot, renderer: { domElement: canvas }, overlay,
    picking: { pick: () => state.pickId ? [{ object: { userData: { fixture_id: state.pickId } } }] : [],
      projectToScreen: () => assert.fail('no legacy marker projection is needed by this authored timer fixture') },
    displaySurfaceHit: () => ({ point: [1, 2, 0], normal: [0, 0, 1] }),
    rig: { inCameraPose: () => false, stepAzimuth() {}, stepElevation() {}, snapHome() {} } };
  const frames = new Map(); let frameId = 0;
  overlay.createVirtualFixtureAimGesture = options => createDirectAimGestureController({ ...options,
    requestFrame: callback => { const id = ++frameId; frames.set(id, callback); return id; }, cancelFrame: id => frames.delete(id) });
  window.HomeApartmentDirectional = Directional;
  window.AptDirectionalComposer = function AuthoredComposerElement() {};
  window.HomeSpatialViewport = { getSelectedSiteId: () => state.selectedSite };
  window.HomeOverlay = { topKey: () => 'apartment-embedded' };
  const values = { window, open: true, phase: 'ready', selectedSiteId: 'victoria', selectedContentRevision: binding.content_revision,
    editing: false, calibCam: null, liveOn: false, rehearsalIsolated: false, spatialScale: 'apartment', unifiedShell: true,
    virtualSpotlightsVisible: true, apartmentSceneControlsEnabled: true, selectedSiteInterior: true,
    apartmentAuthorityKey: 'authored-authority', workspaceAuthorityRef: { current: { key: 'authored-authority' } },
    modelSiteRef: { current: 'victoria' }, modelScopeRef: { current: { siteId: 'victoria', authorityKey: 'authored-authority',
      contentRevision: binding.content_revision, registrationVersion: 1, frameDigest: binding.frame_digest } },
    engineRef: { current: engine }, hostRef: { current: host }, viewOpenRef: { current: true },
    apartmentSceneControlsEnabledRef: { current: true }, spatialLifecycleRevisionRef: { current: 1 }, travellingRef: { current: false },
    virtualFixtureDocument: document, virtualFixtureItems: preview().fixtures, virtualFixtureControlsOpen: true,
    virtualFixtureId: ids[0], selectedVirtualFixture: preview().fixtures[0], virtualInspectorTab: mode === 'zones' ? 'light' : 'aim',
    fixtureInteractionMode: mode, fixtureInteractionModeRef: { current: mode },
    virtualAimGestureRef: { current: null }, radialGestureOwnerRef: { current: null }, flushRadialGestureRef: { current: null },
    directionalProjectionRef: { current: null }, deskTargetCaptureRef: { current: null },
    radialReadout: null, radialGestureActive: false, anchors: {},
    virtualFixtureStatesRef: { current: { states: new Map(), targets: new Map() } },
    virtualAimHistoryRef: { current: { undo: [{ authored: 'previous aim' }], redo: [] } },
    virtualFixtureBindingKey: JSON.stringify(binding), hoverId: null, cardId: null, semanticDevicesForRoom: [],
    setVirtualFixturePreview: value => { values.virtualFixtureItems = value.fixtures; },
    setVirtualFixtureId: value => { values.virtualFixtureId = value; state.calls.push(['fixture', value]); },
    setVirtualFixtureControlsOpen: value => { values.virtualFixtureControlsOpen = value; state.calls.push(['open', value]); },
    setFixtureInteractionMode: value => { values.fixtureInteractionMode = value; values.fixtureInteractionModeRef.current = value; state.calls.push(['mode', value]); },
    setVirtualInspectorTab: value => { values.virtualInspectorTab = value; state.calls.push(['tab', value]); },
    setRadialReadout: value => { state.readout = value; values.radialReadout = value; },
    setRadialGestureActive: value => { values.radialGestureActive = value; },
    setAnchors: value => { state.projection = value; values.anchors = value; }, onShowShellControls: () => state.calls.push(['show']),
    setVirtualFixtureChannel: value => state.calls.push(['channel', value]), setVirtualAimNotice() {}, setVirtualAimHistoryVersion() {},
    restoreApartmentOperationFocus: value => state.calls.push(['focus', value]),
    callSvc: () => assert.fail('directional UI cannot send a physical command'), saveModel: () => assert.fail('directional UI cannot save equipment'),
    setInterval(callback, delay) { const record = { callback, delay, cleared: false }; state.intervals.push(record); return record; },
    clearInterval(record) { record.cleared = true; },
    requestAnimationFrame: () => assert.fail('directional integration cannot create a new RAF loop'),
  };
  const context = vm.createContext(values), cleanups = [];
  for (const name of ['selectionEngine', 'selectionLifecycle']) context[name] = actual.initializer(name, context);
  for (const name of ['getDirectionalSnapshot', 'isDirectionalProjectionCurrent', 'projectDirectionalFixture', 'changeFixtureInteractionMode', 'changeVirtualInspectorTab', 'selectVirtualFixture', 'changeVirtualFixture']) actual.install(name, context);
  const mount = signature => {
    const cleanup = actual.evaluate(actual.effect(signature), context)();
    if (typeof cleanup === 'function') cleanups.push(cleanup);
  };
  const commit = anchors => {
    context.anchors = anchors;
    context.directionalDisplay = actual.initializer('directionalDisplay', context);
    actual.evaluate(actual.effect('directionalProjectionRef.current = directionalDisplay'), context)();
    return context.directionalProjectionRef.current;
  };
  const refresh = () => commit({ __directional: context.projectDirectionalFixture() });
  refresh();
  return { state, values, context, ids, descriptors, binding, document, engine, window, host, canvas, hub, refresh, commit,
    mountRadial: () => mount('const current = owner =>'), mountTimer: () => mount('const directional = projectDirectionalFixture()'),
    mountPreview: () => mount('overlay.setVirtualFixtures(document)'),
    mountKeyboard: () => mount('const semanticCycleFromCard'), mountSpot: () => mount('overlay.createVirtualFixtureAimGesture'),
    point(relative = [0, 0], projection = values.directionalProjectionRef.current) {
      const point = projection.frame.project(relative), rect = host.getBoundingClientRect();
      assert.ok(point?.visible); return { clientX: rect.left + point.x * rect.width / host.clientWidth,
        clientY: rect.top + point.y * rect.height / host.clientHeight };
    },
    flush: () => values.flushRadialGestureRef.current?.(),
    dispose() { for (const cleanup of cleanups.reverse()) cleanup(); },
  };
}

for (const [name, change] of [
  ['selected fixture', f => { f.values.virtualFixtureId = f.ids[1]; f.state.pickId = f.ids[1]; }],
  ['equipment revision', f => { f.descriptors.get(f.ids[0]).equipment_revision++; f.document.revision++; f.state.previewRevision++; }],
  ['equipment digest', f => { const digest = 'e'.repeat(64); f.descriptors.get(f.ids[0]).equipment_digest = digest; f.document.digest = digest; f.state.previewDigest = digest; }],
  ['generation', f => { f.descriptors.get(f.ids[0]).generation++; }],
  ['anchor', f => { f.descriptors.get(f.ids[0]).display_anchor_m[0] += .25; }],
  ['azimuth', f => { f.descriptors.get(f.ids[0]).state.radials[0].azimuth_deg += 5; }],
]) test('cached directional projection refuses changed ' + name + ' before gesture admission', () => {
  const f = fixture(); f.mountRadial(); const oldProjection = f.values.directionalProjectionRef.current;
  change(f); const event = emit(f.canvas, 'pointerdown', f.point([0, 0], oldProjection));
  assert.equal(event.defaultPrevented, true, 'a fixture hit remains selection-owned rather than orbiting');
  assert.equal(f.values.radialGestureOwnerRef.current === null, true, 'a fresh descriptor cannot borrow an earlier drawn plane');
  assert.deepEqual(f.state.patches, []); assert.equal(f.host.captures.size, 0); f.dispose();
});

function queuePaint(f) {
  emit(f.canvas, 'pointerdown', f.point());
  assert.equal(f.values.radialGestureOwnerRef.current?.gesture.readback().active, true);
  emit(f.host, 'pointermove', f.point([1.1, 0]));
  assert.ok(f.values.radialGestureOwnerRef.current?.pending?.radials.length > 0);
}

test('actual 80 ms HUD flush applies a radial-only modeled patch and publishes its matching projection/readout', () => {
  const f = fixture(), before = copy(f.descriptors.get(f.ids[0])), history = f.values.virtualAimHistoryRef.current;
  f.mountRadial(); f.mountTimer(); assert.equal(f.state.intervals.length, 1); assert.equal(f.state.intervals[0].delay, 80);
  queuePaint(f); assert.deepEqual(f.state.patches, []); assert.equal(f.state.readout, null);
  f.state.intervals[0].callback(); assert.equal(f.state.patches.length, 1);
  const [{ id, patch }] = f.state.patches; assert.equal(id, f.ids[0]); assert.deepEqual(Object.keys(patch), ['radials']);
  for (const zone of patch.radials) assert.deepEqual(Object.keys(zone).sort(), ['brightness', 'number', 'on']);
  const after = f.descriptors.get(f.ids[0]);
  for (const key of ['spotlight', 'downward', 'pan_deg', 'tilt_deg']) assert.deepEqual(copy(after.state[key]), before.state[key]);
  assert.deepEqual(after.target, before.target); assert.equal(after.physical_commands, false); assert.equal(after.truth_role, 'virtual_preview');
  for (const zone of after.state.radials) {
    const original = before.state.radials.find(item => item.number === zone.number);
    assert.equal(zone.color_temp_kelvin, original.color_temp_kelvin); assert.equal(zone.azimuth_deg, original.azimuth_deg);
  }
  assert.equal(f.values.virtualAimHistoryRef.current, history); assert.deepEqual(copy(history), { undo: [{ authored: 'previous aim' }], redo: [] });
  assert.ok(f.state.readout); assert.ok(f.state.projection.__directional.geometry);
  f.state.intervals[0].callback(); assert.equal(f.state.patches.length, 1, 'already flushed intent is not repeated');
  assert.deepEqual(f.state.calls, []); f.dispose(); assert.equal(f.state.intervals[0].cleared, true);
});

test('pointerup flushes the final queued modeled level and releases only its owned capture', () => {
  const f = fixture(); f.mountRadial(); queuePaint(f); emit(f.host, 'pointerup', f.point([1.1, 0]));
  assert.equal(f.state.patches.length, 1); assert.equal(f.values.radialGestureOwnerRef.current, null);
  assert.equal(f.host.captures.size, 0); assert.equal(f.values.virtualFixtureControlsOpen, true); f.dispose();
});

for (const [name, cancel] of [
  ['pointercancel', f => emit(f.host, 'pointercancel')],
  ['lost capture', f => emit(f.host, 'lostpointercapture')],
  ['second pointer', f => emit(f.canvas, 'pointerdown', { ...f.point(), pointerId: 2, isPrimary: false })],
  ['Escape', f => emit(f.window, 'keydown', { key: 'Escape' })],
  ['blur', f => emit(f.window, 'blur')],
  ['wheel', f => emit(f.host, 'wheel')],
  ['owner cleanup', f => f.dispose()],
  ['overlay Escape path', f => actual.install('handleApartmentEscape', f.context)()],
]) test('actual radial owner drops unflushed intent and releases capture on ' + name, () => {
  const f = fixture(), before = copy([...f.descriptors]); f.mountRadial(); queuePaint(f); cancel(f); f.flush();
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.host.captures.size, 0);
  assert.deepEqual(f.state.patches, []); assert.deepEqual(copy([...f.descriptors]), before);
  assert.equal(f.values.virtualFixtureId, f.ids[0]); assert.equal(f.values.virtualFixtureControlsOpen, true); f.dispose();
});

test('cancel retains already applied modeled levels while dropping later queued values', () => {
  const f = fixture(); f.mountRadial(); queuePaint(f); f.flush(); const applied = copy([...f.descriptors]);
  emit(f.host, 'pointermove', f.point([0, 1.1])); assert.ok(f.values.radialGestureOwnerRef.current.pending);
  emit(f.window, 'keydown', { key: 'Escape' }); f.flush();
  assert.equal(f.state.patches.length, 1); assert.deepEqual(copy([...f.descriptors]), applied); f.dispose();
});

for (const [name, retire] of [
  ['authority', f => { f.values.workspaceAuthorityRef.current.key = 'retired-owner'; }],
  ['home', f => { f.state.selectedSite = 'echo'; }],
  ['model authority', f => { f.values.modelScopeRef.current.authorityKey = 'retired-owner'; }],
  ['model frame', f => { f.values.modelScopeRef.current.frameDigest = 'e'.repeat(64); }],
  ['model registration', f => { f.values.modelScopeRef.current.registrationVersion++; }],
  ['travel', f => { f.values.travellingRef.current = true; }],
  ['closed view', f => { f.values.viewOpenRef.current = false; }],
  ['engine lifetime', f => { f.values.engineRef.current = { ...f.engine }; }],
  ['A-to-B-to-A lifetime', f => { f.values.spatialLifecycleRevisionRef.current += 2; }],
  ['content revision', f => { f.engine.contentRevision += '-new'; }],
  ['equipment revision', f => { f.document.revision++; }],
  ['equipment digest', f => { f.document.digest = 'e'.repeat(64); }],
  ['selection', f => { f.values.virtualFixtureId = f.ids[1]; }],
  ['closed inspector', f => { f.values.virtualFixtureControlsOpen = false; }],
  ['mode', f => { f.values.fixtureInteractionModeRef.current = 'spot'; }],
  ['immediate display state', f => { f.values.virtualSpotlightsVisible = false; }],
  ['overlay display', f => { f.state.previewVisible = false; }],
  ['camera projection', f => { f.engine.camera.projectionMatrix.elements[0] += .1; }],
  ['native viewport', f => { f.host.clientWidth -= 1; }],
  ['descriptor generation', f => { f.descriptors.get(f.ids[0]).generation++; }],
  ['descriptor anchor', f => { f.descriptors.get(f.ids[0]).display_anchor_m[0] += .1; }],
  ['canonical azimuth', f => { f.descriptors.get(f.ids[0]).state.radials[0].azimuth_deg += 5; }],
  ['manifest binding', f => { f.descriptors.get(f.ids[0]).binding.manifest_digest = 'e'.repeat(64); }],
  ['collision binding', f => { f.descriptors.get(f.ids[0]).binding.canonical_collision_digest = 'e'.repeat(64); }],
  ['preview provenance', f => { f.descriptors.get(f.ids[0]).truth_role = 'reported'; }],
  ['physical provenance', f => { f.descriptors.get(f.ids[0]).physical_commands = true; }],
]) test('queued directional patch cannot cross retired ' + name, () => {
  const f = fixture(); f.mountRadial(); queuePaint(f); retire(f); f.flush();
  assert.deepEqual(f.state.patches, []); assert.equal(f.values.radialGestureOwnerRef.current, null);
  assert.equal(f.host.captures.size, 0); f.dispose();
});

test('native capture failure cancels without output or a stranded owner', () => {
  const f = fixture(); f.host.setPointerCapture = () => { throw Error('Authored detached capture'); }; f.mountRadial();
  emit(f.canvas, 'pointerdown', f.point()); assert.equal(f.values.radialGestureOwnerRef.current, null);
  assert.equal(f.host.captures.size, 0); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('a different pointer cannot move or release the captured radial gesture', () => {
  const f = fixture(); f.mountRadial(); emit(f.canvas, 'pointerdown', f.point()); const owner = f.values.radialGestureOwnerRef.current;
  emit(f.host, 'pointermove', { ...f.point([1.1, 0]), pointerId: 2 }); emit(f.host, 'pointerup', { ...f.point([1.1, 0]), pointerId: 2 });
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(owner.pending, null); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('Zones leaves empty canvas input to the existing orbit owner', () => {
  const f = fixture(); let orbit = 0; f.mountSpot(); f.mountRadial(); f.state.pickId = null;
  f.host.addEventListener('pointerdown', () => orbit++);
  const event = emit(f.canvas, 'pointerdown', f.point([1.1, 0]));
  assert.equal(event.defaultPrevented, false); assert.equal(orbit, 1); assert.equal(f.values.radialGestureOwnerRef.current, null);
  assert.equal(f.values.virtualAimGestureRef.current.readback().active, false); f.dispose();
});

test('Zones selects another fixture without entering direct aim and waits for its own freshly drawn plane', () => {
  const f = fixture(); f.mountSpot(); f.mountRadial(); f.state.pickId = f.ids[1];
  emit(f.canvas, 'pointerdown', f.point()); assert.equal(f.values.virtualFixtureId, f.ids[1]);
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.values.virtualAimGestureRef.current.readback().active, false);
  emit(f.canvas, 'pointerdown', f.point()); assert.equal(f.values.radialGestureOwnerRef.current, null);
  f.refresh(); emit(f.canvas, 'pointerdown', f.point()); assert.equal(f.values.radialGestureOwnerRef.current.fixtureId, f.ids[1]);
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('Spot routes fixture input to the actual existing direct-aim controller', () => {
  const f = fixture({ mode: 'spot' }); f.mountSpot(); f.mountRadial(); const event = emit(f.canvas, 'pointerdown', f.point());
  assert.equal(event.defaultPrevented, true); assert.equal(f.values.radialGestureOwnerRef.current, null);
  assert.equal(f.values.virtualAimGestureRef.current.readback().active, true); assert.equal(f.values.virtualFixtureId, f.ids[0]); f.dispose();
});

test('A/Z uses the actual mode callback and changes the matching inspector tab', () => {
  const f = fixture({ mode: 'spot' }); f.mountKeyboard(); const zones = emit(f.host, 'keydown', { key: 'z' });
  assert.equal(zones.defaultPrevented, true); assert.deepEqual(f.state.calls, [['mode', 'zones'], ['tab', 'light']]);
  f.state.calls.length = 0; const spot = emit(f.host, 'keydown', { key: 'a' });
  assert.equal(spot.defaultPrevented, true); assert.deepEqual(f.state.calls, [['mode', 'spot'], ['tab', 'aim']]); f.dispose();
});

for (const tag of ['input', 'textarea', 'select', 'button', 'contenteditable', 'svg role button']) {
  test('A/Z leaves ' + tag + ' keyboard ownership intact', () => {
    const f = fixture({ mode: 'spot' }); f.mountKeyboard(); const control = new AuthoredTarget(tag === 'contenteditable' ? 'div' : tag === 'svg role button' ? 'g' : tag, f.host);
    if (tag === 'contenteditable') control.isContentEditable = true;
    if (tag === 'svg role button') control.setAttribute('role', 'button');
    const event = emit(control, 'keydown', { key: 'z' }); assert.equal(event.defaultPrevented, false);
    assert.deepEqual(f.state.calls, []); f.dispose();
  });
}

for (const modifier of ['ctrlKey', 'metaKey', 'altKey', 'shiftKey', 'defaultPrevented']) test('A/Z does not borrow ' + modifier + ' input', () => {
  const f = fixture({ mode: 'spot' }); f.mountKeyboard(); emit(f.host, 'keydown', { key: 'z', [modifier]: true });
  assert.deepEqual(f.state.calls, []); f.dispose();
});

test('an active radial owner retains keyboard mode ownership until cancellation', () => {
  const f = fixture(); f.mountRadial(); f.mountKeyboard(); queuePaint(f); emit(f.host, 'keydown', { key: 'a' });
  assert.equal(f.values.fixtureInteractionModeRef.current, 'zones'); assert.deepEqual(f.state.calls, []); f.dispose();
});

for (const [name, change] of [
  ['default-prevented Escape', event => { event.defaultPrevented = true; }],
  ['child modal', (_, f) => { f.window.HomeOverlay.topKey = () => 'authored-child-modal'; }],
]) test('radial Escape preserves ' + name + ' ownership', () => {
  const f = fixture(); f.mountRadial(); queuePaint(f); const owner = f.values.radialGestureOwnerRef.current, detail = { key: 'Escape' };
  change(detail, f); emit(f.window, 'keydown', detail);
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(f.state.patches.length, 0); f.dispose();
});

test('immediate display state withdraws directional admission before the overlay effect updates readback', () => {
  const f = fixture(); assert.equal(f.state.previewVisible, true); f.values.virtualSpotlightsVisible = false;
  assert.equal(f.context.getDirectionalSnapshot(f.ids[0]), null); assert.equal(f.context.projectDirectionalFixture(), null);
  f.mountRadial(); emit(f.canvas, 'pointerdown', f.point()); assert.equal(f.values.radialGestureOwnerRef.current, null); f.dispose();
});

test('a plane with a visible hub but clipped contour uses inspector fallback and cannot paint', () => {
  const f = fixture(), camera = new THREE.PerspectiveCamera(60, 1, .1, 20);
  camera.up.set(0, 0, 1); camera.position.set(0, -.3, 1.1); camera.lookAt(0, 0, 1); camera.updateMatrixWorld(true); f.engine.camera = camera;
  const matrix = camera.projectionMatrix.clone().multiply(camera.matrixWorldInverse).multiply(f.engine.apartmentRoot.matrixWorld);
  const frame = Directional.createPlaneFrame({ anchor: [0, 0, 1], clipFromLocal: matrix.elements, width: 400, height: 400 });
  assert.equal(frame.project([0, 0]).visible, true); assert.equal(Directional.radialSectorPaths({ frame, radials: f.descriptors.get(f.ids[0]).state.radials }), null);
  assert.equal(f.refresh(), null); f.mountRadial(); emit(f.hub, 'pointerdown', { clientX: 240, clientY: 260 });
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.deepEqual(f.state.patches, []); f.dispose();
});

for (const [name, change] of [
  ['non-preview provenance', descriptor => { descriptor.truth_role = 'reported'; }],
  ['physical command provenance', descriptor => { descriptor.physical_commands = true; }],
]) test('directional input rejects ' + name, () => {
  const f = fixture(); change(f.descriptors.get(f.ids[0])); assert.equal(f.context.getDirectionalSnapshot(f.ids[0]), null); f.dispose();
});

test('the real inspector Show in room callback uses guarded Zones navigation without output', () => {
  const f = fixture({ mode: 'spot' }); actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onShowComposer'), f.context)();
  assert.deepEqual(f.state.calls, [['mode', 'zones'], ['tab', 'light']]); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('the actual initial inspector tab and room tool both default to Spot and Aim', () => {
  const f = fixture();
  assert.equal(actual.initialState('fixtureInteractionMode', f.context), 'spot');
  assert.equal(actual.initialState('virtualInspectorTab', f.context), 'aim'); f.dispose();
});

for (const [tab, mode] of [['light', 'zones'], ['aim', 'spot']]) {
  test('the actual inspector ' + tab + ' tab selects its matching room tool', () => {
    const f = fixture({ mode: mode === 'zones' ? 'spot' : 'zones' });
    actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context)(tab);
    assert.equal(f.values.virtualInspectorTab, tab); assert.equal(f.values.fixtureInteractionModeRef.current, mode);
    assert.deepEqual(f.state.patches, []); f.dispose();
  });
}

test('the inspector Setup tab preserves the selected room tool', () => {
  const f = fixture(); actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context)('setup');
  assert.equal(f.values.virtualInspectorTab, 'setup'); assert.equal(f.values.fixtureInteractionModeRef.current, 'zones');
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('tab navigation cannot switch tools underneath an active radial owner', () => {
  const f = fixture(); f.mountRadial(); queuePaint(f); const owner = f.values.radialGestureOwnerRef.current;
  actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context)('aim');
  assert.equal(f.values.fixtureInteractionModeRef.current, 'zones'); assert.equal(f.values.virtualInspectorTab, 'light');
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('a newly admitted equipment binding resets the inspector and tool together', () => {
  const f = fixture(); f.values.virtualFixtureStatesRef.current.key = 'previous-binding'; f.mountPreview();
  assert.equal(f.values.fixtureInteractionModeRef.current, 'spot'); assert.equal(f.values.virtualInspectorTab, 'aim');
  assert.deepEqual(copy(f.values.virtualAimHistoryRef.current), { undo: [], redo: [] }); f.dispose();
});

test('readmitting the same equipment binding preserves its inspector and tool choice', () => {
  const f = fixture(); f.values.virtualFixtureStatesRef.current.key = f.values.virtualFixtureBindingKey;
  const history = f.values.virtualAimHistoryRef.current; f.mountPreview();
  assert.equal(f.values.fixtureInteractionModeRef.current, 'zones'); assert.equal(f.values.virtualInspectorTab, 'light');
  assert.equal(f.values.virtualAimHistoryRef.current, history); assert.deepEqual(f.state.patches, []); f.dispose();
});

function renderComposer(f, projection = f.values.directionalProjectionRef.current) {
  f.context.anchors = { __directional: projection }; f.context.radialReadout = f.state.readout;
  f.context.window.AptDirectionalComposer = function AuthoredComposerElement() {};
  f.context.React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) };
  f.context.ReactDOM = { createPortal: (element, host) => ({ element, host }) };
  f.context.directionalDisplay = actual.initializer('directionalDisplay', f.context);
  return actual.renderExpression(actual.portal('AptDirectionalComposer'), f.context);
}

for (const [name, change] of [
  ['generation', f => { f.descriptors.get(f.ids[0]).generation++; }],
  ['equipment revision', f => { f.descriptors.get(f.ids[0]).equipment_revision++; f.document.revision++; f.state.previewRevision++; }],
  ['equipment digest', f => { const digest = 'e'.repeat(64); f.descriptors.get(f.ids[0]).equipment_digest = digest; f.document.digest = digest; f.state.previewDigest = digest; }],
  ['anchor', f => { f.descriptors.get(f.ids[0]).display_anchor_m[0] += .25; }],
  ['azimuth', f => { f.descriptors.get(f.ids[0]).state.radials[0].azimuth_deg += 5; }],
  ['manifest binding', f => { f.document.binding.manifest_digest = 'e'.repeat(64); f.descriptors.get(f.ids[0]).binding.manifest_digest = 'e'.repeat(64); }],
]) test('actual portal withdraws a cached drawing immediately after admitted ' + name + ' changes', () => {
  const f = fixture(), cached = f.values.directionalProjectionRef.current; assert.ok(renderComposer(f, cached));
  change(f); assert.ok(f.context.getDirectionalSnapshot(f.ids[0]), 'the new descriptor is itself admitted');
  assert.equal(Boolean(renderComposer(f, cached)), false, 'no stale drawing waits for the next 80ms HUD publication');
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('actual portal retains the committed drawing when only modeled radial levels change', () => {
  const f = fixture(), cached = f.values.directionalProjectionRef.current;
  f.descriptors.get(f.ids[0]).state.radials[0].brightness = .75;
  const rendered = renderComposer(f, cached); assert.ok(rendered); assert.equal(rendered.host, f.host);
  assert.deepEqual(f.state.patches, []); f.dispose();
});

function realRigFixture() {
  const f = fixture(), camera = new THREE.PerspectiveCamera(50, 1, .1, 100);
  const rig = createRig(camera); f.engine.camera = camera; f.engine.rig = rig;
  f.engine.apartmentRoot.rotation.x = -Math.PI / 2; f.engine.apartmentRoot.updateMatrixWorld(true);
  rig.fitBounds(new THREE.Box3(new THREE.Vector3(-4, 0, -4), new THREE.Vector3(4, 3, 4)), { dur: 0 });
  const step = () => { rig.update(1 / 60); camera.updateMatrixWorld(true); };
  rig.hoverPivot(.8, -.6); step(); assert.ok(f.refresh(), 'the actual fitted rig admits the authored fixture plane');
  return { ...f, rig, step };
}

test('actual createRig ambient settling preserves painting against the same committed plane', () => {
  const f = realRigFixture(), committed = f.values.directionalProjectionRef.current;
  const before = f.engine.camera.matrixWorldInverse.toArray(), key = f.context.directionalCameraKey(f.engine);
  f.step(); assert.notDeepEqual(f.engine.camera.matrixWorldInverse.toArray(), before, 'actual double-lerp settling changes the view matrix');
  assert.equal(f.context.directionalCameraKey(f.engine), key, 'unchanged public navigation keeps the drawn plane admitted');
  f.mountRadial(); emit(f.hub, 'pointerdown', f.point([0, 0], committed));
  const owner = f.values.radialGestureOwnerRef.current; assert.ok(owner);
  const downMatrix = f.engine.camera.matrixWorldInverse.toArray();
  for (let frame = 0; frame < 6; frame++) f.step();
  assert.notDeepEqual(f.engine.camera.matrixWorldInverse.toArray(), downMatrix, 'ambient motion genuinely continues during the gesture');
  emit(f.host, 'pointermove', f.point([1.1, 0], committed));
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(owner.projection, committed);
  assert.ok(owner.pending?.radials.length); f.flush(); assert.equal(f.state.patches.length, 1); f.dispose();
});

test('the HUD cannot publish an undrawn frame; the actual layout commit advances active painting', () => {
  const f = realRigFixture(); f.mountRadial(); f.mountTimer(); queuePaint(f);
  const owner = f.values.radialGestureOwnerRef.current, drawn = owner.projection;
  for (let frame = 0; frame < 6; frame++) f.step();
  f.state.intervals[0].callback(); const pendingRender = f.state.projection.__directional;
  assert.ok(pendingRender); assert.notEqual(pendingRender, drawn);
  assert.equal(f.values.directionalProjectionRef.current, drawn, 'the timer has not claimed React committed its geometry');
  assert.equal(owner.projection, drawn);
  const rendered = renderComposer(f, pendingRender); assert.ok(rendered);
  assert.equal(rendered.element.props.projection, pendingRender.frame, 'the real portal consumes the pending render geometry');
  f.commit(f.state.projection); assert.equal(f.values.directionalProjectionRef.current, pendingRender);
  emit(f.host, 'pointermove', f.point([1.1, 0], pendingRender));
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(owner.projection, pendingRender);
  assert.ok(Math.abs(owner.gesture.readback().radius - 1.1) < 1e-8, 'input inverts the frame actually committed'); f.dispose();
});

test('actual createRig navigation retires pending paint while ambient drift alone does not', () => {
  const f = realRigFixture(); f.mountRadial(); queuePaint(f);
  f.rig.stepAzimuth(1, 0); f.step(); f.flush();
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.deepEqual(f.state.patches, []);
  assert.equal(f.host.captures.size, 0); f.dispose();
});

test('an actual rig navigation tween withdraws the drawing and cannot start painting', () => {
  const f = realRigFixture(), drawn = f.values.directionalProjectionRef.current;
  f.rig.stepAzimuth(1, 450); f.step(); assert.equal(f.rig.isTweening(), true);
  assert.equal(f.context.getDirectionalSnapshot(f.ids[0]), null); assert.equal(f.refresh(), null);
  f.mountRadial(); emit(f.hub, 'pointerdown', f.point([0, 0], drawn));
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('a null presentation is published by the actual layout effect and retires its owner', () => {
  const f = fixture(); f.mountRadial(); queuePaint(f); f.commit({});
  assert.equal(f.values.directionalProjectionRef.current, null); f.flush();
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('the real composer receives active drag ownership through the actual portal', () => {
  const f = fixture(); f.mountRadial(); queuePaint(f);
  assert.equal(renderComposer(f).element.props.ownedGesture, true);
  emit(f.window, 'keydown', { key: 'Escape' }); assert.equal(renderComposer(f).element.props.ownedGesture, false); f.dispose();
});

test('the actual composer places its pill above the hub and hides it during owned dragging', () => {
  const source = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment-cards.jsx'), 'utf8');
  const program = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
  const declarations = ['aptDirectionalTone', 'AptDirectionalComposer'].map(name => {
    const nodes = program.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
    assert.equal(nodes.length, 1); return source.slice(nodes[0].start, nodes[0].end);
  });
  const context = vm.createContext({ window: { HomeApartmentDirectional: Directional }, CARD_FONT_SANS: 'authored-sans', CARD_FONT_MONO: 'authored-mono',
    React: { Fragment: 'fragment', useId: () => ':authored-composer:',
      createElement: (type, props, ...children) => ({ type, props: { ...props, children: children.flat(Infinity) } }) } });
  vm.runInContext(babel.transform(declarations.join('\n'), { presets: ['react'] }).code, context, { timeout: 1000 });
  const f = fixture(), projection = f.values.directionalProjectionRef.current;
  const props = { projection: projection.frame, descriptor: projection.descriptor, geometry: projection.geometry, mode: 'zones' };
  const nodes = elementNodes(context.AptDirectionalComposer({ ...props, ownedGesture: false }));
  const pill = nodes.find(node => node.props?.['aria-label'] === 'Room lighting tool'); assert.ok(pill);
  const button = elementNodes(pill).find(node => node.type === 'button'); assert.ok(button);
  const lowerEdge = pill.props.style.top + button.props.style.height + 2 * pill.props.style.padding + 2;
  assert.ok(lowerEdge < projection.geometry.hub.y - 22, 'the source pill box clears the hub pickup region above it');
  const activeNodes = elementNodes(context.AptDirectionalComposer({ ...props, ownedGesture: true, readout: { zone: 2, level: .5 } }));
  assert.equal(activeNodes.some(node => node.props?.['aria-label'] === 'Room lighting tool'), false);
  const gradients = nodes.filter(node => node.type === 'linearGradient'); assert.equal(gradients.length, 6);
  for (const item of projection.geometry.zones) {
    const gradient = gradients.find(node => node.props.id.endsWith('-zone-' + item.number)); assert.ok(gradient);
    assert.equal(gradient.props.gradientUnits, 'userSpaceOnUse');
    assert.deepEqual([gradient.props.x1, gradient.props.y1], [projection.geometry.hub.x, projection.geometry.hub.y]);
    assert.deepEqual([gradient.props.x2, gradient.props.y2], [item.node.x, item.node.y]);
    const stops = elementNodes(gradient).filter(node => node.type === 'stop').map(node => Number(node.props.stopOpacity));
    assert.ok(stops.length >= 2 && stops.every((opacity, index) => index === 0 || opacity < stops[index - 1]));
    assert.equal(stops.at(-1), 0, 'the projected lobe fades outward to transparent');
    assert.equal(nodes.find(node => node.props?.['data-apt-radial-start'] === 'zone:' + item.number)?.props.fill, 'url(#' + gradient.props.id + ')');
  }
  const white = activeNodes.filter(node => node.type === 'circle' && node.props.r === '5' && node.props.fill === '#fff');
  assert.equal(white.length, 1); assert.equal(white[0].props.cx, projection.geometry.zones[1].node.x);
  assert.equal(white[0].props.cy, projection.geometry.zones[1].node.y);
  assert.equal(nodes.some(node => node.type === 'circle' && node.props.r === '5' && node.props.fill === '#fff'), false);
  assert.ok(nodes.some(node => node.props?.['data-apt-radial-start'] === 'hub')); f.dispose();
});

function inspector() {
  const states = [], effectDeps = new Map(), effects = []; let cursor = 0;
  const element = (type, props, ...children) => ({ type, props: { ...(props || {}), children: children.flat(Infinity) } });
  const context = vm.createContext({ window: {}, React: { createElement: element, Fragment: 'fragment' },
    useState(initial) { const index = cursor++; if (!Object.hasOwn(states, index)) states[index] = initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }]; },
    useRef(initial) { const index = cursor++; if (!Object.hasOwn(states, index)) states[index] = { current: initial }; return states[index]; },
    useEffect(callback, dependencies) {
      const index = cursor++, previous = effectDeps.get(index);
      if (!previous || dependencies.some((value, offset) => !Object.is(value, previous[offset]))) effects.push(callback);
      effectDeps.set(index, dependencies);
    }, AptUnifiedIcon() {}, AptUnifiedRange() {} });
  const source = actual.text(actual.component('aptUnifiedRadialPowerPatch')) + '\n' + actual.text(actual.component('AptUnifiedVirtualInspector'));
  vm.runInContext(babel.transform(source, { presets: ['react'] }).code, context, { timeout: 1000 });
  return { context, render(props) { cursor = 0; return context.AptUnifiedVirtualInspector(props); },
    // Explicit authored passive-effect scheduling, not a React mount.
    flushEffects() { for (const callback of effects.splice(0)) callback(); } };
}
function elementNodes(tree) {
  const result = [];
  const visit = node => { if (!node || typeof node !== 'object') return; result.push(node); (node.props?.children || []).forEach(visit); };
  visit(tree); return result;
}
function elementText(tree) {
  return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : (tree?.props?.children || []).map(elementText).join('');
}
function inspectorProps(f, patches) {
  const fixture = copy(f.descriptors.get(f.ids[0]));
  return { homeName: 'Authored Home', fixtures: [fixture], fixture, selected: true, visible: true, tab: 'light',
    onPatch: patch => patches.push(copy(patch)), onShowComposer: () => {}, onSelect() {}, onBack() {}, onVisible() {}, onPatchAll() {},
    onUndo() {}, onRedo() {}, onTabChange() {}, canUndo: false, canRedo: false, sourceLabel: 'Authored modeled preview' };
}

test('actual inspector retains native all-zone preset and range controls when the composer is unavailable', () => {
  const f = fixture(), component = inspector(), patches = [], nodes = elementNodes(component.render(inspectorProps(f, patches)));
  assert.equal(component.context.window.AptDirectionalRing, undefined);
  nodes.find(node => node.type === 'button' && elementText(node) === '25%').props.onClick();
  assert.deepEqual(patches[0], { radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, on: true, brightness: .25 })) });
  const range = nodes.find(node => node.props?.ariaLabel === 'Radial brightness'); assert.ok(range); range.props.onChange(80);
  assert.deepEqual(patches[1], { radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, on: true, brightness: .8 })) });
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('actual inspector ring selection narrows the same native fallback controls to one existing zone', () => {
  const f = fixture(), component = inspector(), patches = [], props = inspectorProps(f, patches);
  component.context.window.AptDirectionalRing = function AuthoredRingElement() {};
  const first = elementNodes(component.render(props)), ring = first.find(node => node.type === component.context.window.AptDirectionalRing);
  assert.ok(ring); ring.props.onZone(3);
  const next = elementNodes(component.render(props)); next.find(node => node.type === 'button' && elementText(node) === '50%').props.onClick();
  assert.deepEqual(patches, [{ radials: [{ number: 3, on: true, brightness: .5 }] }]);
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('a fresh inspector hook instance retains Light and Zones for an old aim notice, but routes a new notice', () => {
  const f = fixture(), component = inspector(), props = inspectorProps(f, []);
  props.onTabChange = actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context);
  const retained = { ...props, notice: 'Aim updated in this virtual preview.' };
  component.render(retained); component.flushEffects();
  assert.equal(f.values.virtualInspectorTab, 'light'); assert.equal(f.values.fixtureInteractionModeRef.current, 'zones');
  assert.deepEqual(f.state.calls, [], 'a retained status message cannot navigate a fresh inspector instance');
  const changed = { ...retained, notice: 'Aiming at the authored wall.' };
  component.render(changed); component.flushEffects();
  assert.equal(f.values.virtualInspectorTab, 'aim'); assert.equal(f.values.fixtureInteractionModeRef.current, 'spot');
  assert.deepEqual(f.state.calls, [['mode', 'spot'], ['tab', 'aim']]);
  props.onTabChange('light'); f.state.calls.length = 0;
  component.render({ ...changed, tab: 'light' }); component.flushEffects();
  assert.equal(f.values.virtualInspectorTab, 'light'); assert.equal(f.values.fixtureInteractionModeRef.current, 'zones');
  assert.deepEqual(f.state.calls, [], 'the same notice does not override a later explicit tab choice'); f.dispose();
});

test('changed non-aim notices preserve the inspector tab and tool choice', () => {
  const f = fixture(), component = inspector(), props = inspectorProps(f, []);
  props.onTabChange = actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context);
  component.render({ ...props, notice: 'Aim updated in this virtual preview.' }); component.flushEffects();
  component.render({ ...props, notice: 'Authored modeled channels updated.' }); component.flushEffects();
  assert.equal(f.values.virtualInspectorTab, 'light'); assert.equal(f.values.fixtureInteractionModeRef.current, 'zones');
  assert.deepEqual(f.state.calls, []); assert.deepEqual(f.state.patches, []); f.dispose();
});
