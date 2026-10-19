#!/usr/bin/env node
// Command ladder (APT-A06) and Echo Manual state (APT-A09): the actual module
// source rendered through an element shim with authored synthetic data, plus
// source contracts for its mount points. Every transport global is a trap, so
// any request, socket or beacon fails the suite. This is not mounted React,
// browser, actual-room, installed or physical acceptance.
// Runs as plain `node`; node:test sets the exit code.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = name => fs.readFileSync(path.join(ROOT, name), 'utf8');
const babel = require(path.join(ROOT, 'app/src/vendor/babel-7.29.0/babel.min.js'));
const AIMING = require(path.join(ROOT, 'app/src/home-apartment-aiming.js'));
const SOURCE = read('app/src/home-apartment-command-ladder.jsx');
const PROGRAM = babel.transform(SOURCE, { presets: ['react'] }).code;

const hex = character => character.repeat(64);
const BINDING = Object.freeze({ content_revision: 'authored-content-7', registration_version: 3,
  manifest_digest: hex('a'), frame_digest: hex('b'), canonical_collision_digest: hex('c') });
const VICTORIA_LIMITS = Object.freeze({ pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90,
  limit_role: 'virtual_preview', physical_travel: 'unknown' });
const victoriaFixture = (state = {}) => ({ fixture_id: 'socket-alpha', label: 'Socket Alpha', limits: { ...VICTORIA_LIMITS },
  profile_status: 'display_model', optical_origin_m: null,
  state: { pan_deg: 31.26, tilt_deg: 42.04, spotlight: { on: true, brightness: 0.8, color_temp_kelvin: 3000 }, ...state } });
const echoFixture = () => ({ id: 'authored-echo-spot', name: 'Authored Spot', pos: [1, 1, 2.4],
  fixture_calibration: { status: 'proposed' }, ...AIMING.defaultEngineeredFixtureFields() });
const ECHO_BLOCKERS = ['no authoritative Apartment → Product target-plane binding', 'no Home owner-authority delegation', 'Product adoption is false'];
const PLANE = { kind: 'qualified_target_plane', origin: [0, 0, 0], x_axis: [1, 0, 0], y_axis: [0, 1, 0],
  tolerance_m: 0.01, apartment_frame_digest: hex('d'), product_profile_sha256: hex('e') };

/* A fresh sandbox per render: element shim, one-slot useState, clipboard spy
 * and transport traps that count and throw. */
function sandbox() {
  const transport = [], clipboard = [], state = { slots: [], cursor: 0 };
  const trap = name => function () { transport.push(name); throw Error(`transport attempted: ${name}`); };
  const element = (type, props, ...children) => ({ type, props: { ...(props || {}), children: children.flat(Infinity) } });
  const window = {
    HomeApartmentAiming: AIMING,
    fetch: trap('fetch'), XMLHttpRequest: trap('XMLHttpRequest'), WebSocket: trap('WebSocket'), EventSource: trap('EventSource'),
  };
  const navigator = { sendBeacon: trap('sendBeacon'),
    clipboard: { writeText: text => { clipboard.push(text); return { then: resolve => resolve() }; } } };
  const context = vm.createContext({ window, navigator, fetch: window.fetch, XMLHttpRequest: window.XMLHttpRequest,
    WebSocket: window.WebSocket, EventSource: window.EventSource,
    React: { createElement: element, useState: initial => {
      const index = state.cursor++;
      if (!(index in state.slots)) state.slots[index] = initial;
      return [state.slots[index], value => { state.slots[index] = value; }];
    } } });
  vm.runInContext(PROGRAM, context, { timeout: 1000 });
  const render = (Component, props) => { state.cursor = 0; return expand(Component(props)); };
  return { window, transport, clipboard, render };
}
// Function components (the Badge fallback) render inline.
function expand(node) {
  if (Array.isArray(node)) return node.map(expand);
  if (!node || typeof node !== 'object') return node;
  if (typeof node.type === 'function') return expand(node.type(node.props));
  return { ...node, props: { ...node.props, children: node.props.children.map(expand) } };
}
function nodes(tree, out = []) {
  if (Array.isArray(tree)) { tree.forEach(item => nodes(item, out)); return out; }
  if (!tree || typeof tree !== 'object') return out;
  out.push(tree); tree.props.children.forEach(item => nodes(item, out)); return out;
}
const text = tree => Array.isArray(tree) ? tree.map(text).join('')
  : tree == null || tree === false || tree === true ? '' : typeof tree === 'object' ? text(tree.props.children) : String(tree);
const find = (tree, predicate) => nodes(tree).filter(predicate);
const one = (tree, predicate, label) => { const found = find(tree, predicate); assert.equal(found.length, 1, label); return found[0]; };
const attr = (name, value) => node => value === undefined ? node.props[name] !== undefined : node.props[name] === value;
const button = (tree, label) => one(tree, node => node.type === 'button' && text(node) === label, `one ${label} button`);
const steps = tree => find(tree, attr('data-apt-command-step')).map(node => ({ key: node.props['data-apt-command-step'],
  title: text(node.props.children[1].props.children[0]), detail: text(node.props.children[1].props.children[1]) }));
function clickEnabled(tree) {
  for (const node of find(tree, node => node.type === 'button' && !node.props.disabled)) node.props.onClick?.();
}
const RESERVED = /Return to room|Return to Auto|\bClose\b|\bBack\b/;

test('Victoria collapses to one status line: Modeled · nothing sent', () => {
  const { window, render } = sandbox();
  const tree = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING });
  const disc = find(tree, node => node.type === 'details');
  assert.ok(disc.length >= 1);
  assert.equal(disc[0].props.open, undefined, 'collapsed by default');
  const summary = disc[0].props.children.find(child => child?.type === 'summary');
  assert.equal(text(summary).replace(/\s+/g, ' ').trim(), 'Command Modeled · nothing sent');
  assert.equal(text(one(tree, attr('data-apt-command-status'), 'one status')), 'Modeled · nothing sent');
  assert.equal(tree.props['data-apt-command-ladder'], 'modeled');
});

test('Victoria expanded copy: intent first, then the four steps, never within-limit', () => {
  const { window, render } = sandbox();
  const tree = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING });
  assert.equal(text(one(tree, attr('data-apt-command-intent'), 'intent')),
    "Point Socket Alpha's spotlight at pan 31° and tilt 42°, 80% and 3000 K.");
  assert.deepEqual(steps(tree), [
    { key: 'draft', title: 'Current aim', detail: 'Modeled aim in this view.' },
    { key: 'modeled', title: 'Modeled command', detail: 'Modeled in the Apartment frame · pan 31°, tilt 42° · travel limits not commissioned' },
    { key: 'accepted', title: 'Accepted execution', detail: 'not available · not commissioned' },
    { key: 'reported', title: 'Reported state', detail: 'none · nothing physical reports back for this socket' },
  ]);
  const all = text(tree);
  assert.doesNotMatch(all, /within/i, 'never claims within-limit pan/tilt');
  assert.doesNotMatch(all, /\b(sent|accepted|applied) to (the )?(light|hardware|device)\b/i);
  assert.match(all, /travel limits not commissioned/);
  assert.doesNotMatch(all, RESERVED, 'ladder wording never collides with exits');
  const off = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture({ spotlight: { on: false } }), binding: BINDING });
  assert.equal(text(one(off, attr('data-apt-command-intent'), 'intent')), "Keep Socket Alpha's spotlight off, aimed at pan 31° and tilt 42°.");
  const draft = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING, draft: { origin: 'manual' } });
  assert.deepEqual(steps(draft)[0], { key: 'draft', title: 'Draft target', detail: 'Proposed in this view. Nothing has changed yet.' });
});

test('Victoria exact output: modeled JSON only, no canonical command, Copy request disabled', () => {
  const { window, render } = sandbox();
  const tree = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING });
  const modeled = JSON.parse(text(one(tree, attr('data-apt-command-modeled'), 'modeled pre')));
  assert.deepEqual(modeled, { frame: 'apartment', frame_digest: hex('b'), fixture_id: 'socket-alpha', pan_deg: 31.3, tilt_deg: 42,
    spotlight: { on: true, brightness: 0.8, color_temp_kelvin: 3000 }, limit_role: 'virtual_preview', physical_travel: 'unknown',
    optical_origin_m: null, physical_commands: false });
  assert.equal(text(one(tree, attr('data-apt-command-request'), 'request pre')),
    '// No canonical command exists for an uncommissioned socket.\n// Nothing here is a device packet.');
  assert.equal(find(tree, attr('data-apt-command-simulation')).length, 0, 'no device payload of any kind');
  const copyRequest = button(tree, 'Copy request');
  assert.equal(copyRequest.props.disabled, true);
  assert.equal(copyRequest.props.title, 'Copy request is blocked · no executable request exists.');
  assert.equal(text(one(tree, attr('data-apt-command-copy-blocked'), 'visible reason')), copyRequest.props.title);
  assert.notEqual(button(tree, 'Copy intent').props.disabled, true);
  assert.notEqual(button(tree, 'Copy modeled JSON').props.disabled, true);
});

test('Victoria provenance comes from the binding keys and discloses assumed profile values', () => {
  const { window, render } = sandbox();
  const tree = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING });
  const list = one(tree, node => node.type === 'dl', 'provenance list');
  const pairs = {}; let key = null;
  for (const node of nodes(list)) {
    if (node.type === 'dt') key = text(node); else if (node.type === 'dd') pairs[key] = text(node);
  }
  assert.deepEqual(pairs, {
    Model: 'Den registration · revision authored-content-7', Registration: 'version 3',
    Frame: 'Apartment frame · bbbbbbbb', Scene: 'manifest · aaaaaaaa', Collision: 'canonical collision · cccccccc',
    Optic: 'Display model · profile values are assumed defaults, not measured',
    Origin: 'Socket position · optical origin not commissioned', Data: 'Authored equipment registration',
  });
  const unbound = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: null });
  assert.match(text(unbound), /Den registration · not bound/);
});

test('Victoria copy actions use the clipboard only, with zero transport', () => {
  const { window, render, transport, clipboard } = sandbox();
  const tree = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING });
  clickEnabled(tree);
  assert.deepEqual(transport, []);
  const ladder = window.HomeApartmentCommandLadder.victoriaLadder({ fixture: victoriaFixture(), binding: BINDING });
  assert.deepEqual(clipboard, [ladder.intent, ladder.exact.modeledJson]);
  const after = render(window.AptCommandLadder, { site: 'victoria', fixture: victoriaFixture(), binding: BINDING });
  assert.equal(text(one(after, node => node.props.role === 'status', 'copy status')), 'Modeled JSON copied');
});

test('Echo simulated: execution blocked, Product Aim request with nulls and blockers, never copyable', () => {
  const { window, render, transport, clipboard } = sandbox();
  const fixture = echoFixture(), destination = { kind: 'named', id: 'desk', name: 'Desk', pos: [2, 2, 0.75] };
  const solve = AIMING.solveAim(fixture.pos, destination.pos, { limits: fixture.gimbal.limits });
  const product = AIMING.productAimRequest(destination.pos, { ...PLANE, origin: [0, 0, 0.75] }, 0, hex('e'));
  assert.equal(product.qualified, true, 'authored plane qualifies through the real builder');
  const props = { site: 'echo', sim: true, fixture, model: { revision: 4 }, destination, isCurrent: false, solve, product,
    spotlight: { state: 'on', attributes: { brightness: 204, color_temp_kelvin: 2700 } }, telemetry: { valid: true }, blockers: ECHO_BLOCKERS };
  const tree = render(window.AptCommandLadder, props);
  assert.equal(text(one(tree, attr('data-apt-command-status'), 'status')), 'Simulated · execution blocked');
  assert.equal(text(one(tree, attr('data-apt-command-intent'), 'intent')), "Point Authored Spot's spotlight at Desk, 80% and 2700 K.");
  const ladder = steps(tree);
  assert.deepEqual(ladder.map(step => step.title), ['Draft target', 'Modeled command', 'Accepted execution', 'Reported state']);
  assert.equal(ladder[2].detail, 'blocked · no movement authority and no target-plane binding');
  assert.equal(ladder[3].detail, 'none · simulated pose only, never live');
  assert.match(ladder[1].detail, /^Solved in the Apartment frame · pan -?\d+°, tilt -?\d+° · travel limits not commissioned$/);
  assert.equal(text(one(tree, attr('data-apt-command-request'), 'request')),
    'POST /api/group/aim\n{\n  "target_x_m": null,\n  "target_y_m": null,\n  "post_dwell_ms": null\n}');
  assert.deepEqual(find(tree, attr('data-apt-command-blocker')).map(text), ECHO_BLOCKERS.map(item => `blocked · ${item}`));
  const simulation = text(one(tree, attr('data-apt-command-simulation'), 'builder output in simulation'));
  assert.equal(simulation, `SIMULATION ONLY\nPOST /api/group/aim\n${JSON.stringify(product.body, null, 2)}`);
  assert.equal(button(tree, 'Copy request').props.disabled, true);
  clickEnabled(tree);
  assert.deepEqual(transport, []);
  assert.ok(clipboard.every(value => !value.includes('target_x_m') && !value.includes('SIMULATION ONLY')), 'request body never copied');
  assert.doesNotMatch(text(tree), /within/i);
  assert.doesNotMatch(text(tree), RESERVED);
  const modeled = JSON.parse(text(one(tree, attr('data-apt-command-modeled'), 'modeled')));
  assert.deepEqual(Object.keys(modeled), ['frame', 'fixture_id', 'origin_m', 'destination_m', 'surface', 'pan_deg', 'tilt_deg',
    'executable', 'violations', 'full_fwhm_deg']);
  assert.equal(modeled.pan_deg, Math.round(solve.pan_deg * 10) / 10);
});

test('Echo live preview: unqualified builder result, unexecutable solve and no telemetry stay explicit', () => {
  const { window, render } = sandbox();
  const fixture = echoFixture(), destination = { kind: 'mesh', pos: [1, 1, 0] };
  const solve = AIMING.solveAim(fixture.pos, destination.pos, { limits: fixture.gimbal.limits });
  const product = AIMING.productAimRequest(destination.pos, null, 0, null);
  const tree = render(window.AptCommandLadder, { site: 'echo', sim: false, fixture, model: { revision: 4 }, destination,
    isCurrent: true, solve, product, spotlight: { state: 'off' }, telemetry: { valid: false }, blockers: ECHO_BLOCKERS });
  assert.equal(text(one(tree, attr('data-apt-command-status'), 'status')), 'Preview only · execution blocked');
  const ladder = steps(tree);
  assert.deepEqual(ladder[0], { key: 'draft', title: 'Current aim', detail: 'Previewed aim in this view.' });
  assert.match(ladder[1].detail, /not executable: vertical target pan indeterminate$/);
  assert.equal(ladder[3].detail, 'no telemetry · the bench is loopback-only and not open here');
  assert.equal(find(tree, attr('data-apt-command-simulation')).length, 0, 'no builder body outside simulation');
  const offPlane = AIMING.productAimRequest([0, 0, 2], PLANE, 0, hex('e'));
  const blocked = render(window.AptCommandLadder, { site: 'echo', sim: true, fixture, destination, solve, product: offPlane, blockers: ECHO_BLOCKERS });
  assert.ok(find(blocked, attr('data-apt-command-blocker')).map(text).includes('blocked · destination off qualified plane'));
  assert.equal(find(blocked, attr('data-apt-command-simulation')).length, 0);
  const none = render(window.AptCommandLadder, { site: 'echo', sim: true, fixture, blockers: ECHO_BLOCKERS });
  assert.equal(text(one(none, attr('data-apt-command-intent'), 'intent')), "Choose where Authored Spot's spotlight should point.");
});

test('the manual-hold light map matches the generated Living Lights detection package', () => {
  const yaml = read('ha-config/packages/living_lights_manual_detection.yaml');
  const generated = {};
  for (const block of yaml.split(/\n  - alias: /)) {
    const light = /^"Living Lights - fast manual hold arm \((light\.[a-z0-9_]+)\)"/.exec(block)?.[1];
    if (!light) continue;
    generated[light] = [...new Set([...block.matchAll(/input_datetime\.living_lights_zone_([a-z0-9_]+)_last_manual_at/g)].map(match => match[1]))];
  }
  const { window } = sandbox();
  assert.ok(Object.keys(generated).length > 0);
  assert.deepEqual(JSON.parse(JSON.stringify(window.HomeApartmentCommandLadder.LIVING_LIGHTS_ZONES_BY_LIGHT)), generated);
});

test('Echo Manual state is read-only "Changed by hand" from last_manual_at, hidden without a hold or offline', () => {
  const { window, render, transport } = sandbox();
  const H = window.HomeApartmentCommandLadder;
  const now = new Date(2026, 9, 19, 21, 5).getTime();
  const stamp = (zone, value) => ({ [`input_datetime.living_lights_zone_${zone}_last_manual_at`]: { state: value } });
  const sentinel = { ...stamp('weights', '1970-01-01 00:00:00'), ...stamp('sofa', '1970-01-01 00:00:00') };
  assert.equal(H.manualHold('light.rear_right', sentinel, { now }), null, 'the 1970 sentinel is no hold');
  assert.equal(H.manualHold('light.rear_right', {}, { now }), null);
  assert.equal(H.manualHold('light.not_a_living_light', stamp('sofa', '2026-10-19 20:58:00'), { now }), null);
  const held = { ...sentinel, ...stamp('sofa', '2026-10-19 20:58:00'), ...stamp('weights', '2026-10-19 20:41:12') };
  assert.deepEqual(JSON.parse(JSON.stringify(H.manualHold('light.rear_right', held, { now }))), { zones: ['weights', 'sofa'], at: '20:58' });
  assert.equal(H.manualHold('light.rear_left', stamp('sofa', '2026-10-18T07:03:00'), { now }).at, 'Oct 18, 07:03');

  const tree = render(window.AptManualState, { entityId: 'light.rear_right', states: held, now });
  assert.equal(tree.props['data-apt-manual-state'], '1');
  assert.match(text(tree), /^ManualChanged by hand 20:58\. Adaptive lighting is paused for this light\./);
  assert.equal(find(tree, node => ['button', 'input', 'select', 'a'].includes(node.type)).length, 0, 'read-only: no control');
  assert.doesNotMatch(text(tree), RESERVED, 'no Return to Auto offer; never collides with Return to room or Close');
  assert.equal(render(window.AptManualState, { entityId: 'light.rear_right', states: held, now, offline: true }), null);
  assert.equal(render(window.AptManualState, { entityId: 'light.rear_right', states: sentinel, now }), null);
  assert.equal(render(window.AptManualState, { entityId: null, states: held, now }), null);
  assert.deepEqual(transport, []);
});

test('the module has no transport or Home Assistant write path', () => {
  for (const token of ['fetch(', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'sendBeacon', 'callService', 'haCallService',
    '__hav_haClient', 'call_service', 'set_datetime', '/api/']) {
    assert.ok(!SOURCE.includes(token) || (token === '/api/' && /DEFAULT_PRODUCT_AIM_PATH = "\/api\/group\/aim"/.test(SOURCE)
      && SOURCE.split('/api/').length === 2), `no ${token}`);
  }
});

test('one mount in each Aim tab; currentness guard and registration lists intact', () => {
  const apartment = read('app/src/home-apartment.jsx'), aim = read('app/src/home-apartment-aim.jsx');
  const victoriaAim = apartment.slice(apartment.indexOf('{tab === "aim" && <section className="apt-ui-section" id="apt-ui-virtual-aim"'));
  assert.equal(apartment.split('<window.AptCommandLadder').length, 2, 'one Victoria mount');
  assert.ok(victoriaAim.slice(0, victoriaAim.indexOf('</section>}')).includes('<window.AptCommandLadder site="victoria" fixture={fixture} binding={commandBinding} />'));
  assert.ok(apartment.includes('commandBinding={virtualFixtureDocument.binding}'));
  assert.equal(aim.split('<window.AptCommandLadder').length, 2, 'one Echo mount');
  assert.ok(aim.includes('<window.AptCommandLadder site="echo" sim={sim}'));
  assert.ok(aim.includes('<window.AptManualState entityId={primaryEntity} states={states} offline={sim || connection !== "online"} />'));
  assert.ok(!aim.includes('Authoritative Product Aim request'), 'the legacy Product Aim block moved into the ladder');
  assert.ok(aim.includes('typeof latest.isControlCurrent === "function" && latest.isControlCurrent() === true'), 'currentness guard unchanged');
  const index = read('app/src/index.html'), loader = read('app/src/home-feature-loader.js'), worker = read('app/src/home-service-worker.js');
  assert.ok(index.includes("['home-apartment-cards.jsx', true],\n        ['home-apartment-command-ladder.jsx', true],\n        ['home-apartment-aim.jsx', true],"));
  assert.ok(index.includes("'home-apartment-command-ladder.jsx': true,"));
  assert.ok(loader.includes('["home-apartment-command-ladder.jsx", true],\n        ["home-apartment-aim.jsx", true],'));
  assert.ok(worker.includes('"/home-apartment-command-ladder.jsx",'));
});
