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
const presentationSource = fs.readFileSync(path.join(ROOT, 'app/src/home-3d/apartment-directional-presentation.mjs'), 'utf8');
assert.equal((presentationSource.match(/from 'three'/g) || []).length, 1);
const Presentation = await import('data:text/javascript;base64,' + Buffer.from(presentationSource.replace("from 'three'",
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
  function assignment(name, context) {
    const matches = owner.body.body.filter(node => node.type === 'ExpressionStatement' && node.expression?.type === 'AssignmentExpression'
      && node.expression.left?.object?.name === name);
    assert.equal(matches.length, 1, 'one actual live assignment: ' + name);
    return vm.runInContext(text(matches[0]), context, { timeout: 1000 });
  }
  return { owner, text, initializer, initialState, install, effect, component, prop, portal, renderExpression, assignment,
    evaluate: (node, context) => vm.runInContext('(' + text(node) + ')', context, { timeout: 1000 }) };
}

class AuthoredTarget {
  constructor(tag = 'div', parent = null) {
    this.tagName = tag.toUpperCase(); this.parentElement = parent; this.style = {};
    this.listeners = new Map(); this.attributes = new Map(); this.captures = new Set();
    this.isContentEditable = false; this.isConnected = true;
    this.clientWidth = 400; this.clientHeight = 400;
    this.children = []; this.ownerDocument = parent?.ownerDocument; this.hidden = false; this.textContent = '';
  }
  appendChild(child) { child.remove(); this.children.push(child); child.parentElement = this; child.ownerDocument = this.ownerDocument; child.isConnected = this.isConnected; return child; }
  remove() { if (this.parentElement) { const index = this.parentElement.children.indexOf(this); if (index >= 0) this.parentElement.children.splice(index, 1); this.parentElement = null; } this.isConnected = false; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  querySelectorAll(selector) {
    const found = [], match = /^\[([^=\]]+)(?:="([^"]+)")?\]$/.exec(selector); assert.ok(match);
    for (const child of this.children) { if (child.hasAttribute(match[1]) && (match[2] === undefined || child.getAttribute(match[1]) === match[2])) found.push(child); found.push(...child.querySelectorAll(selector)); }
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getContext(kind) { assert.equal(kind, '2d'); return { createRadialGradient: () => ({ addColorStop() {} }), fillRect() {} }; }
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
  const domDocument = new AuthoredTarget('document'); domDocument.visibilityState = 'visible';
  domDocument.createElement = tag => { const node = new AuthoredTarget(tag); node.ownerDocument = domDocument; return node; };
  host.ownerDocument = domDocument;
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
    previewRevision: 7, previewDigest: document.digest, previewVisible: true, previewStatus: 'ready', readout: null, projection: null, panel: 'controls', previewPublications: 0, nowMs: 0 };
  const preview = () => ({ status: state.previewStatus, visible: state.previewVisible, revision: state.previewRevision,
    digest: state.previewDigest, fixtures: ids.map(id => ({ fixture_id: id, label: id, state: copy(descriptors.get(id).state) })) });
  const overlay = {
    virtualFixturesReadback: preview, virtualFixtureInteractionDescriptor: id => descriptors.has(id) ? copy(descriptors.get(id)) : null,
    virtualFixturePickObjects: () => [], personGroup: { visible: false }, markersById: new Map(),
    setVirtualFixtureSelected: id => { state.calls.push(['selected', id]); return overlay.virtualFixtureInteractionDescriptor(id); },
    setVirtualFixtureState(id, patch) {
      state.patches.push({ id, patch: copy(patch) });
      const before = descriptors.get(id); before.state = nextState(before.state, patch); return { ok: true, ...preview() };
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
  const engineListeners = new Map(), layers = new Set(); state.running = true; state.layerRequests = [];
  engine.on = (name, listener) => { const rows = engineListeners.get(name) || new Set(); rows.add(listener); engineListeners.set(name, rows); return () => rows.delete(listener); };
  engine.createUiOverlayLayer = async options => {
    state.layerRequests.push(options); if (!state.running || options.signal.aborted || !options.isCurrent()) return null;
    const layer = { group: new THREE.Group(), disposed: false, dispose() { if (this.disposed) return false; this.disposed = true; layers.delete(this); options.signal.removeEventListener('abort', abort); options.onPresentationDispose(); return true; },
      frame(info) { if (this.disposed) return false; if (options.signal.aborted || !options.isCurrent()) { this.dispose(); return false; } options.onPresentationFrame(info); return true; } };
    const abort = () => layer.dispose(); options.signal.addEventListener('abort', abort); layers.add(layer); return layer;
  };
  const frames = new Map(); let frameId = 0;
  overlay.createVirtualFixtureAimGesture = options => createDirectAimGestureController({ ...options,
    requestFrame: callback => { const id = ++frameId; frames.set(id, callback); return id; }, cancelFrame: id => frames.delete(id) });
  window.HomeApartmentDirectional = Directional;
  window.AptDirectionalComposer = function AuthoredComposerElement() {};
  window.HomeSpatialViewport = { getSelectedSiteId: () => state.selectedSite };
  window.HomeOverlay = { topKey: () => 'apartment-embedded' };
  window.Home3D = { ready: Promise.resolve({ loadDirectionalPresentation: async () => Presentation }) };
  const values = { window, document: domDocument, AbortController, open: true, phase: 'ready', selectedSiteId: 'victoria', selectedContentRevision: binding.content_revision,
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
    directionalView: null, directionalViewRef: { current: null }, directionalProjectRef: { current: null },
    virtualFixturePreviewDirtyRef: { current: false }, publishVirtualFixturePreviewRef: { current: null },
    radialReadout: null, radialGestureActive: false, anchors: {},
    virtualFixtureStatesRef: { current: { states: new Map(), targets: new Map() } }, denDeskAimPreviewRef: { current: null },
    virtualAimHistoryRef: { current: { undo: [{ authored: 'previous aim' }], redo: [] } },
    virtualFixtureBindingKey: JSON.stringify(binding), hoverId: null, cardId: null, semanticDevicesForRoom: [],
    setVirtualFixturePreview: value => { state.previewPublications++; values.virtualFixtureItems = value.fixtures; },
    setVirtualFixtureId: value => { values.virtualFixtureId = value; state.calls.push(['fixture', value]); },
    setVirtualFixtureControlsOpen: value => { values.virtualFixtureControlsOpen = value; state.calls.push(['open', value]); },
    setFixtureInteractionMode: value => { values.fixtureInteractionMode = value; values.fixtureInteractionModeRef.current = value; state.calls.push(['mode', value]); },
    setVirtualInspectorTab: value => { values.virtualInspectorTab = value; state.calls.push(['tab', value]); },
    setRadialReadout: value => { state.readout = value; values.radialReadout = value; },
    setRadialGestureActive: value => { values.radialGestureActive = value; },
    setAnchors: value => { const next = typeof value === 'function' ? value(values.anchors) : value; state.projection = next; values.anchors = next; },
    setDirectionalView: value => { values.directionalView = typeof value === 'function' ? value(values.directionalView) : value; },
    onShowShellControls: () => { state.panel = 'controls'; state.calls.push(['show']); },
    setVirtualFixtureChannel: value => state.calls.push(['channel', value]), setVirtualAimNotice() {}, setVirtualAimHistoryVersion() {},
    restoreApartmentOperationFocus: value => state.calls.push(['focus', value]),
    callSvc: () => assert.fail('directional UI cannot send a physical command'), saveModel: () => assert.fail('directional UI cannot save equipment'),
    setInterval(callback, delay) { const record = { callback, delay, cleared: false }; state.intervals.push(record); return record; },
    clearInterval(record) { record.cleared = true; },
    requestAnimationFrame: () => assert.fail('directional integration cannot create a new RAF loop'),
  };
  const context = vm.createContext(values), cleanups = [];
  for (const name of ['selectionEngine', 'selectionLifecycle']) context[name] = actual.initializer(name, context);
  for (const name of ['getDirectionalSnapshot', 'isDirectionalProjectionCurrent', 'projectDirectionalFixture', 'changeFixtureInteractionMode', 'changeVirtualInspectorTab', 'selectVirtualFixture', 'changeVirtualFixture', 'reapplyVirtualFixtureMemory']) actual.install(name, context);
  values.directionalProjectRef.current = context.projectDirectionalFixture;
  actual.assignment('publishVirtualFixturePreviewRef', context);
  const mount = signature => {
    const cleanup = actual.evaluate(actual.effect(signature), context)();
    if (typeof cleanup === 'function') cleanups.push(cleanup);
  };
  const attach = view => {
    const root = domDocument.createElement('svg'), pill = domDocument.createElement('div'), readout = domDocument.createElement('div');
    for (let number = 1; number <= 6; number++) { const path = domDocument.createElement('path'); path.setAttribute('data-apt-radial-number', number); root.appendChild(path); }
    const hit = domDocument.createElement('circle'); hit.setAttribute('data-apt-radial-hub', 'hit'); root.appendChild(hit);
    host.appendChild(root); host.appendChild(pill); host.appendChild(readout); view.attach({ root, pill, readout });
    view.subscribe(() => { state.readout = view.getSnapshot().readout; });
    return { root, pill, readout };
  };
  let elements, presentationGroup;
  const refresh = () => {
    const initial = context.getDirectionalSnapshot(values.virtualFixtureId, { drawing: false });
    let view = values.directionalViewRef.current;
    if (!initial) { view?.dispose(); return null; }
    if (!view || view.readback().disposed || view.readback().identity !== Directional.identityKey(initial.descriptor)) {
      view?.dispose();
      presentationGroup = new THREE.Group();
      view = Presentation.createDirectionalPresentation({ group: presentationGroup, host, api: Directional, initial,
        getProjection: () => values.directionalProjectRef.current(), getMode: () => values.fixtureInteractionModeRef.current,
        publishPlane: value => { values.directionalProjectionRef.current = value; },
        isCurrent: () => !!context.getDirectionalSnapshot(values.virtualFixtureId, { drawing: false }),
        onRetire: () => { if (values.directionalViewRef.current === view) { values.directionalViewRef.current = null; values.directionalView = null; } } });
      values.directionalViewRef.current = view; values.directionalView = view; elements = attach(view);
    }
    view.frame({ nowMs: state.nowMs += 16 });
    return values.directionalProjectionRef.current;
  };
  refresh();
  return { state, values, context, ids, descriptors, binding, document, domDocument, engine, window, host, canvas, hub, refresh, attach,
    get elements() { return elements; },
    get presentationGroup() { return presentationGroup; },
    mountRadial: () => mount('const current = owner =>'), mountTimer: () => mount('virtualFixturePreviewDirtyRef.current &&'),
    mountPresentation: () => { values.directionalViewRef.current?.dispose(); values.directionalView = null; mount('let closed = false, lease = null'); },
    mountPreview: () => mount('overlay.setVirtualFixtures(document)'),
    mountKeyboard: () => mount('const semanticCycleFromCard'), mountSpot: () => mount('overlay.createVirtualFixtureAimGesture'),
    point(relative = [0, 0], projection = values.directionalProjectionRef.current) {
      const point = projection.frame.project(relative), rect = host.getBoundingClientRect();
      assert.ok(point?.visible); return { clientX: rect.left + point.x * rect.width / host.clientWidth,
        clientY: rect.top + point.y * rect.height / host.clientHeight };
    },
    checkOwner: () => { const owner = values.radialGestureOwnerRef.current; if (owner) emit(host, 'pointermove', { ...pointFor(owner.projection, [1.1, 0]) }); },
    emitRunning(running) { state.running = running; if (!running) for (const layer of [...layers]) layer.dispose(); for (const callback of [...engineListeners.get('running') || []]) callback({ running }); },
    frame() { for (const layer of [...layers]) layer.frame({ nowMs: state.nowMs += 16, deltaSeconds: .016 }); },
    listeners: engineListeners, layers,
    dispose() { for (const cleanup of cleanups.splice(0).reverse()) cleanup(); values.directionalViewRef.current?.dispose(); },
  };
  function pointFor(projection, relative) { const point = projection.frame.project(relative), rect = host.getBoundingClientRect(); return { clientX: rect.left + point.x * rect.width / host.clientWidth, clientY: rect.top + point.y * rect.height / host.clientHeight }; }
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

function paintNow(f) {
  emit(f.canvas, 'pointerdown', f.point());
  assert.equal(f.values.radialGestureOwnerRef.current?.gesture.readback().active, true);
  assert.ok(f.state.readout, 'down publishes the existing zone level immediately');
  assert.equal(f.state.patches.length, 0, 'pickup alone does not write');
  emit(f.host, 'pointermove', f.point([1.1, 0]));
  assert.ok(f.values.radialGestureOwnerRef.current?.lastPatch);
  assert.equal(f.state.patches.length, 1, 'the admitted modeled setter completes in this input turn');
  assert.equal(f.state.readout.zone, 1); assert.equal(f.state.readout.level, .83);
}

test('input immediately applies only modeled radials/readout; the 80 ms HUD publishes summary only after the gesture', () => {
  const f = fixture(), before = copy(f.descriptors.get(f.ids[0])), history = f.values.virtualAimHistoryRef.current;
  f.mountRadial(); f.mountTimer(); assert.equal(f.state.intervals.length, 1); assert.equal(f.state.intervals[0].delay, 80);
  paintNow(f); assert.ok(f.state.readout); assert.equal(f.state.previewPublications, 0);
  const drawn = f.values.directionalProjectionRef.current;
  f.state.intervals[0].callback(); assert.equal(f.state.patches.length, 1);
  assert.equal(f.state.previewPublications, 0, 'an active gesture keeps the unrelated full-room React summary deferred');
  assert.equal(f.values.directionalProjectionRef.current, drawn, 'HUD cannot claim a newly drawn plane');
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
  assert.ok(f.state.readout); assert.ok(f.values.directionalProjectionRef.current.geometry);
  assert.equal(f.values.virtualFixtureStatesRef.current.states.get(id).radials[0].brightness, patch.radials[0].brightness, 'remembered state follows the setter immediately');
  assert.deepEqual(copy(f.values.virtualFixtureStatesRef.current.targets.get(id)), before.target);
  f.state.intervals[0].callback(); assert.equal(f.state.patches.length, 1, 'already flushed intent is not repeated');
  assert.deepEqual(f.state.calls, [['selected', f.ids[0]], ['fixture', f.ids[0]], ['open', true], ['show']],
    'painting admission only reuses the existing selection/reveal callback');
  emit(f.window, 'keydown', { key: 'Escape' }); f.state.intervals[0].callback();
  assert.equal(f.state.previewPublications, 1); assert.equal(f.values.virtualFixturePreviewDirtyRef.current, false);
  const anchors = f.values.anchors; f.state.intervals[0].callback(); assert.equal(f.values.anchors, anchors, 'identical generic HUD anchors retain their state identity');
  f.dispose(); assert.equal(f.state.intervals[0].cleared, true);
});

test('pointerup applies a different final modeled level synchronously and releases only its owned capture', () => {
  const f = fixture(); f.mountRadial(); paintNow(f); emit(f.host, 'pointerup', f.point([1.3, 0]));
  assert.equal(f.state.patches.length, 2); assert.equal(f.descriptors.get(f.ids[0]).state.radials[0].brightness, 1); assert.equal(f.values.radialGestureOwnerRef.current, null);
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
]) test('actual radial owner retains applied levels, stops later writes and releases capture on ' + name, () => {
  const f = fixture(); f.mountRadial(); paintNow(f); const applied = copy([...f.descriptors]); cancel(f); f.checkOwner();
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.host.captures.size, 0);
  assert.equal(f.state.patches.length, 1); assert.deepEqual(copy([...f.descriptors]), applied);
  assert.equal(f.values.virtualFixtureId, f.ids[0]); assert.equal(f.values.virtualFixtureControlsOpen, true); f.dispose();
});

test('cancel retains both earlier and latest synchronous modeled levels without any late write', () => {
  const f = fixture(); f.mountRadial(); paintNow(f);
  emit(f.host, 'pointermove', f.point([0, 1.1])); assert.equal(f.state.patches.length, 2); const applied = copy([...f.descriptors]);
  emit(f.window, 'keydown', { key: 'Escape' }); f.checkOwner();
  assert.equal(f.state.patches.length, 2); assert.deepEqual(copy([...f.descriptors]), applied); f.dispose();
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
]) test('a later directional input cannot cross retired ' + name, () => {
  const f = fixture(); f.mountRadial(); paintNow(f); retire(f); f.checkOwner();
  assert.equal(f.state.patches.length, 1); assert.equal(f.values.radialGestureOwnerRef.current, null);
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
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(owner.lastPatch, null); assert.deepEqual(f.state.patches, []); f.dispose();
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

test('pressing the already selected fixture in Zones reveals Controls through the existing guarded callback', () => {
  const f = fixture(), before = copy([...f.descriptors]), history = f.values.virtualAimHistoryRef.current;
  f.state.panel = 'chat'; f.mountRadial();
  const event = emit(f.canvas, 'pointerdown', f.point());
  assert.equal(event.defaultPrevented, true); assert.equal(f.state.panel, 'controls');
  assert.deepEqual(f.state.calls, [['selected', f.ids[0]], ['fixture', f.ids[0]], ['open', true], ['show']]);
  assert.equal(f.values.virtualFixtureId, f.ids[0]); assert.equal(f.values.virtualFixtureControlsOpen, true);
  assert.equal(f.values.virtualAimHistoryRef.current, history); assert.deepEqual(copy([...f.descriptors]), before);
  assert.deepEqual(f.state.patches, []); assert.ok(f.values.radialGestureOwnerRef.current); f.dispose();
});

test('a declined same-fixture selection prevents Zones painting and cannot reveal Controls', () => {
  const f = fixture(), before = copy([...f.descriptors]); f.state.panel = 'chat';
  f.engine.overlay.setVirtualFixtureSelected = id => { f.state.calls.push(['selection-declined', id]); return null; };
  f.mountRadial(); emit(f.canvas, 'pointerdown', f.point());
  assert.deepEqual(f.state.calls, [['selection-declined', f.ids[0]]]); assert.equal(f.state.panel, 'chat');
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.host.captures.size, 0);
  assert.equal(f.values.virtualFixtureId, f.ids[0]); assert.equal(f.values.virtualFixtureControlsOpen, true);
  assert.deepEqual(copy([...f.descriptors]), before); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('Spot routes fixture input to the actual existing direct-aim controller', () => {
  const f = fixture({ mode: 'spot' }); f.mountSpot(); f.mountRadial(); const event = emit(f.canvas, 'pointerdown', f.point());
  assert.equal(event.defaultPrevented, true); assert.equal(f.values.radialGestureOwnerRef.current, null);
  assert.equal(f.values.virtualAimGestureRef.current.readback().active, true); assert.equal(f.values.virtualFixtureId, f.ids[0]); f.dispose();
});

test('A/Z uses the actual mode callback and changes the matching inspector tab', () => {
  const f = fixture({ mode: 'spot' }); f.mountKeyboard(); const zones = emit(f.host, 'keydown', { key: 'z' });
  assert.equal(zones.defaultPrevented, true); assert.deepEqual(f.state.calls, [['mode', 'zones'], ['tab', 'light']]);
  assert.equal(f.values.directionalViewRef.current.getSnapshot().mode, 'zones', 'pill selection publishes before another renderer frame');
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
  const f = fixture(); f.mountRadial(); f.mountKeyboard(); paintNow(f); emit(f.host, 'keydown', { key: 'a' });
  assert.equal(f.values.fixtureInteractionModeRef.current, 'zones');
  assert.deepEqual(f.state.calls, [['selected', f.ids[0]], ['fixture', f.ids[0]], ['open', true], ['show']],
    'the key adds no mode/tab calls to the existing selection/reveal admission'); f.dispose();
});

for (const [name, change] of [
  ['default-prevented Escape', event => { event.defaultPrevented = true; }],
  ['child modal', (_, f) => { f.window.HomeOverlay.topKey = () => 'authored-child-modal'; }],
]) test('radial Escape preserves ' + name + ' ownership', () => {
  const f = fixture(); f.mountRadial(); paintNow(f); const owner = f.values.radialGestureOwnerRef.current, detail = { key: 'Escape' };
  change(detail, f); emit(f.window, 'keydown', detail);
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(f.state.patches.length, 1); f.dispose();
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
    assert.equal(f.values.directionalViewRef.current.getSnapshot().mode, mode, 'mode control does not wait for the next renderer frame');
    assert.deepEqual(f.state.patches, []); f.dispose();
  });
}

test('the inspector Setup tab preserves the selected room tool', () => {
  const f = fixture(); actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context)('setup');
  assert.equal(f.values.virtualInspectorTab, 'setup'); assert.equal(f.values.fixtureInteractionModeRef.current, 'zones');
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('tab navigation cannot switch tools underneath an active radial owner', () => {
  const f = fixture(); f.mountRadial(); paintNow(f); const owner = f.values.radialGestureOwnerRef.current;
  actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'onTabChange'), f.context)('aim');
  assert.equal(f.values.fixtureInteractionModeRef.current, 'zones'); assert.equal(f.values.virtualInspectorTab, 'light');
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(f.state.patches.length, 1); f.dispose();
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

function renderComposer(f) {
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

function stationaryPointerAcrossDrawnDrift() {
  const f = realRigFixture(), future = realRigFixture();
  try {
    const selected = f.descriptors.get(f.ids[0]);
    for (const zone of selected.state.radials) { zone.on = true; zone.brightness = .01; }
    const initial = f.refresh(), firstHub = f.point([0, 0], initial);
    for (let frame = 0; frame < 6; frame++) future.step();
    const futureProjection = future.refresh(), futureHub = future.point([0, 0], futureProjection);
    // Independent instances run the same actual rig. The midpoint puts the
    // unchanged client pointer on opposite sides of the settling hub; both
    // local samples stay inside the pickup radius and imply no real drag.
    const pointer = { clientX: (firstHub.clientX + futureHub.clientX) / 2,
      clientY: (firstHub.clientY + futureHub.clientY) / 2 };
    const local = projection => {
      const rect = f.host.getBoundingClientRect();
      return projection.frame.unproject((pointer.clientX - rect.left) * f.host.clientWidth / rect.width,
        (pointer.clientY - rect.top) * f.host.clientHeight / rect.height);
    };
    const beforePoint = local(initial), afterPoint = local(futureProjection);
    assert.ok(Math.hypot(...beforePoint) < .14 && Math.hypot(...afterPoint) < .14);
    assert.notEqual(Directional.paintPatch({ point: beforePoint, radials: selected.state.radials }).zone,
      Directional.paintPatch({ point: afterPoint, radials: selected.state.radials }).zone,
      'actual ambient settling crosses a local nearest-zone boundary at the stationary client point');
    return { f, pointer, initial, local };
  } catch (error) { f.dispose(); throw error; }
  finally { future.dispose(); }
}

for (const eventType of ['pointerup', 'pointermove']) test('stationary ' + eventType + ' cannot paint when the actual rig republishes a drifting plane', () => {
  const { f, pointer, initial, local } = stationaryPointerAcrossDrawnDrift();
  try {
    const before = copy([...f.descriptors]), history = copy(f.values.virtualAimHistoryRef.current);
    f.state.panel = 'chat'; f.mountRadial(); emit(f.hub, 'pointerdown', pointer);
    assert.equal(f.state.panel, 'controls', 'stationary pickup still reveals the retained fixture controls');
    assert.ok(f.values.radialGestureOwnerRef.current); assert.equal(f.host.captures.size, 1); assert.deepEqual(f.state.patches, []);
    for (let frame = 0; frame < 6; frame++) f.step();
    const updated = f.refresh(); assert.notEqual(updated, initial); assert.equal(updated.key, initial.key);
    assert.notDeepEqual(local(updated), local(initial), 'the drawn plane really changes under the unchanged native client point');
    emit(f.host, eventType, pointer);
    assert.deepEqual(f.state.patches, [], 'camera settling alone is never modeled paint intent');
    assert.deepEqual(copy([...f.descriptors]), before, 'the saved .01 level, all other channels, aims and canonical identities remain exact');
    if (eventType === 'pointermove') emit(f.host, 'pointerup', pointer);
    assert.deepEqual(f.state.patches, []); assert.deepEqual(copy([...f.descriptors]), before);
    assert.deepEqual(copy(f.values.virtualAimHistoryRef.current), history);
    assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.host.captures.size, 0);
  } finally { f.dispose(); }
});

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
  assert.ok(owner.lastPatch); assert.equal(f.state.patches.length, 1); f.dispose();
});

test('the HUD cannot publish an undrawn plane; real presentation drawing advances the exact plane used by input', () => {
  const f = realRigFixture(); f.mountRadial(); f.mountTimer(); paintNow(f);
  const owner = f.values.radialGestureOwnerRef.current, drawn = owner.projection;
  for (let frame = 0; frame < 6; frame++) f.step();
  f.state.intervals[0].callback();
  assert.equal(f.state.projection.__directional, undefined);
  assert.equal(f.values.directionalProjectionRef.current, drawn, 'the generic HUD never claims a drawing commit');
  assert.equal(owner.projection, drawn);
  const pendingRender = f.refresh(); assert.ok(pendingRender); assert.notEqual(pendingRender, drawn);
  const rendered = renderComposer(f); assert.ok(rendered);
  assert.equal(rendered.element.props.view, f.values.directionalViewRef.current, 'the portal consumes the admitted presentation owner');
  assert.equal(f.values.directionalProjectionRef.current, pendingRender);
  const hitPath = f.elements.root.querySelector('[data-apt-radial-number="1"]');
  assert.equal(hitPath.getAttribute('d'), pendingRender.geometry.zones[0].path, 'native paths precede published plane ownership');
  emit(f.host, 'pointermove', f.point([1.1, 0], pendingRender));
  assert.equal(f.values.radialGestureOwnerRef.current, owner); assert.equal(owner.projection, pendingRender);
  assert.ok(Math.abs(owner.gesture.readback().radius - 1.1) < 1e-8, 'input inverts the frame actually committed'); f.dispose();
});

test('actual createRig navigation retires later paint while preserving previously applied levels', () => {
  const f = realRigFixture(); f.mountRadial(); paintNow(f);
  f.rig.stepAzimuth(1, 0); f.step(); f.checkOwner();
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.state.patches.length, 1);
  assert.equal(f.host.captures.size, 0); f.dispose();
});

test('an actual rig navigation tween withdraws the drawing and cannot start painting', () => {
  const f = realRigFixture(), drawn = f.values.directionalProjectionRef.current;
  f.rig.stepAzimuth(1, 450); f.step(); assert.equal(f.rig.isTweening(), true);
  assert.equal(f.context.getDirectionalSnapshot(f.ids[0]), null); assert.equal(f.refresh(), null);
  f.mountRadial(); emit(f.hub, 'pointerdown', f.point([0, 0], drawn));
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('a failed real drawing withdraws its plane and the next input retires its owner without a late write', () => {
  const f = fixture(); f.mountRadial(); paintNow(f); f.values.directionalProjectRef.current = () => null; f.refresh();
  assert.equal(f.values.directionalProjectionRef.current, null); f.checkOwner();
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.state.patches.length, 1); f.dispose();
});

test('the real composer receives active drag ownership through the actual portal', () => {
  const f = fixture(); f.mountRadial(); paintNow(f);
  assert.equal(renderComposer(f).element.props.view.getSnapshot().active, true);
  emit(f.window, 'keydown', { key: 'Escape' }); assert.equal(renderComposer(f).element.props.view.getSnapshot().active, false); f.dispose();
});

test('the actual composer attaches native hit paths/readout/pill to the admitted view and preserves visible drawing cues', () => {
  const source = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment-cards.jsx'), 'utf8');
  const program = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
  const declaration = program.body.find(node => node.type === 'FunctionDeclaration' && node.id?.name === 'AptDirectionalComposer'); assert.ok(declaration);
  const effects = [], refs = [], modes = [], f = fixture();
  const context = vm.createContext({ window: {}, CARD_FONT_SANS: 'authored-sans', CARD_FONT_MONO: 'authored-mono',
    useRef: () => { const ref = { current: null }; refs.push(ref); return ref; },
    React: { Fragment: 'fragment', useSyncExternalStore: (_, read) => read(), useLayoutEffect: callback => effects.push(callback),
      createElement: (type, props, ...children) => ({ type, props: { ...props, children: children.flat(Infinity) } }) } });
  vm.runInContext(babel.transform(source.slice(declaration.start, declaration.end), { presets: ['react'] }).code, context, { timeout: 1000 });
  const view = f.values.directionalViewRef.current, tree = context.AptDirectionalComposer({ view, onMode: value => modes.push(value) });
  const nodes = elementNodes(tree), pill = nodes.find(node => node.props?.['aria-label'] === 'Room lighting tool'); assert.ok(pill);
  const buttons = elementNodes(pill).filter(node => node.type === 'button'); assert.equal(buttons.length, 2);
  assert.deepEqual(buttons.map(node => node.props['aria-keyshortcuts']), ['Z', 'A']);
  assert.deepEqual(buttons.map(node => node.props['aria-pressed']), [true, false]);
  buttons[1].props.onClick(); assert.deepEqual(modes, ['spot']);
  let stopped = 0; pill.props.onPointerDownCapture({ stopPropagation: () => stopped++ }); pill.props.onClick({ stopPropagation: () => stopped++ }); assert.equal(stopped, 2);
  function mount(node, parent) {
    if (!node || typeof node !== 'object') return;
    const element = f.domDocument.createElement(node.type); parent.appendChild(element);
    for (const [key, value] of Object.entries(node.props)) {
      if (key === 'ref') value.current = element;
      else if (key === 'hidden') element.hidden = value;
      else if (key === 'style') Object.assign(element.style, value);
      else if (key.startsWith('data-') || key.startsWith('aria-')) element.setAttribute(key, value);
    }
    node.props.children.forEach(child => mount(child, element));
  }
  mount(tree, f.host); const cleanups = effects.map(effect => effect());
  assert.equal(refs.length, 3); assert.equal(refs[0].current.hidden, true, 'React does not publish a plane before drawing');
  const projection = f.refresh(); assert.equal(refs[0].current.hidden, false);
  assert.equal(refs[0].current.querySelectorAll('[data-apt-radial-number]').length, 6);
  assert.equal(refs[0].current.querySelector('[data-apt-radial-hub="hit"]').getAttribute('data-apt-radial-start'), 'hub');
  assert.match(refs[1].current.style.transform, new RegExp('translate3d\\(' + Math.round(projection.geometry.hub.x) + 'px,' + Math.round(projection.geometry.hub.y - 26) + 'px,0\\) translate\\(-50%,-100%\\)'));
  assert.equal(refs[1].current.hidden, false);
  view.pointer({ zone: 2, level: .5, x: 10, y: 10 }, true); f.refresh();
  assert.equal(refs[1].current.hidden, true); assert.equal(refs[2].current.hidden, false); assert.match(refs[2].current.textContent, /Zone 2.*50%/);
  const composer = f.presentationGroup.getObjectByName('apartment-directional-composer');
  const fill = composer.getObjectByName('directional-polar-fill'); assert.equal(fill.geometry.index.count, 720);
  assert.match(fill.material.fragmentShader, /pow\(1\.0-vT,1\.8\)/); assert.match(fill.material.fragmentShader, /smoothstep\(0\.8,1\.0,vT\)/);
  const active = composer.getObjectByName('directional-zone-node-2'); assert.deepEqual(active.material.color.toArray(), [1, 1, 1]); assert.equal(active.scale.x, .17);
  view.pointer(null, false); f.refresh(); assert.notDeepEqual(active.material.color.toArray(), [1, 1, 1]); assert.equal(refs[1].current.hidden, false);
  for (const cleanup of cleanups) cleanup?.(); assert.equal(f.values.directionalProjectionRef.current, null); f.dispose();
});
function inspector() {
  const states = [], effectDeps = new Map(), effects = []; let cursor = 0;
  const element = (type, props, ...children) => ({ type, props: { ...(props || {}), children: children.flat(Infinity) } });
  const context = vm.createContext({ window: {}, aptEmptySubscribe: () => () => {}, aptEmptySnapshot: () => null,
    React: { createElement: element, Fragment: 'fragment', useSyncExternalStore: (_, snapshot) => snapshot() },
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
  nodes.find(node => node.type === 'button' && elementText(node) === 'Off').props.onClick();
  assert.deepEqual(patches[2], { radials: Array.from({ length: 6 }, (_, index) => ({ number: index + 1, on: false, brightness: 0 })) },
    'the explicit Off preset retains its credited zero-brightness behavior');
  assert.deepEqual(f.state.patches, []); f.dispose();
});

for (const selectedZone of [null, 3]) test('actual inspector ' + (selectedZone == null ? 'master' : 'selected-zone')
  + ' power switches retain remembered nonzero levels through Off and On', () => {
  const f = fixture(), component = inspector();
  f.descriptors.get(f.ids[0]).state.radials.forEach((zone, index) => { zone.brightness = .2 + index * .08; });
  const before = copy(f.descriptors.get(f.ids[0])), props = inspectorProps(f, []);
  component.context.window.AptDirectionalRing = function AuthoredRingElement() {};
  props.onPatch = patch => {
    f.engine.overlay.setVirtualFixtureState(f.ids[0], patch);
    props.fixture = copy(f.descriptors.get(f.ids[0])); props.fixtures = [props.fixture];
  };
  const render = () => { const tree = component.render(props); component.flushEffects(); const nodes = elementNodes(tree);
    // The authored ring is a leaf boundary. Its native power element is passed
    // by the actual inspector, and the real ring renders that element unchanged.
    return [...nodes, ...nodes.flatMap(node => elementNodes(node.props?.power))]; };
  let nodes = render();
  if (selectedZone != null) {
    nodes.find(node => node.type === component.context.window.AptDirectionalRing).props.onZone(selectedZone); nodes = render();
  }
  const label = selectedZone == null ? 'Radial power' : `Radial zone ${selectedZone} power`;
  const affected = before.state.radials.filter(zone => selectedZone == null || zone.number === selectedZone);
  for (const on of [false, true]) {
    const control = nodes.find(node => node.type === 'input' && node.props?.['aria-label'] === label); assert.ok(control);
    control.props.onChange({ target: { checked: on } });
    assert.deepEqual(f.state.patches.at(-1), { id: f.ids[0], patch: { radials: affected.map(zone => ({ number: zone.number, on })) } },
      'power-only output must not include even unchanged brightness, temperature, pan, tilt or target fields');
    const after = copy(f.descriptors.get(f.ids[0]));
    assert.deepEqual(after.state.radials, before.state.radials.map(zone => ({ ...zone, on: selectedZone == null || zone.number === selectedZone ? on : zone.on })));
    for (const key of ['spotlight', 'downward', 'pan_deg', 'tilt_deg']) assert.deepEqual(after.state[key], before.state[key]);
    assert.deepEqual(after.target, before.target); nodes = render();
  }
  assert.deepEqual(copy(f.descriptors.get(f.ids[0])), before); f.dispose();
});

test('actual inspector ring selection narrows the native brightness range to one existing zone', () => {
  const f = fixture(), component = inspector(), patches = [], props = inspectorProps(f, patches);
  component.context.window.AptDirectionalRing = function AuthoredRingElement() {};
  const first = elementNodes(component.render(props)), ring = first.find(node => node.type === component.context.window.AptDirectionalRing);
  assert.ok(ring); ring.props.onZone(3);
  const next = elementNodes(component.render(props)); next.find(node => node.props?.ariaLabel === 'Radial brightness').props.onChange(50);
  assert.deepEqual(patches, [{ radials: [{ number: 3, on: true, brightness: .5 }] }]);
  assert.deepEqual(f.state.patches, []); f.dispose();
});

for (const value of [0, 25, 50, 100]) test(`actual ${value}% quick preset clears a selected zone and changes all six radial levels only`, () => {
  const f = fixture(), component = inspector(), props = inspectorProps(f, []), before = copy(f.descriptors.get(f.ids[0]));
  const Ring = component.context.window.AptDirectionalRing = function AuthoredRingElement() {};
  props.onPatch = patch => { f.engine.overlay.setVirtualFixtureState(f.ids[0], patch); props.fixture = copy(f.descriptors.get(f.ids[0])); props.fixtures = [props.fixture]; };
  const render = () => { const nodes = elementNodes(component.render(props)); component.flushEffects(); return nodes; };
  let nodes = render(); nodes.find(node => node.type === Ring).props.onZone(3); nodes = render();
  assert.equal(nodes.find(node => node.type === Ring).props.zone, 3);
  const label = value ? `${value}%` : 'Off'; nodes.find(node => node.type === 'button' && elementText(node) === label).props.onClick();
  assert.deepEqual(f.state.patches, [{ id: f.ids[0], patch: { radials: before.state.radials.map(zone => ({ number: zone.number, on: value > 0, brightness: value / 100 })) } }]);
  nodes = render(); assert.equal(nodes.find(node => node.type === Ring).props.zone, null);
  assert.equal(nodes.find(node => node.type === 'button' && elementText(node) === label).props['aria-pressed'], true);
  const after = copy(f.descriptors.get(f.ids[0]));
  assert.deepEqual(after, { ...before, state: { ...before.state, radials: before.state.radials.map(zone => ({ ...zone, on: value > 0, brightness: value / 100 })) } });
  f.dispose();
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

// These execute the actual async parent effect. Its engine/module boundaries
// are controlled adapters; real engine pause/import/frame behavior is covered
// separately by test-apartment-ui-overlay-bridge.mjs. No scheduler is created.
const settlePromises = async () => { for (let turn = 0; turn < 10; turn++) await Promise.resolve(); };

test('the actual async presentation effect creates one lease but publishes no plane before attached real drawing', async () => {
  const f = fixture(); f.mountPresentation();
  assert.equal(f.values.directionalViewRef.current, null); assert.equal(f.values.directionalProjectionRef.current, null);
  await settlePromises();
  const view = f.values.directionalViewRef.current; assert.ok(view); assert.equal(f.values.directionalView, view);
  assert.equal(f.state.layerRequests.length, 1); assert.equal(f.layers.size, 1);
  assert.equal(f.values.directionalProjectionRef.current, null);
  f.frame(); assert.equal(f.values.directionalProjectionRef.current, null, 'an unattached DOM cannot publish a plane');
  const elements = f.attach(view); f.frame();
  const drawn = f.values.directionalProjectionRef.current; assert.ok(drawn?.geometry);
  assert.equal(elements.root.hidden, false); assert.equal(elements.root.querySelector('[data-apt-radial-number="1"]').getAttribute('d'), drawn.geometry.zones[0].path);
  assert.deepEqual(f.state.patches, []); assert.equal(f.state.intervals.length, 0);
  f.dispose(); assert.equal(f.layers.size, 0); assert.equal(f.listeners.get('running').size, 0); assert.equal(f.domDocument.listeners.get('visibilitychange').length, 0);
});

test('actual pause retires the parent view/plane/capture synchronously and resume acquires a fresh lease', async () => {
  const f = fixture(); f.mountPresentation(); await settlePromises();
  const old = f.values.directionalViewRef.current, elements = f.attach(old); f.frame(); f.mountRadial(); paintNow(f);
  const applied = copy([...f.descriptors]); f.emitRunning(false);
  assert.equal(old.readback().disposed, true); assert.equal(f.values.directionalProjectionRef.current, null); assert.equal(f.values.directionalViewRef.current, null);
  assert.equal(f.values.radialGestureOwnerRef.current, null); assert.equal(f.host.captures.size, 0); assert.equal(elements.root.hidden, true);
  assert.deepEqual(copy([...f.descriptors]), applied); assert.equal(f.state.patches.length, 1);
  f.emitRunning(true); await settlePromises(); const next = f.values.directionalViewRef.current;
  assert.ok(next); assert.notEqual(next, old); assert.equal(f.state.layerRequests.length, 2); assert.equal(f.layers.size, 1);
  assert.equal(f.values.directionalProjectionRef.current, null); f.attach(next); f.frame(); assert.ok(f.values.directionalProjectionRef.current);
  f.dispose(); assert.equal(next.readback().disposed, true);
});

test('actual visibility retirement clears the plane and visible resume recreates without another timer', async () => {
  const f = fixture(); f.mountPresentation(); await settlePromises(); const old = f.values.directionalViewRef.current;
  f.attach(old); f.frame(); f.domDocument.visibilityState = 'hidden'; emit(f.domDocument, 'visibilitychange');
  assert.equal(old.readback().disposed, true); assert.equal(f.values.directionalViewRef.current, null); assert.equal(f.values.directionalProjectionRef.current, null);
  await settlePromises(); assert.equal(f.state.layerRequests.length, 1);
  f.domDocument.visibilityState = 'visible'; emit(f.domDocument, 'visibilitychange'); await settlePromises();
  assert.ok(f.values.directionalViewRef.current); assert.notEqual(f.values.directionalViewRef.current, old); assert.equal(f.state.layerRequests.length, 2);
  assert.equal(f.state.intervals.length, 0); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('a hidden initial parent refuses creation until its visibility producer admits the same owner', async () => {
  const f = fixture(); f.domDocument.visibilityState = 'hidden'; f.mountPresentation(); await settlePromises();
  assert.equal(f.state.layerRequests.length, 0); assert.equal(f.values.directionalViewRef.current, null);
  f.domDocument.visibilityState = 'visible'; emit(f.domDocument, 'visibilitychange'); await settlePromises();
  assert.equal(f.state.layerRequests.length, 1); assert.ok(f.values.directionalViewRef.current); f.dispose();
});

for (const [name, retire] of [
  ['effect cleanup', f => f.dispose()],
  ['workspace authority', f => { f.values.workspaceAuthorityRef.current.key = 'retired'; }],
  ['visibility', f => { f.domDocument.visibilityState = 'hidden'; emit(f.domDocument, 'visibilitychange'); }],
  ['selection', f => { f.values.virtualFixtureId = f.ids[1]; }],
]) test('a pending module namespace cannot create presentation after ' + name, async () => {
  const f = fixture(); let release;
  f.window.Home3D.ready = new Promise(resolve => { release = resolve; });
  f.mountPresentation(); retire(f); release({ loadDirectionalPresentation: async () => Presentation }); await settlePromises();
  assert.equal(f.state.layerRequests.length, 0); assert.equal(f.values.directionalViewRef.current, null); assert.equal(f.values.directionalProjectionRef.current, null);
  assert.deepEqual(f.state.patches, []); f.dispose();
});

test('cleanup during the real helper import refuses the later module before acquiring a layer', async () => {
  const f = fixture(); let release;
  f.window.Home3D.ready = Promise.resolve({ loadDirectionalPresentation: () => new Promise(resolve => { release = resolve; }) });
  f.mountPresentation(); await settlePromises(); assert.equal(typeof release, 'function'); f.dispose();
  release(Presentation); await settlePromises(); assert.equal(f.state.layerRequests.length, 0); assert.equal(f.values.directionalViewRef.current, null);
});

test('an already requested late layer is disposed exactly once after owner cleanup without publishing its view', async () => {
  const f = fixture(); let release, options, disposals = 0;
  f.engine.createUiOverlayLayer = value => { options = value; return new Promise(resolve => { release = resolve; }); };
  f.mountPresentation(); await settlePromises(); assert.equal(typeof release, 'function'); f.dispose(); assert.equal(options.signal.aborted, true);
  release({ group: new THREE.Group(), dispose() { disposals++; options.onPresentationDispose(); } }); await settlePromises();
  assert.equal(disposals, 1); assert.equal(f.values.directionalViewRef.current, null); assert.equal(f.values.directionalView, null); assert.equal(f.values.directionalProjectionRef.current, null);
  assert.deepEqual(f.state.patches, []);
});

for (const [name, change] of [
  ['null layer', f => { f.engine.createUiOverlayLayer = async () => null; }],
  ['rejected import', f => { f.window.Home3D.ready = Promise.resolve({ loadDirectionalPresentation: async () => { throw Error('Authored import refusal'); } }); }],
]) test('actual parent ' + name + ' leaves no view, drawing or dispatch', async () => {
  const f = fixture(); change(f); f.mountPresentation(); await settlePromises();
  assert.equal(f.values.directionalViewRef.current, null); assert.equal(f.values.directionalView, null); assert.equal(f.values.directionalProjectionRef.current, null);
  assert.equal(f.layers.size, 0); assert.deepEqual(f.state.patches, []); f.dispose();
});

test('the actual parent lease refuses a later authority mismatch before another drawing callback', async () => {
  const f = fixture(); f.mountPresentation(); await settlePromises(); const view = f.values.directionalViewRef.current, elements = f.attach(view); f.frame();
  f.values.workspaceAuthorityRef.current.key = 'retired'; f.frame();
  assert.equal(view.readback().disposed, true); assert.equal(elements.root.hidden, true); assert.equal(f.values.directionalProjectionRef.current, null);
  assert.equal(f.values.directionalViewRef.current, null); assert.equal(f.layers.size, 0); assert.deepEqual(f.state.patches, []); f.dispose();
});

for (const [name, change] of [
  ['generation', f => { f.descriptors.get(f.ids[0]).generation++; }],
  ['binding', f => { f.document.binding.manifest_digest = 'e'.repeat(64); f.descriptors.get(f.ids[0]).binding.manifest_digest = 'e'.repeat(64); }],
]) test('actual inspector uses the newly admitted channels instead of a same-ID old ' + name + ' view before effect cleanup', () => {
  const f = fixture(), old = f.values.directionalViewRef.current; assert.equal(old.getSnapshot().descriptor.state.radials[0].brightness, .5);
  change(f); f.descriptors.get(f.ids[0]).state.radials[0].brightness = .9;
  assert.ok(f.context.getDirectionalSnapshot(f.ids[0], { drawing: false }));
  f.context.directionalDisplay = actual.initializer('directionalDisplay', f.context);
  const view = actual.evaluate(actual.prop('AptUnifiedVirtualInspector', 'directionalView'), f.context);
  assert.equal(view, null, 'the actual inspector prop shares the full identity admission used by the portal');
  assert.equal(old.readback().disposed, false, 'this regression is checked before passive cleanup');
  const component = inspector(), props = { ...inspectorProps(f, []), directionalView: view };
  const range = elementNodes(component.render(props)).find(node => node.props?.ariaLabel === 'Radial brightness'); assert.ok(range);
  assert.ok(Math.abs(range.props.value - (.9 + .5 * 5) / 6 * 100) < 1e-8);
  assert.equal(old.getSnapshot().descriptor.state.radials[0].brightness, .5); assert.deepEqual(f.state.patches, []); f.dispose();
});
