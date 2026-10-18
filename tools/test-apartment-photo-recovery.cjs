// Actual host callbacks and renderer mode method, extracted without mounting
// Home or starting a renderer/service. Authored promises expose load races.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ROOT = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment.jsx'), 'utf8');
const babel = require(path.join(ROOT, 'app/src/vendor/babel-7.29.0/babel.min.js'));
const ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
const modes = [];
walk(ast, node => { if (node.type === 'VariableDeclarator' && node.id?.name === 'pickMode') modes.push(node); });
assert.equal(modes.length, 1, 'one existing mode-controller owner');
assert.equal(modes[0].init.callee.name, 'useCallback');
const callback = modes[0].init.arguments[0], callbackSource = source.slice(callback.start, callback.end);
const cancellations = [];
walk(ast, node => { if (node.type === 'VariableDeclarator' && node.id?.name === 'cancelSpatialTravel') cancellations.push(node); });
assert.equal(cancellations.length, 1, 'one existing spatial cancellation owner');
assert.equal(cancellations[0].init.callee.name, 'useCallback');
const cancelCallback = cancellations[0].init.arguments[0], cancelSource = source.slice(cancelCallback.start, cancelCallback.end);
const bindings = [], modeListeners = [];
walk(ast, node => {
  if (node.type === 'VariableDeclarator' && node.id?.name === 'bindInteriorEngine') bindings.push(node);
  if (node.type === 'CallExpression' && node.callee?.type === 'MemberExpression' && node.callee.property?.name === 'onChange'
    && node.callee.object?.type === 'MemberExpression' && node.callee.object.object?.name === 'engine'
    && node.callee.object.property?.name === 'modes') modeListeners.push(node);
});
assert.equal(bindings.length, 1, 'one installed engine binding owner');
assert.equal(modeListeners.length, 1, 'one existing renderer mode subscription');
const modeListener = modeListeners[0].arguments[0], modeListenerSource = source.slice(modeListener.start, modeListener.end);
const modePresentationNames = new Set(['modePresentationScope', 'modePresentationLifecycle', 'modePresentationIsCurrent']);
const modePresentationDeclarations = bindings[0].init.arguments[0].body.body
  .filter(node => node.type === 'VariableDeclaration' && node.declarations.some(declaration => modePresentationNames.has(declaration.id?.name)))
  .map(node => source.slice(node.start, node.end)).join('\n');
const rendererSource = fs.readFileSync(path.join(ROOT, 'app/src/home-3d/modes.js'), 'utf8');
const rendererAst = babel.transform(rendererSource, { ast: true, code: false }).ast.program, rendererSetModes = [];
walk(rendererAst, node => { if (node.type === 'ObjectMethod' && node.key?.name === 'setMode') rendererSetModes.push(node); });
assert.equal(rendererSetModes.length, 1, 'one actual renderer mode method');
const rendererSetModeSource = rendererSource.slice(rendererSetModes[0].start, rendererSetModes[0].end);
function fixture(options = {}) {
  const calls = [], state = { selected: 'victoria', mode: 'points', error: null, loading: null },
    engine = { siteId: 'victoria', contentRevision: 'authored-r1', sceneIdentity: { siteId: 'victoria', contentRevision: 'authored-r1' },
      rig: { inCameraPose: () => !!options.cameraPose }, scenePlaceholder: options.pointsOnly ? { pointsOnly: true } : null,
      modes: { mode: 'points', setMode: async mode => { calls.push(['engine', mode]); if (options.fail) throw Error('Authored Photo missing'); engine.modes.mode = mode; } } };
  const bindings = {
    engineRef: { current: engine }, selectedSiteInterior: options.interior !== false,
    liveCam: options.liveCam || null, calibCam: options.calibCam || null, preSnapModeRef: { current: 'authored-prior-mode' },
    viewOpenRef: { current: true }, selectedSiteId: 'victoria', selectedContentRevision: 'authored-r1',
    modeRequestRef: { current: null },
    spatialLifecycleRevisionRef: { current: 1 }, spatialTravelIntentRef: { current: 1 },
    worldOverviewAbortRef: { current: null }, travellingRef: { current: false },
    spatialCoordinatorRef: { current: null }, worldSurfaceRef: { current: null },
    setTravelling: value => calls.push(['travelling', value]),
    workspaceAuthorityRef: { current: { key: 'authored-authority' } }, apartmentAuthorityKey: 'authored-authority',
    window: { HomeSpatialViewport: { getSelectedSiteId: () => state.selected, getSite: () => ({ shortName: 'Authored home', contentRevision: 'authored-r1' }) } },
    setModeLoading: value => { calls.push(['loading', value]); state.loading = value; },
    setModeError: value => { calls.push(['error', value]); state.error = value; },
    setMode: value => { calls.push(['mode', value]); state.mode = value; },
    showToast: (...args) => calls.push(['toast', ...args]), refitMobileOverview: value => calls.push(['refit', value]),
  };
  const pick = vm.runInNewContext('(' + callbackSource + ')', bindings, { timeout: 1000 });
  const cancel = vm.runInNewContext('(' + cancelSource + ')', bindings, { timeout: 1000 });
  return { calls, state, engine, bindings, pick, cancel };
}
const ownerCalls = f => f.calls.filter(row => row[0] === 'engine');

function installedModeFixture({ legacy = false, manifestRegistration = 1 } = {}) {
  const f = fixture(), owner = { revision: legacy ? null : 'authored-r1', registration: 0, frame: legacy ? null : 'authored-frame' };
  f.engine.contentRevision = legacy ? 'legacy' : owner.revision;
  f.engine.sceneIdentity = { siteId: 'victoria', contentRevision: owner.revision,
    registrationVersion: legacy ? null : manifestRegistration ?? owner.registration, frameDigest: owner.frame };
  f.bindings.window.HomeSpatialViewport.getSite = () => ({ id: 'victoria', contentRevision: owner.revision,
    ...(legacy ? {} : { sceneRegistration: { version: owner.registration } }) });
  f.bindings.window.HomeContentPacks = { get: () => legacy ? undefined : { manifest: {
    revision: owner.revision, registrationVersion: manifestRegistration, assets: { 'frame.json': { sha256: owner.frame } },
  } } };
  const intent = f.bindings.spatialTravelIntentRef.current, lifecycle = f.bindings.spatialLifecycleRevisionRef.current;
  const listenerBindings = { ...f.bindings, engine: f.engine,
    current: () => f.bindings.viewOpenRef.current && f.bindings.engineRef.current === f.engine
      && f.state.selected === 'victoria' && f.bindings.spatialLifecycleRevisionRef.current === lifecycle
      && f.bindings.spatialTravelIntentRef.current === intent,
    persistView: () => f.calls.push(['persist']),
  };
  const listener = vm.runInNewContext('(() => {\n' + modePresentationDeclarations + '\nreturn (' + modeListenerSource + ');\n})()', listenerBindings, { timeout: 1000 });
  let resolvePhoto;
  const photo = new Promise(resolve => { resolvePhoto = resolve; });
  const rendererState = { mode: 'points', targetMode: 'points', modeSeq: 0, fading: null, splat: { opacity: 0 } };
  const actualSetMode = vm.runInNewContext('({' + rendererSetModeSource + '}).setMode', {
    state: rendererState, listeners: [listener], assertLive: () => {}, pointsOnlyScene: async () => false,
    loadSplat: () => photo, loadMesh: async () => {}, visibleFor: next => f.calls.push(['visible', next]),
    pointsMaterial: { uniforms: { uOpacity: { value: 1 } } },
  }, { timeout: 1000 });
  Object.defineProperty(f.engine.modes, 'mode', { get: () => rendererState.mode });
  f.engine.modes.setMode = actualSetMode;
  return { ...f, owner, resolvePhoto, rendererState, listener };
}

for (const [name, change] of [
  ['missing registry', f => { f.bindings.window.HomeSpatialViewport = undefined; }],
  ['missing selected-site getter', f => { delete f.bindings.window.HomeSpatialViewport.getSelectedSiteId; }],
  ['missing owner site', f => { f.bindings.window.HomeSpatialViewport.getSite = () => null; }],
  ['unreadable owner site', f => { f.bindings.window.HomeSpatialViewport.getSite = () => { throw Error('Authored registry unavailable'); }; }],
  ['unreadable selection', f => { f.bindings.window.HomeSpatialViewport.getSelectedSiteId = () => { throw Error('Authored selection unavailable'); }; }],
  ['different selected site', f => { f.state.selected = 'echo'; }],
  ['different registry revision', f => { f.bindings.window.HomeSpatialViewport.getSite = () => ({ contentRevision: 'authored-r2' }); }],
]) {
  test('unavailable owner context refuses before mutation: ' + name, async () => {
    const f = fixture(), error = f.state.error = { mode: 'mesh', message: 'Existing error' };
    f.state.loading = 'mesh'; const priorRequest = f.bindings.modeRequestRef.current = Promise.resolve();
    change(f); await f.pick('splat');
    assert.deepEqual(f.calls, []); assert.equal(f.state.error, error); assert.equal(f.state.loading, 'mesh');
    assert.equal(f.bindings.preSnapModeRef.current, 'authored-prior-mode'); assert.equal(f.bindings.modeRequestRef.current, priorRequest);
  });
}

test('an existing legacy site retains the existing absent-revision normalization', async () => {
  const f = fixture(); delete f.engine.contentRevision;
  f.bindings.window.HomeSpatialViewport.getSite = () => ({ shortName: 'Authored legacy home' });
  await f.pick('splat');
  assert.deepEqual(ownerCalls(f), [['engine', 'splat']]); assert.equal(f.state.mode, 'splat'); assert.equal(f.state.loading, null);
});

for (const fails of [true, false]) {
  test('context retired synchronously during mode admission still awaits ' + (fails ? 'rejection' : 'success'), async () => {
    const f = fixture(); let settle;
    f.engine.modes.setMode = () => {
      f.state.selected = 'echo';
      return new Promise((resolve, reject) => { settle = fails ? () => reject(Error('Authored admission retired')) : resolve; });
    };
    const pending = f.pick('splat'); settle(); await pending;
    assert.deepEqual(f.calls, []); assert.equal(f.bindings.modeRequestRef.current, null);
  });
}

test('explicit Photo retry uses the existing mode owner and reports success only after it resolves', async () => {
  const f = fixture(); await f.pick('splat');
  assert.deepEqual(ownerCalls(f), [['engine', 'splat']]); assert.equal(f.state.mode, 'splat');
  assert.equal(f.state.error, null); assert.equal(f.state.loading, null); assert.equal(f.bindings.preSnapModeRef.current, null);
  assert.deepEqual(f.calls.at(-1), ['refit', 520]);
});
test('failed Photo retry retains an explicit Photo error and does not claim a successful mode change', async () => {
  const f = fixture({ fail: true }); await f.pick('splat');
  assert.equal(f.state.mode, 'points'); assert.equal(f.state.error.mode, 'splat'); assert.equal(f.state.error.message, 'photo unavailable');
  assert.match(f.state.error.detail, /Authored Photo missing/); assert.equal(f.state.loading, null);
  assert.equal(f.calls.filter(row => row[0] === 'toast').length, 1);
});
test('explicit Back to Cloud uses the existing points mode without a retry or hidden fallback', async () => {
  const f = fixture(); f.state.error = { mode: 'splat', message: 'photo unavailable' }; await f.pick('points');
  assert.deepEqual(ownerCalls(f), [['engine', 'points']]); assert.equal(f.state.mode, 'points'); assert.equal(f.state.error, null);
  assert.deepEqual(f.calls.at(-1), ['refit', 360]);
});
for (const flags of [{ liveCam: { id: 'authored-camera' } }, { cameraPose: true }, { pointsOnly: true }, { interior: false }]) {
  test('Photo recovery preserves existing lock/unavailable refusal ' + JSON.stringify(flags), async () => {
    const f = fixture(flags); await f.pick('splat'); assert.deepEqual(ownerCalls(f), []);
    assert.equal(f.state.error, null); assert.equal(f.state.loading, null);
  });
}
test('a points-only placeholder still permits an explicit Back to Cloud', async () => {
  const f = fixture({ pointsOnly: true }); await f.pick('points'); assert.deepEqual(ownerCalls(f), [['engine', 'points']]);
});
for (const fails of [true, false]) {
  test('retired home ' + (fails ? 'failure' : 'success') + ' cannot publish mode/error/loading or refit into the new home', async () => {
    const f = fixture(); let resolve, reject;
    f.engine.modes.setMode = mode => { f.calls.push(['engine', mode]); return new Promise((yes, no) => { resolve = yes; reject = no; }); };
    const pending = f.pick('splat');
    f.bindings.engineRef.current = { siteId: 'echo', contentRevision: 'authored-echo-r1' }; f.state.selected = 'echo';
    f.bindings.spatialLifecycleRevisionRef.current++; f.bindings.spatialTravelIntentRef.current++;
    f.state.loading = 'mesh'; f.state.mode = 'mesh';
    const destinationError = f.state.error = { mode: 'mesh', message: 'Authored destination state' };
    f.calls.length = 0;
    if (fails) reject(Error('Authored old home load failed')); else resolve();
    await pending;
    assert.deepEqual(f.calls, [], 'completion from an old engine/site must have no current UI effects');
    assert.equal(f.state.loading, 'mesh'); assert.equal(f.state.mode, 'mesh'); assert.equal(f.state.error, destinationError);
  });
}
for (const fails of [true, false]) {
  test('Photo-to-Cloud-to-Photo old ' + (fails ? 'failure' : 'success') + ' cannot clear newest loading or Promise ownership', async () => {
    const f = fixture(), pending = [];
    f.engine.modes.setMode = mode => new Promise((resolve, reject) => pending.push({ mode, resolve, reject }));
    const olderPhoto = f.pick('splat'), supersededCloud = f.pick('points'), newestPhoto = f.pick('splat');
    assert.equal(pending.length, 3); assert.equal(f.state.loading, 'splat');
    const newestPromise = f.bindings.modeRequestRef.current;
    assert.ok(newestPromise && typeof newestPromise.then === 'function', 'the controller owns the exact in-flight Promise');
    f.calls.length = 0;
    if (fails) pending[0].reject(Error('Authored superseded Photo failure')); else pending[0].resolve();
    await olderPhoto;
    assert.deepEqual(f.calls, []); assert.equal(f.state.loading, 'splat'); assert.equal(f.bindings.modeRequestRef.current, newestPromise);
    pending[1].resolve(); await supersededCloud;
    assert.deepEqual(f.calls, []); assert.equal(f.state.loading, 'splat'); assert.equal(f.bindings.modeRequestRef.current, newestPromise);
    if (fails) pending[2].reject(Error('Authored current Photo failure')); else pending[2].resolve();
    await newestPhoto;
    assert.equal(f.state.loading, null); assert.equal(f.bindings.modeRequestRef.current, null);
    assert.equal(f.calls.filter(row => row[0] === 'refit').length, 1, 'only the current owner may refit');
    if (fails) { assert.equal(f.state.error.mode, 'splat'); assert.match(f.state.error.detail, /current Photo failure/); }
    else { assert.equal(f.state.mode, 'splat'); assert.equal(f.state.error, null); }
  });
}
for (const retirement of ['closed', 'revision']) {
  test('retired ' + retirement + ' cannot publish a delayed Photo failure', async () => {
    const f = fixture(); let reject;
    f.engine.modes.setMode = () => new Promise((_, no) => { reject = no; });
    const pending = f.pick('splat');
    if (retirement === 'closed') f.bindings.viewOpenRef.current = false;
    else f.engine.contentRevision = 'authored-r2';
    f.calls.length = 0; reject(Error('Authored retired Photo failure')); await pending;
    assert.deepEqual(f.calls, []);
  });
}
for (const fails of [true, false]) {
  test('A-to-B-to-A cached-engine ' + (fails ? 'failure' : 'success') + ' remains retired under the existing lifecycle identity', async () => {
    const f = fixture(); let resolve, reject;
    f.engine.modes.setMode = () => new Promise((yes, no) => { resolve = yes; reject = no; });
    const pending = f.pick('splat');
    // SiteSession.begin already increments these identities for every home
    // request. Returning to the original engine/site must not revive a request.
    f.bindings.engineRef.current = { siteId: 'echo' }; f.state.selected = 'echo';
    f.bindings.spatialLifecycleRevisionRef.current++; f.bindings.spatialTravelIntentRef.current++;
    f.bindings.engineRef.current = f.engine; f.state.selected = 'victoria';
    f.bindings.spatialLifecycleRevisionRef.current++; f.bindings.spatialTravelIntentRef.current++;
    f.state.loading = 'mesh'; const destinationError = f.state.error = { mode: 'mesh', message: 'Newer home request state' };
    f.calls.length = 0;
    if (fails) reject(Error('Authored retired load failure')); else resolve();
    await pending;
    assert.deepEqual(f.calls, []); assert.equal(f.state.loading, 'mesh'); assert.equal(f.state.error, destinationError);
  });
}

for (const fails of [true, false]) {
  test('same-home cancellation synchronously retires Photo loading before delayed ' + (fails ? 'failure' : 'success'), async () => {
    const f = fixture(); let resolveMode, rejectMode, resolveCancellation;
    f.engine.modes.setMode = () => new Promise((resolve, reject) => { resolveMode = resolve; rejectMode = reject; });
    f.bindings.spatialCoordinatorRef.current = {
      getState: () => ({ phase: 'idle' }),
      cancel: () => new Promise(resolve => { resolveCancellation = resolve; }),
    };
    const pending = f.pick('splat'), oldIntent = f.bindings.spatialTravelIntentRef.current;
    assert.equal(f.state.loading, 'splat'); assert.ok(f.bindings.modeRequestRef.current);
    const pendingCancellation = f.cancel();
    assert.equal(f.state.loading, null, 'a cancelled pack picker or rehearsal boundary must not strand the Photo spinner');
    assert.equal(f.bindings.modeRequestRef.current, null, 'retired mode ownership is released synchronously before coordinator cancellation settles');
    assert.equal(f.bindings.spatialTravelIntentRef.current, oldIntent + 1);
    assert.equal(f.state.selected, 'victoria'); assert.equal(f.bindings.engineRef.current, f.engine);
    f.calls.length = 0;
    if (fails) rejectMode(Error('Authored cancelled Photo failure')); else resolveMode();
    await pending;
    assert.deepEqual(f.calls, [], 'a retired completion cannot publish a mode/error/toast/loading update or refit');
    assert.equal(f.state.mode, 'points'); assert.equal(f.state.error, null); assert.equal(f.state.loading, null);
    assert.equal(f.bindings.modeRequestRef.current, null);
    resolveCancellation(); await pendingCancellation;
    assert.deepEqual(f.calls, [], 'late coordinator cancellation does not publish mode state');
  });
}

for (const fails of [true, false]) {
  test('same-home cancelled Photo ' + (fails ? 'failure' : 'success') + ' cannot erase a later mode request', async () => {
    const f = fixture(), requests = []; let resolveCancellation;
    f.engine.modes.setMode = mode => new Promise((resolve, reject) => requests.push({ mode, resolve, reject }));
    f.bindings.spatialCoordinatorRef.current = {
      getState: () => ({ phase: 'idle' }),
      cancel: () => new Promise(resolve => { resolveCancellation = resolve; }),
    };
    const retired = f.pick('splat');
    const pendingCancellation = f.cancel();
    assert.equal(f.state.loading, null, 'retirement happens before any later request starts');
    const current = f.pick('mesh'), currentPromise = f.bindings.modeRequestRef.current;
    assert.equal(f.state.loading, 'mesh'); assert.ok(currentPromise);
    f.calls.length = 0;
    if (fails) requests[0].reject(Error('Authored retired Photo failure')); else requests[0].resolve();
    await retired;
    assert.deepEqual(f.calls, []); assert.equal(f.state.loading, 'mesh');
    assert.equal(f.bindings.modeRequestRef.current, currentPromise, 'old finally cannot erase the new request');
    resolveCancellation(); await pendingCancellation;
    assert.deepEqual(f.calls, []); assert.equal(f.state.loading, 'mesh'); assert.equal(f.bindings.modeRequestRef.current, currentPromise);
    requests[1].resolve(); await current;
    assert.equal(f.state.mode, 'mesh'); assert.equal(f.state.error, null); assert.equal(f.state.loading, null);
    assert.equal(f.bindings.modeRequestRef.current, null); assert.deepEqual(f.calls.at(-1), ['refit', 520]);
  });
}

for (const [name, options] of [
  ['with manifest registration precedence', {}],
  ['with canonical registry registration fallback', { manifestRegistration: null }],
  ['for an existing legacy scene', { legacy: true }],
]) {
  test('same-home cancellation still presents the actual committed renderer mode ' + name, async () => {
    const f = installedModeFixture(options), pending = f.pick('splat');
    const cancellation = f.cancel();
    assert.equal(f.state.loading, null); await cancellation;
    f.calls.length = 0; f.resolvePhoto(); await pending;
    assert.equal(f.engine.modes.mode, 'splat', 'the unchanged renderer method committed Photo');
    assert.equal(f.state.mode, f.engine.modes.mode, 'recovered controls must present the actual installed engine mode');
    assert.deepEqual(f.calls.filter(row => row[0] !== 'visible'), [['mode', 'splat']], 'only mode presentation survives cancellation; persistence and retired request effects stay refused');
  });
}

for (const [name, retire] of [
  ['closed view', f => { f.bindings.viewOpenRef.current = false; }],
  ['retired engine', f => { f.bindings.engineRef.current = { siteId: 'victoria' }; }],
  ['wrong site', f => { f.state.selected = 'echo'; }],
  ['owner content revision', f => { f.owner.revision = 'authored-r2'; }],
  ['owner frame identity', f => { f.owner.frame = 'authored-frame-r2'; }],
  ['owner registration identity', f => { f.bindings.window.HomeContentPacks.get = () => ({ manifest: {
    revision: f.owner.revision, registrationVersion: 2, assets: { 'frame.json': { sha256: f.owner.frame } },
  } }); }],
  ['wrong active manifest revision', f => { f.bindings.window.HomeContentPacks.get = () => ({ manifest: {
    revision: 'other-revision', registrationVersion: 1, assets: { 'frame.json': { sha256: f.owner.frame } },
  } }); }],
  ['missing owner site', f => { f.bindings.window.HomeSpatialViewport.getSite = () => null; }],
  ['unreadable owner registry', f => { f.bindings.window.HomeSpatialViewport.getSite = () => { throw Error('Authored owner unavailable'); }; }],
  ['unavailable owner manifest API', f => { delete f.bindings.window.HomeContentPacks.get; }],
  ['installed frame identity', f => { f.engine.sceneIdentity = { ...f.engine.sceneIdentity, frameDigest: 'other-frame' }; }],
  ['old lifecycle', f => { f.bindings.spatialLifecycleRevisionRef.current++; }],
  ['A-to-B-to-A lifecycle', f => {
    f.state.selected = 'echo'; f.bindings.spatialLifecycleRevisionRef.current++;
    f.state.selected = 'victoria'; f.bindings.spatialLifecycleRevisionRef.current++;
  }],
]) {
  test('installed mode event refuses ' + name + ' after cancellation', async () => {
    const f = installedModeFixture(), pending = f.pick('splat'); await f.cancel(); retire(f);
    f.calls.length = 0; f.resolvePhoto(); await pending;
    assert.equal(f.engine.modes.mode, 'splat', 'the old renderer may finish without granting UI publication');
    assert.equal(f.state.mode, 'points'); assert.equal(f.state.error, null); assert.equal(f.state.loading, null);
    assert.deepEqual(f.calls.filter(row => row[0] !== 'visible'), [], 'no retired mode/persistence/error/loading/refit publication');
  });
}
