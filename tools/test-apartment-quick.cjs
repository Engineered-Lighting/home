// APT-R03/L04: exact Quick Undo and Echo HA presentation against an authored,
// in-memory Home Assistant adapter. No client, socket, network or real dispatch.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment-quick.js'), 'utf8');
const Q = require(path.join(ROOT, 'app/src/home-apartment-quick.js'));

const light = (state, attributes = {}) => ({ state, attributes, last_reported: '2026-10-19T19:58:00.000Z' });
const ct = (brightness, kelvin) => light('on', { brightness, color_mode: 'color_temp', color_temp_kelvin: kelvin,
  supported_color_modes: ['color_temp'], min_color_temp_kelvin: 2200, max_color_temp_kelvin: 6500 });
const hs = (brightness, hue) => light('on', { brightness, color_mode: 'hs', hs_color: hue, supported_color_modes: ['hs', 'color_temp'] });

const DEVICES = [
  { id: 'front-left', type: 'light', ha_entity_id: 'light.front_left' },
  { id: 'front-right', type: 'light', ha_entity_id: 'light.front_right' },
  { id: 'desk-lamp', type: 'plug', ha_entity_id: 'switch.desk_lamp' },
  { id: 'reading', type: 'light', ha_entity_id: 'light.reading' },
  { id: 'speaker', type: 'speaker', ha_entity_id: 'media_player.living' },
  { id: 'fan', type: 'fan', ha_entity_id: 'switch.fan' },
];

function initialStates() {
  return {
    'light.front_left': ct(178, 2700),
    'light.front_right': light('off', { supported_color_modes: ['color_temp'] }),
    'switch.desk_lamp': light('on'),
    'light.reading': hs(90, [32.5, 71]),
    'switch.fan': light('off'),
    'scene.evening': light('2026-10-19T18:00:00+00:00', { friendly_name: 'Evening',
      entity_id: ['light.front_left', 'light.front_right', 'switch.desk_lamp'] }),
    'scene.movie': light('unknown', { friendly_name: 'Movie', entity_id: ['light.front_left', 'media_player.living'] }),
    'script.wind_down': light('off', { friendly_name: 'Wind down' }),
    'automation.porch': light('on', { friendly_name: 'Porch' }),
  };
}

// Authored adapter: HA light/switch/scene/script semantics on an in-memory map.
// It records every call; nothing leaves the process.
function authoredHa(states, { refuse = () => false } = {}) {
  const calls = [];
  const sceneTargets = {
    'scene.evening': { 'light.front_left': ct(64, 2200), 'light.front_right': ct(128, 2400), 'switch.desk_lamp': light('off') },
    'scene.movie': { 'light.front_left': light('off') },
  };
  const set = (id, next) => { states[id] = { ...next, last_reported: '2026-10-19T20:00:00.000Z' }; };
  const dispatch = async (domain, service, data) => {
    calls.push({ domain, service, data: JSON.parse(JSON.stringify(data)) });
    if (refuse(domain, service, data)) return false;
    const id = data.entity_id, previous = states[id];
    if (domain === 'switch') set(id, light(service === 'turn_on' ? 'on' : 'off'));
    else if (domain === 'light' && service === 'turn_off') set(id, { ...previous, state: 'off', attributes: { supported_color_modes: previous.attributes.supported_color_modes } });
    else if (domain === 'light' && service === 'turn_on') {
      const attributes = { ...previous.attributes };
      if (data.brightness != null) attributes.brightness = data.brightness;
      if (data.color_temp_kelvin != null) Object.assign(attributes, { color_mode: 'color_temp', color_temp_kelvin: data.color_temp_kelvin });
      if (data.hs_color != null) { delete attributes.color_temp_kelvin; Object.assign(attributes, { color_mode: 'hs', hs_color: data.hs_color }); }
      if (attributes.brightness == null) attributes.brightness = 255;
      if (!attributes.color_mode) Object.assign(attributes, { color_mode: 'color_temp', color_temp_kelvin: 4000 });
      set(id, { ...previous, state: 'on', attributes });
    } else if (domain === 'scene') for (const [target, next] of Object.entries(sceneTargets[id] || {})) set(target, next);
    else if (domain === 'script') for (const target of ['light.front_left', 'light.reading']) set(target, light('off'));
    else throw Error('authored adapter has no ' + domain + '.' + service);
    return true;
  };
  return { calls, dispatch };
}

const exactView = (states, ids) => Object.fromEntries(ids.map(id => [id, Q.snapshot(states, [id])[0]]));

test('the Quick module has no dispatch, client or network path of its own', () => {
  assert.doesNotMatch(SOURCE, /__hav_haClient|callService|fetch\(|WebSocket|XMLHttpRequest|readStates/);
  assert.equal(typeof Q.applyQuick, 'function');
});

test('snapshots and restore calls are exact for each colour mode and refuse to guess', () => {
  const states = initialStates();
  const before = Q.snapshot(states, ['light.front_left', 'light.front_right', 'switch.desk_lamp', 'light.reading', 'light.missing', 'scene.evening']);
  assert.ok(Object.isFrozen(before) && Object.isFrozen(before[0]));
  assert.deepEqual(before.map(entry => entry.entity_id), ['light.front_left', 'light.front_right', 'switch.desk_lamp', 'light.reading', 'light.missing']);
  const { calls, skipped, exact } = Q.restoreCalls(before);
  assert.deepEqual(calls, [
    { domain: 'light', service: 'turn_on', data: { entity_id: 'light.front_left', brightness: 178, color_temp_kelvin: 2700 } },
    { domain: 'light', service: 'turn_off', data: { entity_id: 'light.front_right' } },
    { domain: 'switch', service: 'turn_on', data: { entity_id: 'switch.desk_lamp' } },
    { domain: 'light', service: 'turn_on', data: { entity_id: 'light.reading', brightness: 90, hs_color: [32.5, 71] } },
  ]);
  assert.deepEqual(skipped, ['light.missing']); assert.equal(exact, false);
});

test('All off then Undo restores every Echo light and plug exactly through the authored adapter', async () => {
  const states = initialStates(), ha = authoredHa(states), ids = Q.echoLightEntityIds(DEVICES);
  assert.deepEqual(ids, ['light.front_left', 'light.front_right', 'switch.desk_lamp', 'light.reading']);
  const original = exactView(states, ids);
  const plan = Q.planQuick({ kind: 'all', on: false }, { devices: DEVICES, states });
  assert.equal(plan.label, 'All off'); assert.equal(plan.energizing, false); assert.equal(plan.exactUndo, true);
  const applied = await Q.applyQuick(plan, { states, dispatch: ha.dispatch, isCurrent: () => true });
  assert.deepEqual({ ok: applied.ok, sent: applied.sent, exactUndo: applied.exactUndo }, { ok: true, sent: 4, exactUndo: true });
  assert.ok(ids.every(id => states[id].state === 'off'));
  assert.equal(states['switch.fan'].state, 'off', 'a non-light switch is not part of All off');
  const undone = await Q.undoQuick(applied.before, { dispatch: ha.dispatch, isCurrent: () => true });
  assert.deepEqual({ ok: undone.ok, exact: undone.exact, skipped: undone.skipped }, { ok: true, exact: true, skipped: [] });
  assert.deepEqual(exactView(states, ids), original, 'Undo restores state, brightness and colour exactly');
  assert.ok(applied.before.every(entry => Q.matchesSnapshot(entry, states[entry.entity_id])));
  assert.equal(ha.calls.length, 8);
  assert.ok(ha.calls.every(call => ['light', 'switch'].includes(call.domain)));
});

test('All on then Undo turns the previously off light back off and keeps the lit ones exact', async () => {
  const states = initialStates(), ha = authoredHa(states), ids = Q.echoLightEntityIds(DEVICES);
  const original = exactView(states, ids);
  const plan = Q.planQuick({ kind: 'all', on: true }, { devices: DEVICES, states });
  assert.equal(plan.energizing, true);
  const applied = await Q.applyQuick(plan, { states, dispatch: ha.dispatch, isCurrent: () => true });
  assert.equal(states['light.front_right'].state, 'on');
  await Q.undoQuick(applied.before, { dispatch: ha.dispatch, isCurrent: () => true });
  assert.deepEqual(exactView(states, ids), original);
});

test('a scene chip applies once and Undo restores its member lights exactly', async () => {
  const states = initialStates(), ha = authoredHa(states);
  const members = ['light.front_left', 'light.front_right', 'switch.desk_lamp'], original = exactView(states, members);
  const plan = Q.planQuick({ kind: 'chip', entity_id: 'scene.evening' }, { devices: DEVICES, states });
  assert.deepEqual(plan.calls, [{ domain: 'scene', service: 'turn_on', data: { entity_id: 'scene.evening' } }]);
  assert.deepEqual(plan.targets, members); assert.equal(plan.exactUndo, true); assert.equal(plan.note, '');
  const applied = await Q.applyQuick(plan, { states, dispatch: ha.dispatch, isCurrent: () => true });
  assert.equal(applied.sent, 1); assert.equal(states['light.front_left'].attributes.brightness, 64);
  const undone = await Q.undoQuick(applied.before, { dispatch: ha.dispatch, isCurrent: () => true });
  assert.equal(undone.exact, true); assert.deepEqual(exactView(states, members), original);
  assert.equal(ha.calls.filter(call => call.domain === 'scene').length, 1, 'the scene applied exactly once');
});

test('scenes with other members and scripts disclose that Undo is not exact', () => {
  const states = initialStates();
  const movie = Q.planQuick({ kind: 'chip', entity_id: 'scene.movie' }, { devices: DEVICES, states });
  assert.equal(movie.exactUndo, false); assert.match(movie.note, /other effects stay/);
  assert.deepEqual(movie.targets, ['light.front_left']);
  const script = Q.planQuick({ kind: 'chip', entity_id: 'script.wind_down' }, { devices: DEVICES, states });
  assert.equal(script.exactUndo, false); assert.equal(script.energizing, true);
  assert.match(script.note, /can't be undone exactly/);
  assert.deepEqual(script.targets, Q.echoLightEntityIds(DEVICES));
  assert.equal(Q.planQuick({ kind: 'chip', entity_id: 'automation.porch' }, { devices: DEVICES, states }), null);
  assert.equal(Q.planQuick({ kind: 'chip', entity_id: 'scene.unknown' }, { devices: DEVICES, states }), null);
});

test('chips are the home scenes then scripts, scripts marked as runs, and Victoria has none', () => {
  const states = { ...initialStates(), 'scene.broken': light('unavailable', { friendly_name: 'Broken' }) };
  const chips = Q.quickChips(states);
  assert.deepEqual(chips.map(chip => [chip.entity_id, chip.kind, chip.runs, chip.name]), [
    ['scene.broken', 'scene', false, 'Broken'], ['scene.evening', 'scene', false, 'Evening'], ['scene.movie', 'scene', false, 'Movie'],
    ['script.wind_down', 'script', true, 'Wind down']]);
  assert.deepEqual(chips.map(chip => chip.unavailable), [true, false, false, false], 'a never-run scene reports unknown and stays usable');
  assert.equal(Q.planQuick({ kind: 'chip', entity_id: 'scene.broken' }, { devices: DEVICES, states }), null);
  assert.deepEqual(Q.quickChips({}), []);
  assert.equal(Q.planQuick({ kind: 'all', on: true }, { devices: [], states: {} }), null, 'no lights means no plan');
});

test('a stale context stops dispatch before the next call and Undo never runs stale', async () => {
  const states = initialStates(), ha = authoredHa(states);
  let current = true;
  const dispatch = async (...args) => { const result = await ha.dispatch(...args); current = false; return result; };
  const plan = Q.planQuick({ kind: 'all', on: false }, { devices: DEVICES, states });
  const applied = await Q.applyQuick(plan, { states, dispatch, isCurrent: () => current });
  assert.deepEqual({ ok: applied.ok, sent: applied.sent, reason: applied.reason }, { ok: false, sent: 1, reason: 'stale' });
  const undone = await Q.undoQuick(applied.before, { dispatch: ha.dispatch, isCurrent: () => false });
  assert.equal(undone.sent, 0); assert.equal(ha.calls.length, 1);
});

test('a refused guarded call stops the batch; Travel Mode and offline block before dispatch', async () => {
  const states = initialStates(), ha = authoredHa(states, { refuse: (domain, service) => service === 'turn_on' });
  const plan = Q.planQuick({ kind: 'all', on: true }, { devices: DEVICES, states });
  const applied = await Q.applyQuick(plan, { states, dispatch: ha.dispatch, isCurrent: () => true });
  assert.deepEqual({ ok: applied.ok, sent: applied.sent, reason: applied.reason }, { ok: false, sent: 0, reason: 'refused' });
  assert.deepEqual(Q.quickBlock({ connection: 'online', travel: true }), { all: '', energizing: Q.QUICK_TRAVEL_REASON });
  assert.deepEqual(Q.quickBlock({ connection: 'online', travel: false }), { all: '', energizing: '' });
  for (const connection of ['offline', 'disconnected', 'connecting', 'auth_invalid']) {
    assert.deepEqual(Q.quickBlock({ connection, travel: false }), { all: Q.QUICK_OFFLINE_REASON, energizing: Q.QUICK_OFFLINE_REASON });
  }
  assert.equal(Q.quickBlock({ connection: 'online', sim: true }).all, 'Simulation · controls disabled');
});

test('HA offline (Echo): controls disabled with the HA-reported time, never an invented one', () => {
  const at = new Date(2026, 9, 19, 20, 58, 12).toISOString();
  const state = { ...ct(178, 2700), last_reported: at };
  const offline = Q.controlBlock({ connection: 'offline', state, entityId: 'light.front_left' });
  assert.deepEqual(offline, { disabled: true, status: 'offline', reason: 'Home Assistant is offline. Nothing is queued.', detail: 'Last reported 20:58' });
  const noTime = Q.controlBlock({ connection: 'offline', state: { state: 'on', attributes: {} }, entityId: 'light.front_left' });
  assert.equal(noTime.detail, 'Last reported time unavailable');
  assert.equal(Q.controlBlock({ connection: 'offline', state: { ...state, last_reported: 'not a time' }, entityId: 'x' }).detail, 'Last reported time unavailable');
  assert.equal(Q.controlBlock({ connection: 'online', state, entityId: 'light.front_left' }).disabled, false);
  assert.equal(Q.controlBlock({ connection: 'online', state: { state: 'unavailable' }, entityId: 'light.front_left' }).disabled, true);
  assert.match(Q.controlBlock({ connection: 'online', state: null, entityId: null }).reason, /Match it in Setup › Home Assistant/);
});

test('capabilities come from Home Assistant: brightness and colour temperature only where supported', () => {
  assert.deepEqual(Q.lightCapabilities('light.front_left', ct(178, 2700)), { power: true, brightness: true, colorTemp: { min: 2200, max: 6500 } });
  assert.deepEqual(Q.lightCapabilities('light.onoff', light('on', { supported_color_modes: ['onoff'] })), { power: true, brightness: false, colorTemp: null });
  assert.deepEqual(Q.lightCapabilities('switch.desk_lamp', light('on')), { power: true, brightness: false, colorTemp: null });
  assert.equal(Q.lightSummary('light.front_left', ct(178, 2700)), '70% · 2700 K');
  assert.equal(Q.lightSummary('switch.desk_lamp', light('on')), 'On');
  assert.equal(Q.lightSummary('light.x', { state: 'unavailable' }), 'Unavailable');
  assert.equal(Q.isEchoLightDevice({ type: 'plug', ha_entity_id: 'switch.desk_lamp' }), true);
  assert.equal(Q.isEchoLightDevice({ type: 'fan', ha_entity_id: 'switch.fan' }), false);
});

test('modeled Den batches restore their exact channel snapshot', () => {
  const fixture = id => ({ fixture_id: id, state: { pan_deg: 10, tilt_deg: 40,
    spotlight: { on: true, brightness: .55, color_temp_kelvin: 3000 }, downward: { on: false, brightness: .18, color_temp_kelvin: 2700 },
    radials: [1, 2, 3, 4, 5, 6].map(number => ({ number, on: number % 2 === 1, brightness: number / 10, color_temp_kelvin: 3000, azimuth_deg: number * 60 })) } });
  const fixtures = [fixture('a'), fixture('b')], before = Q.virtualSnapshot(fixtures);
  assert.equal(Q.virtualSummary(fixtures[0].state), 'Spot 55% · Radial 30%');
  const changed = structuredClone(fixtures[0].state);
  changed.spotlight.on = false; changed.radials.forEach(zone => { zone.on = false; });
  assert.equal(Q.virtualMatches(before[0], changed), false);
  const patch = Q.virtualRestorePatch(before[0]);
  assert.deepEqual(Object.keys(patch), ['spotlight', 'downward', 'radials'], 'restore never moves the aim');
  const restored = { ...changed, spotlight: { ...patch.spotlight }, downward: { ...patch.downward },
    radials: changed.radials.map(zone => ({ ...zone, ...patch.radials.find(item => item.number === zone.number) })) };
  assert.equal(Q.virtualMatches(before[0], restored), true);
});
