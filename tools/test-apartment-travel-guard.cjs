/* Actual Apartment data boundary, authored HA state and dispatch spies only. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const candidateRoot = process.env.HOME_APARTMENT_CANDIDATE_ROOT || path.join(__dirname, '..');
const source = readFileSync(path.join(candidateRoot, 'app/src/home-apartment-data.js'), 'utf8');
const aimSource = readFileSync(path.join(candidateRoot, 'app/src/home-apartment-aim.jsx'), 'utf8');
const hostSource = readFileSync(path.join(candidateRoot, 'app/src/home-apartment.jsx'), 'utf8').replace(/\r\n/g, '\n');
const siteSession = require('../app/src/home-apartment-site-session.js');
const ENTITY = 'input_boolean.living_lights_travel_mode';
const CACHE = 'home.lights.travelMode.state.v1';
const copy = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function fixture({ helper = 'off', cached = null, native = false, read, timers } = {}) {
  const listeners = new Map(), storage = new Map(cached ? [[CACHE, cached]] : []);
  const reads = [], dispatches = [], subscriptions = [];
  let selectedSite = 'echo', current = true;
  const window = {
    location: { protocol: 'https:', host: 'home.invalid' },
    HomeSpatialViewport: { getSelectedSiteId: () => selectedSite },
    addEventListener(type, fn) { const set = listeners.get(type) || new Set(); set.add(fn); listeners.set(type, set); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    dispatchEvent(event) { for (const fn of listeners.get(event.type) || []) fn(event); },
  };
  const client = {
    async call(payload) {
      if (payload.type === 'get_states') {
        reads.push(copy(payload));
        return read ? read() : helper === null ? [] : [{ entity_id: ENTITY, state: helper, attributes: {} }];
      }
      dispatches.push(copy(payload)); return { accepted: true };
    },
    async subscribeEvents(type, callback) {
      const subscription = { type, callback, closed: false }; subscriptions.push(subscription);
      return () => { subscription.closed = true; };
    },
  };
  if (!native) client.callService = async (domain, service, data) => { dispatches.push(copy({ domain, service, data })); return { accepted: true }; };
  window.__hav_haClient = client;
  const localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  window.localStorage = localStorage;
  class CustomEvent { constructor(type, options) { this.type = type; this.detail = options.detail; } }
  vm.runInNewContext(source, { window, localStorage, console, CustomEvent, setTimeout: timers?.setTimeout || setTimeout,
    clearTimeout: timers?.clearTimeout || clearTimeout, setInterval, clearInterval }, { filename: 'home-apartment-data.js' });
  const options = () => ({ siteId: 'echo', isCurrent: () => current });
  return {
    D: window.HomeApartmentData, window, client, reads, dispatches, subscriptions, storage, listeners, options,
    setCurrent: value => { current = value; }, setSite: value => { selectedSite = value; },
    publish(state) {
      window.__HOME_TRAVEL_MODE_STATE = state;
      if (state === 'on') storage.set(CACHE, 'on'); else storage.delete(CACHE);
      window.dispatchEvent({ type: 'home-travel-mode-change', detail: { state } });
    },
    emitHelper(state) {
      for (const subscription of subscriptions) subscription.callback({ data: { entity_id: ENTITY,
        new_state: state === null ? null : { entity_id: ENTITY, state } } });
    },
  };
}

function aimSender(f) {
  const start = aimSource.indexOf('  const sendLights = async '), end = aimSource.indexOf('\n  const restoreZone =', start);
  assert.ok(start >= 0 && end > start, 'exercise the actual Aim sender body');
  const engine = {}, model = {}, context = { engine, model, fixtureId: 'fixture-a', connection: 'online', sim: false,
    isControlCurrent: f.options().isCurrent };
  const state = { roleStatus: {}, notice: null, readbacks: 0 };
  const sandbox = {
    window: f.window, sim: false, connection: 'online',
    controlContextRef: { current: context }, controlMountedRef: { current: true },
    setRoleStatus(update) { state.roleStatus = update(state.roleStatus); },
    setNotice(value) { state.notice = value; },
    readback: async () => { state.readbacks++; return {}; },
    setTimeout: fn => { fn(); return 1; },
  };
  vm.runInNewContext(aimSource.slice(start, end) + '\nthis.sendLights = sendLights;', sandbox, { filename: 'home-apartment-aim.sendLights.js' });
  return { send: sandbox.sendLights, state, context, contextRef: sandbox.controlContextRef, mounted: sandbox.controlMountedRef };
}

function hostHook(name, kind, environment) {
  const marker = `const ${name} = ${kind}(`;
  const start = hostSource.indexOf(marker) + marker.length, end = hostSource.indexOf('\n  }, [', start) + 4;
  assert.ok(start >= marker.length && end > start, 'exercise actual host ' + name);
  const value = new Function(...Object.keys(environment), `return (${hostSource.slice(start, end)});`)(...Object.values(environment));
  return kind === 'useMemo' ? value() : value;
}

function hostFunction(name) {
  const start = hostSource.indexOf('function ' + name + '('), end = hostSource.indexOf('\n}\n', start) + 2;
  assert.ok(start >= 0 && end > start, name);
  return new Function(`return (${hostSource.slice(start, end)});`)();
}

function hostSender(f, { session = null } = {}) {
  const authority = f.D.ensureWorkspaceAuthority({ endpoint: 'https://echo.invalid', token: 'authored-private-token', simulation: false }).key;
  const changes = [], notices = [];
  const engine = { siteId: 'echo', contentRevision: 'echo-r1', overlay: { setDeviceState: (...args) => changes.push(copy(args)) } };
  f.window.HomeApartmentSiteSession = siteSession;
  f.window.HomeSpatialViewport.getSite = () => ({ contentRevision: 'echo-r1' });
  const environment = {
    window: f.window, console,
    controlConnectionRef: { current: { connection: 'online', generation: 0 } },
    engineRef: { current: engine }, workspaceAuthorityRef: { current: { key: authority } },
    spatialTravelIntentRef: { current: 0 }, viewOpenRef: { current: true },
    modelScopeRef: { current: { authorityKey: authority } }, modelSiteRef: { current: 'echo' },
    interiorSessionRef: { current: session }, simActive: false, rehearsalIsolated: false,
    statesRef: { current: { 'light.test': { entity_id: 'light.test', state: 'off', attributes: { brightness: 10 } } } },
    model: { devices: [{ id: 'fixture-a', name: 'Fixture A', ha_entity_id: 'light.test' }] },
    aptOptimisticServiceState: hostFunction('aptOptimisticServiceState'),
    aptExpectedServiceState: hostFunction('aptExpectedServiceState'),
    aptStateMatchesExpected: hostFunction('aptStateMatchesExpected'), aptDelay: async () => {},
    setServicePulse() {}, showToast: message => notices.push(message),
  };
  environment.isControlCurrent = hostHook('isControlCurrent', 'useMemo', environment);
  return { current: environment.isControlCurrent, send: hostHook('callSvc', 'useCallback', environment), environment, changes, notices, authority };
}

test('all Apartment lighting output families require an actual off helper before either transport', async () => {
  for (const native of [false, true]) for (const [domain, service] of [
    ['light', 'turn_on'], ['light', 'toggle'], ['switch', 'turn_on'], ['switch', 'toggle'],
    ['scene', 'turn_on'], ['scene', 'apply'], ['homeassistant', 'turn_on'], ['homeassistant', 'toggle'],
    ['script', 'turn_on'], ['script', 'evening_lights'],
  ]) {
    const f = fixture({ helper: 'on', native });
    await assert.rejects(f.D.callService(f.client, domain, service, { entity_id: domain + '.test' }, f.options()), /travel mode/i);
    assert.equal(f.reads.length, 1, domain + '.' + service + ' reads actual HA state');
    assert.equal(f.dispatches.length, 0, domain + '.' + service + ' must not dispatch');
  }
});

test('confirmed off dispatches once and cached on cannot substitute for live confirmation', async () => {
  for (const native of [false, true]) {
    const f = fixture({ helper: 'off', cached: 'on', native });
    await f.D.callService(f.client, 'light', 'turn_on', { entity_id: 'light.test', brightness: 100 }, f.options());
    assert.equal(f.reads.length, 1); assert.equal(f.dispatches.length, 1);
    const call = f.dispatches[0];
    assert.equal(call.domain, 'light'); assert.equal(call.service, 'turn_on');
    assert.equal(native ? call.target.entity_id : call.data.entity_id, 'light.test');
  }
});

test('missing, unavailable, unknown, malformed and failed helper reads refuse without output', async () => {
  for (const helper of [null, 'unknown', 'unavailable', '', 'ON']) {
    const f = fixture({ helper });
    await assert.rejects(f.D.callService(f.client, 'light', 'turn_on', {}, f.options()), /travel mode/i);
    assert.equal(f.dispatches.length, 0);
  }
  for (const read of [() => Promise.reject(Error('disconnected')), () => ({}), () => [
    { entity_id: ENTITY, state: 'off' }, { entity_id: ENTITY, state: 'on' },
  ]]) {
    const f = fixture({ read });
    await assert.rejects(f.D.callService(f.client, 'light', 'turn_on', {}, f.options()), /travel mode/i);
    assert.equal(f.dispatches.length, 0);
  }
});

test('off operations and unrelated media controls remain available without a Travel helper', async () => {
  for (const [domain, service] of [['light', 'turn_off'], ['switch', 'turn_off'], ['homeassistant', 'turn_off'],
    ['media_player', 'turn_on'], ['media_player', 'media_play'], ['media_player', 'volume_set']]) {
    const f = fixture({ helper: 'on' });
    await f.D.callService(f.client, domain, service, { entity_id: domain + '.test' });
    assert.equal(f.reads.length, 0); assert.equal(f.dispatches.length, 1);
  }
});

test('lighting output needs a current caller context and an actual get_states reader', async () => {
  const f = fixture();
  await assert.rejects(f.D.callService(f.client, 'light', 'turn_on'), /context/i);
  assert.equal(f.reads.length, 0); assert.equal(f.dispatches.length, 0);
  const noRead = { callService: f.client.callService };
  await assert.rejects(f.D.callService(noRead, 'light', 'turn_on', {}, f.options()), /travel mode|context/i);
  assert.equal(f.dispatches.length, 0);
});

test('site, client, authority and generation changes during the helper read cannot dispatch', async () => {
  for (const change of [f => f.setCurrent(false), f => f.setSite('victoria'), f => { f.window.__hav_haClient = {}; },
    f => f.D.setWorkspaceAuthority('new-authority')]) {
    const gate = deferred(), f = fixture({ read: () => gate.promise });
    f.D.setWorkspaceAuthority('initial-authority');
    const operation = f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
    change(f); gate.resolve([{ entity_id: ENTITY, state: 'off' }]);
    await assert.rejects(operation, /context|home|authority/i);
    assert.equal(f.dispatches.length, 0);
  }
});

test('on received while a helper read is pending stays blocked even if a later off event arrives', async () => {
  const gate = deferred(), f = fixture({ read: () => gate.promise });
  const operation = f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
  f.publish('on'); f.publish('off'); gate.resolve([{ entity_id: ENTITY, state: 'off' }]);
  await assert.rejects(operation, /travel mode/i);
  assert.equal(f.dispatches.length, 0);
  assert.equal(f.listeners.get('home-travel-mode-change')?.size || 0, 0, 'pending listeners are released');
});

test('cross-tab on during a pending read blocks and cannot be renewed by the returned old off', async () => {
  const gate = deferred(), f = fixture({ read: () => gate.promise });
  const operation = f.D.callService(f.client, 'switch', 'toggle', {}, f.options());
  f.window.dispatchEvent({ type: 'storage', key: CACHE, newValue: 'on' });
  gate.resolve([{ entity_id: ENTITY, state: 'off' }]);
  await assert.rejects(operation, /travel mode/i); assert.equal(f.dispatches.length, 0);
  assert.equal(f.listeners.get('storage')?.size || 0, 0);
});

test('the pending-on latch remains installed until the final transport boundary', async () => {
  const f = fixture();
  let checks = 0;
  const options = { siteId: 'echo', isCurrent() {
    checks++;
    if (checks === 5) { f.publish('on'); f.publish('off'); }
    return true;
  } };
  await assert.rejects(f.D.callService(f.client, 'light', 'turn_on', {}, options), /travel mode/i);
  assert.equal(f.dispatches.length, 0);
});

test('a pending helper read cannot replace the submitted target or brightness', async () => {
  const gate = deferred(), f = fixture({ read: () => gate.promise });
  const data = { entity_id: ['light.original'], brightness: 10 };
  const operation = f.D.callService(f.client, 'light', 'turn_on', data, f.options());
  data.entity_id[0] = 'light.changed'; data.brightness = 255;
  gate.resolve([{ entity_id: ENTITY, state: 'off' }]);
  await operation;
  assert.deepEqual(f.dispatches[0].data, { entity_id: ['light.original'], brightness: 10 });
});

test('a timed-out read releases listeners and late off cannot dispatch', async () => {
  const gate = deferred(); let timeout, cleared = false;
  const f = fixture({ read: () => gate.promise, timers: {
    setTimeout(fn, ms) { assert.equal(ms, 4000); timeout = fn; return 1; },
    clearTimeout() { cleared = true; },
  } });
  const operation = f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
  timeout();
  await assert.rejects(operation, /travel mode/i);
  gate.resolve([{ entity_id: ENTITY, state: 'off' }]); await Promise.resolve();
  assert.equal(f.dispatches.length, 0); assert.equal(cleared, true);
  assert.equal(f.listeners.get('home-travel-mode-change')?.size || 0, 0);
  assert.equal(f.listeners.get('storage')?.size || 0, 0);
});

test('reconnect requires a fresh off read and never replays a refused command', async () => {
  const f = fixture({ helper: 'off' });
  f.setCurrent(false);
  await assert.rejects(f.D.callService(f.client, 'light', 'turn_on', {}, f.options()), /context/i);
  assert.equal(f.dispatches.length, 0);
  f.setCurrent(true);
  await f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
  await f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
  assert.equal(f.reads.length, 2); assert.equal(f.dispatches.length, 2);
});

test('incumbent Victoria and rehearsal refusal applies before any state or service call', async () => {
  const f = fixture();
  await assert.rejects(f.D.callService(f.client, 'light', 'turn_on', {}, { ...f.options(), siteId: 'victoria' }), /home/i);
  f.window.HomeMapRehearsalOfflineState = { active: true };
  await assert.rejects(f.D.callService(f.client, 'light', 'turn_on', {}, f.options()), /rehearsal/i);
  assert.equal(f.reads.length, 0); assert.equal(f.dispatches.length, 0);
});

test('actual Aim sender uses the final guard for radial, spotlight, identify and copy-all output', async () => {
  for (const statusKey of ['zone-1', 'spotlight', 'identify', 'copy-all']) {
    const f = fixture({ helper: 'on' }), aim = aimSender(f);
    assert.equal(await aim.send(['light.test'], 'turn_on', { brightness: 100 }, statusKey), null);
    assert.equal(f.reads.length, 1); assert.equal(f.dispatches.length, 0);
    assert.equal(aim.state.roleStatus[statusKey], 'failed');
    assert.match(aim.state.notice, /travel mode/i); assert.equal(aim.state.readbacks, 0);
  }
});

test('actual Aim sender preserves explicit off while avoiding stale accepted/readback state', async () => {
  const f = fixture({ helper: 'on' }), aim = aimSender(f);
  await aim.send(['light.test'], 'turn_off', {}, 'spotlight');
  assert.equal(f.reads.length, 0); assert.equal(f.dispatches.length, 1);
  assert.equal(aim.state.roleStatus.spotlight, 'verified');
  const pending = deferred(), staleFixture = fixture({ read: () => pending.promise }), staleAim = aimSender(staleFixture);
  const operation = staleAim.send(['light.test'], 'turn_on', {}, 'spotlight');
  staleAim.contextRef.current = { ...staleAim.context, fixtureId: 'fixture-b' };
  pending.resolve([{ entity_id: ENTITY, state: 'off' }]);
  assert.equal(await operation, null);
  assert.equal(staleFixture.dispatches.length, 0); assert.equal(staleAim.state.readbacks, 0);
});

test('superseded Aim refusals do not write failed status or notices into a newer fixture context', async () => {
  for (const mutate of [(aim) => { aim.contextRef.current = { ...aim.context, fixtureId: 'fixture-b' }; },
    (aim) => { aim.contextRef.current = { ...aim.context, model: {} }; },
    (_aim, f) => { f.window.__hav_haClient = {}; },
    (aim) => { aim.contextRef.current = { ...aim.context, connection: 'reconnecting' }; }]) {
    const pending = deferred(), f = fixture({ read: () => pending.promise }), aim = aimSender(f);
    const operation = aim.send(['light.test'], 'turn_on', {}, 'spotlight');
    mutate(aim, f);
    aim.state.roleStatus = { other: 'idle' }; aim.state.notice = 'Current fixture notice';
    pending.resolve([{ entity_id: ENTITY, state: 'off' }]);
    assert.equal(await operation, null);
    assert.equal(f.dispatches.length, 0); assert.equal(aim.state.readbacks, 0);
    assert.deepEqual(aim.state.roleStatus, { other: 'idle' });
    assert.equal(aim.state.notice, 'Current fixture notice');
  }
});

test('actual host accepts legacy ready context without an interior-session object', async () => {
  const f = fixture(), host = hostSender(f);
  assert.equal(host.authority, 'apartment-authority-1', 'real initial authority is established before the host gate');
  assert.equal(host.current(), true);
  await host.send('light', 'turn_on', { entity_id: 'light.test' });
  assert.equal(f.reads[0].type, 'get_states'); assert.equal(f.dispatches.length, 1);
  assert.equal(host.changes.length, 1, 'optimistic display occurs after accepted dispatch');
});

test('actual host Travel refusal preserves a newer subscribed device state without optimistic rollback', async () => {
  const pending = deferred(), f = fixture({ read: () => pending.promise }), host = hostSender(f);
  const operation = host.send('light', 'turn_on', { entity_id: 'light.test' });
  const subscribed = { entity_id: 'light.test', state: 'on', attributes: { brightness: 222 }, last_updated: 'new-observation' };
  host.environment.statesRef.current['light.test'] = subscribed;
  pending.resolve([{ entity_id: ENTITY, state: 'on' }]);
  await operation;
  assert.equal(f.dispatches.length, 0);
  assert.equal(host.environment.statesRef.current['light.test'], subscribed);
  assert.equal(host.changes.length, 0, 'no optimistic or rollback write occurs for a refused command');
});

test('actual host rejects pending on and immediate off when its current connection or authority changes', async () => {
  for (const mutate of [host => { host.environment.controlConnectionRef.current = { connection: 'online', generation: 1 }; },
    host => { host.environment.workspaceAuthorityRef.current.key = 'new-authority'; },
    host => { host.environment.viewOpenRef.current = false; }]) {
    const pending = deferred(), f = fixture({ read: () => pending.promise }), host = hostSender(f);
    const operation = host.send('light', 'turn_on', { entity_id: 'light.test' });
    mutate(host); pending.resolve([{ entity_id: ENTITY, state: 'off' }]); await operation;
    assert.equal(host.current(), false); assert.equal(f.dispatches.length, 0); assert.equal(host.changes.length, 0);
    await host.send('light', 'turn_off', { entity_id: 'light.test' });
    assert.equal(f.dispatches.length, 0, 'explicit off still requires a current host scope');
  }
});

test('actual host renews future command context after a canceled or unchanged spatial intent', () => {
  const f = fixture(), host = hostSender(f);
  const start = hostSource.indexOf('const isControlCurrent = ') + 'const isControlCurrent = '.length;
  const end = hostSource.indexOf('\n\n  const callSvc =', start);
  assert.ok(start > 0 && end > start);
  let cached;
  const useMemo = (factory, dependencies) => {
    if (!cached || dependencies.some((value, index) => value !== cached.dependencies[index])) cached = { value: factory(), dependencies };
    return cached.value;
  };
  const environment = { ...host.environment, useMemo, connection: 'online', endpoint: 'https://echo.invalid', token: 'authored-private-token',
    selectedSiteId: 'echo', selectedContentRevision: 'echo-r1', phase: 'ready', serviceEpoch: 0, apartmentAuthorityKey: host.authority };
  const render = new Function(...Object.keys(environment), `return ${hostSource.slice(start, end)}`);
  const oldContext = render(...Object.values(environment));
  assert.equal(oldContext(), true);
  host.environment.spatialTravelIntentRef.current++;
  const nextContext = render(...Object.values(environment));
  assert.equal(oldContext(), false, 'an already pending command remains superseded');
  assert.notEqual(nextContext, oldContext, 'a later render must renew future command context even when the home revision did not change');
  assert.equal(nextContext(), true);
});

test('actual bound-state owner publishes Travel helper observations even with zero mapped devices', async () => {
  const f = fixture({ helper: 'off', cached: 'on' }), states = [];
  const cleanup = f.D.bindStates(f.client, { devices: [] }, (id, state) => states.push({ id, state }), f.options());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.subscriptions.length, 1); assert.equal(f.subscriptions[0].type, 'state_changed');
  assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, 'off'); assert.equal(f.storage.has(CACHE), false);
  assert.equal(states[0].id, ENTITY);
  f.emitHelper('on');
  assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, 'on'); assert.equal(f.storage.get(CACHE), 'on');
  f.emitHelper('off');
  assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, 'off'); assert.equal(f.storage.has(CACHE), false, 'off is never cached');
  f.emitHelper('unknown');
  assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, null);
  f.emitHelper('on'); f.emitHelper(null);
  assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, null, 'a deleted helper withdraws global off/on authority');
  assert.equal(f.storage.get(CACHE), 'on', 'deletion retains a fail-safe cached on');
  cleanup(); assert.equal(f.subscriptions[0].closed, true);
});

test('actual bound helper on from another HA client blocks an already pending lighting check', async () => {
  const pending = deferred(); let readCount = 0;
  const f = fixture({ read: () => ++readCount === 1 ? [{ entity_id: ENTITY, state: 'off' }] : pending.promise });
  const cleanup = f.D.bindStates(f.client, { devices: [] }, () => {}, f.options());
  await new Promise(resolve => setImmediate(resolve));
  const operation = f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
  f.emitHelper('on'); f.emitHelper('off');
  pending.resolve([{ entity_id: ENTITY, state: 'off' }]);
  await assert.rejects(operation, /travel mode/i); assert.equal(f.dispatches.length, 0);
  cleanup();
});

test('helper withdrawal while a read is pending cannot be replaced by its returned old off', async () => {
  for (const state of ['unknown', 'unavailable', null]) {
    const pending = deferred(); let readCount = 0;
    const f = fixture({ read: () => ++readCount === 1 ? [{ entity_id: ENTITY, state: 'off' }] : pending.promise });
    const cleanup = f.D.bindStates(f.client, { devices: [] }, () => {}, f.options());
    await new Promise(resolve => setImmediate(resolve));
    const operation = f.D.callService(f.client, 'light', 'turn_on', {}, f.options());
    f.emitHelper(state); pending.resolve([{ entity_id: ENTITY, state: 'off' }]);
    await assert.rejects(operation, /travel mode is unavailable/i); assert.equal(f.dispatches.length, 0);
    cleanup();
  }
});

test('stale, foreign-site, replaced-client and disposed helper subscriptions cannot publish', async () => {
  for (const mutate of [f => f.setCurrent(false), f => f.setSite('victoria'), f => { f.window.__hav_haClient = {}; },
    f => f.D.setWorkspaceAuthority('replacement-authority'), (_f, cleanup) => cleanup()]) {
    const f = fixture(), cleanup = f.D.bindStates(f.client, { devices: [] }, () => {}, f.options());
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, 'off');
    mutate(f, cleanup); f.emitHelper('on');
    assert.equal(f.window.__HOME_TRAVEL_MODE_STATE, 'off'); assert.equal(f.storage.has(CACHE), false);
    cleanup();
  }
});

test('missing snapshot helper withdraws old off and late foreign-site snapshots cannot publish', async () => {
  const missing = fixture({ helper: null }); missing.window.__HOME_TRAVEL_MODE_STATE = 'off';
  const cleanup = missing.D.bindStates(missing.client, { devices: [] }, () => {}, missing.options());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(missing.window.__HOME_TRAVEL_MODE_STATE, null); cleanup();
  const pending = deferred(), stale = fixture({ read: () => pending.promise }); stale.window.__HOME_TRAVEL_MODE_STATE = 'on';
  stale.D.bindStates(stale.client, { devices: [] }, () => assert.fail('foreign-site state'), stale.options());
  stale.setSite('victoria'); pending.resolve([{ entity_id: ENTITY, state: 'off' }]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(stale.window.__HOME_TRAVEL_MODE_STATE, 'on'); assert.equal(stale.subscriptions.length, 0);
});
