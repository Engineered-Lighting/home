/* APT-U03: Setup › Home Assistant link rows and the guarded Flash.
 * Runs the actual Apartment data boundary (HomeApartmentData.callService and
 * its Travel Mode guard) against an authored Home Assistant spy transport. No
 * real client, light or helper is touched. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Links = require('../app/src/home-apartment-setup-links.js');

const ROOT = path.join(__dirname, '..');
const dataSource = readFileSync(path.join(ROOT, 'app/src/home-apartment-data.js'), 'utf8');
const TRAVEL = 'input_boolean.living_lights_travel_mode', CACHE = 'home.lights.travelMode.state.v1';
const copy = value => JSON.parse(JSON.stringify(value));

function boundary({ helper = 'off', cached = null } = {}) {
  const listeners = new Map(), storage = new Map(cached ? [[CACHE, cached]] : []);
  const reads = [], dispatches = [];
  let site = 'echo';
  const window = {
    location: { protocol: 'https:', host: 'home.invalid' },
    HomeSpatialViewport: { getSelectedSiteId: () => site },
    addEventListener(type, fn) { const set = listeners.get(type) || new Set(); set.add(fn); listeners.set(type, set); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    dispatchEvent(event) { for (const fn of listeners.get(event.type) || []) fn(event); },
  };
  const client = {
    async call(payload) {
      if (payload.type === 'get_states') { reads.push(copy(payload)); return helper === null ? [] : [{ entity_id: TRAVEL, state: helper, attributes: {} }]; }
      dispatches.push(copy(payload)); return { accepted: true };
    },
    async callService(domain, service, data) { dispatches.push(copy({ domain, service, data })); return { accepted: true }; },
  };
  window.__hav_haClient = client;
  const localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  window.localStorage = localStorage;
  class CustomEvent { constructor(type, options) { this.type = type; this.detail = options.detail; } }
  vm.runInNewContext(dataSource, { window, localStorage, console, CustomEvent, setTimeout, clearTimeout, setInterval, clearInterval }, { filename: 'home-apartment-data.js' });
  return { D: window.HomeApartmentData, window, client, reads, dispatches, setSite: value => { site = value; } };
}

const devices = [
  { id: 'a', type: 'light', name: 'Ceiling A', ha_entity_id: 'light.ceiling_a', room_id: 'study', pos: [1, 1, 2.3] },
  { id: 'b', type: 'light', name: 'Ceiling B', ha_entity_id: 'light.shared', room_id: 'study', pos: [2, 2, 2.3] },
  { id: 'c', type: 'light', name: 'Lamp', ha_entity_id: 'light.shared', room_id: 'study', pos: [3, 1, .8] },
  { id: 'd', type: 'light', name: 'Unlinked', ha_entity_id: null, room_id: null, pos: [3, 3, 2.3] },
  { id: 'cam', type: 'camera', name: 'Camera', ha_entity_id: 'camera.study', room_id: 'study', pos: [0, 0, 2] },
];
const registry = [{ entity_id: 'light.ceiling_a', name: 'A' }, { entity_id: 'light.shared', name: 'Shared' },
  { entity_id: 'light.spare', name: 'Spare' }, { entity_id: 'switch.not_a_light', name: 'Switch' }];

test('rows read Linked, Shared with "Also controls" and Not linked; pickers offer only free exact lights', () => {
  const rows = Links.linkRows({ devices, zones: [{ id: 'study', name: 'Study' }], lights: registry });
  assert.deepEqual(rows.map(row => [row.id, row.state, row.room]), [['a', 'linked', 'Study'], ['b', 'shared', 'Study'], ['c', 'shared', 'Study'], ['d', 'not_linked', null]]);
  assert.deepEqual(rows[1].sharedWith, ['Lamp']); assert.deepEqual(rows[2].sharedWith, ['Ceiling B']);
  assert.deepEqual(rows[0].options.map(option => option.entity_id), ['light.ceiling_a', 'light.spare'], 'another light\'s link is never offered: no new duplicates');
  assert.deepEqual(rows[3].options.map(option => option.entity_id), ['light.spare']);
  const unregistered = Links.linkRows({ devices: [{ id: 'x', type: 'light', name: 'X', ha_entity_id: 'light.gone' }], lights: [] });
  assert.deepEqual(unregistered[0].options, [{ entity_id: 'light.gone', name: 'light.gone', missing: true }], 'an existing link stays visible when the registry is unavailable');
});

test('a registration home reads Not linked for every socket and fixture, with no picker', () => {
  const rows = Links.registrationRows({ equipment: [{ id: 's1', kind: 'mount', label: 'Socket 1' }, { id: 'f1', kind: 'fixture', label: 'Fixture 1' },
    { id: 'cam', kind: 'camera', label: 'OAK-D Pro' }] });
  assert.deepEqual(rows.map(row => [row.id, row.state, row.options.length, row.entity]), [['s1', 'not_linked', 0, null], ['f1', 'not_linked', 0, null]]);
});

test('Flash is refused before any dispatch for Victoria, simulation, offline, no link, Travel Mode on or unknown', () => {
  const ok = { siteId: 'echo', sim: false, connection: 'online', entityId: 'light.ceiling_a', travel: 'off' };
  assert.equal(Links.flashRefusal(ok), '');
  for (const [patch, pattern] of [[{ siteId: 'victoria' }, /only available/], [{ sim: true }, /Simulation/], [{ connection: 'offline' }, /offline/],
    [{ entityId: null }, /Link this light/], [{ entityId: 'switch.not_a_light' }, /Link this light/], [{ travel: 'on' }, /Travel Mode is on/], [{ travel: null }, /unknown/]]) {
    assert.match(Links.flashRefusal({ ...ok, ...patch }), pattern, JSON.stringify(patch));
  }
});

test('Flash with Travel Mode verified off sends exactly one guarded light.turn_on with Home Assistant\'s short flash', async () => {
  const f = boundary({ helper: 'off' });
  const result = await Links.flash({ client: f.client, entityId: 'light.ceiling_a', isCurrent: () => true, data: f.D });
  assert.deepEqual(result, { ok: true });
  assert.equal(f.reads.length, 1, 'Travel Mode is read from Home Assistant at the moment of output');
  assert.deepEqual(f.dispatches, [{ domain: 'light', service: 'turn_on', data: { entity_id: 'light.ceiling_a', flash: 'short' } }]);
});

for (const [name, setup, pattern] of [
  ['Travel Mode on', { helper: 'on' }, /Travel Mode is on/],
  ['Travel Mode unknown (no helper)', { helper: null }, /Travel Mode is unavailable/],
  ['Travel Mode unknown (unavailable state)', { helper: 'unavailable' }, /Travel Mode is unavailable/],
  ['a cached Travel Mode on that this client has not cleared', { helper: 'on', cached: 'on' }, /Travel Mode is on/],
]) {
  test(`Flash is refused with zero dispatch when ${name}`, async () => {
    const f = boundary(setup);
    const result = await Links.flash({ client: f.client, entityId: 'light.ceiling_a', isCurrent: () => true, data: f.D });
    assert.equal(result.ok, false); assert.match(result.reason, pattern);
    assert.deepEqual(f.dispatches, [], 'no service call of any kind');
  });
}

test('Flash is refused with zero dispatch when Home Assistant is offline, the home is not Echo, or the context changed', async () => {
  const f = boundary({ helper: 'off' });
  for (const [options, pattern] of [
    [{ client: null }, /offline; nothing was sent/],
    [{ siteId: 'victoria' }, /only available/],
    [{ entityId: 'light.ceiling_a', isCurrent: () => false }, /context changed; no command was sent/],
    [{ isCurrent: undefined }, /context is unavailable/],
  ]) {
    const result = await Links.flash({ client: f.client, entityId: 'light.ceiling_a', isCurrent: () => true, data: f.D, ...options });
    assert.equal(result.ok, false); assert.match(result.reason, pattern, JSON.stringify(Object.keys(options)));
  }
  f.setSite('victoria');
  const moved = await Links.flash({ client: f.client, entityId: 'light.ceiling_a', isCurrent: () => true, data: f.D });
  assert.equal(moved.ok, false); assert.match(moved.reason, /context changed/);
  assert.deepEqual(f.dispatches, []);
});

test('Flash is never queued: a Travel Mode change during the helper read refuses the pending Flash', async () => {
  const f = boundary({ helper: 'off' });
  const read = f.client.call.bind(f.client);
  f.client.call = async payload => {
    const states = await read(payload);
    if (payload.type === 'get_states') f.window.dispatchEvent({ type: 'home-travel-mode-change', detail: { state: 'on' } });
    return states;
  };
  const result = await Links.flash({ client: f.client, entityId: 'light.ceiling_a', isCurrent: () => true, data: f.D });
  assert.equal(result.ok, false); assert.match(result.reason, /Travel Mode is on/); assert.deepEqual(f.dispatches, []);
});
