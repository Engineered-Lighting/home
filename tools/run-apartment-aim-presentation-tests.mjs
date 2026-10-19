#!/usr/bin/env node
// Unwired aim presentation and room overlay. Authored synthetic data only: no
// real homes, coordinates or digests. Runs as plain `node`; node:test sets the
// exit code. Real vendored Three and the real proposal store; no WebGL, DOM,
// network, timer or browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';

const require = createRequire(import.meta.url);
const P = require('../app/src/home-apartment-aim-proposals.js');
const SRC = new URL('../app/src/', import.meta.url);
const read = name => fs.readFileSync(new URL(name, SRC), 'utf8');
const vendor = new URL('../app/src/vendor/three-0.180.0/three.module.js', import.meta.url).href;
const source = read('home-3d/apartment-aim-presentation.mjs');
const imports = /import \* as THREE from 'three';/g;
assert.equal([...source.matchAll(imports)].length, 1, 'one exact bare Three resolution seam');
const A = await import('data:text/javascript;base64,' + Buffer.from(source.replace(imports,
  `import * as THREE from ${JSON.stringify(vendor)};`)).toString('base64'));

// Fixed before the first run (2026-10-19); never raised to make a run pass.
const DEADLINE_MS = Object.freeze({ unit: 5000 });

// The test-facing vocabulary, copied from SPEC independently of the product.
const VOCABULARY = {
  root: 'data-apt-room-overlay', label: 'data-apt-fixture-label', ring: 'data-apt-fixture-ring',
  reticle: 'data-apt-aim-reticle', chip: 'data-apt-aim-chip', chipTitle: 'data-apt-aim-chip-title',
  chipMetrics: 'data-apt-aim-chip-metrics', chipNote: 'data-apt-aim-chip-note', disclosure: 'data-apt-aim-disclosure',
  proposal: 'data-apt-aim-proposal', blocker: 'data-apt-aim-blocker', bar: 'data-apt-aim-bar',
  barTarget: 'data-apt-aim-bar-target', action: 'data-apt-aim-action', pending: 'data-apt-aim-pending', state: 'data-state',
  aria: { reticle: 'Aim handle. Drag onto a surface, or use the arrow keys. Enter keeps the proposal, Escape cancels.',
    bar: 'Proposed aim', set: 'Set aim', discard: 'Discard', pending: 'aim pending' },
};
const VICTORIA_ORIGIN = 'Modeled from the socket display point · optical pivot not registered';
const VICTORIA_TRAVEL = 'travel limits not commissioned';

const hex = character => character.repeat(64);
// The fixture module's display-only limits, copied: no commissioned travel.
const VIRTUAL_LIMITS = Object.freeze({ pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90,
  limit_role: 'virtual_preview', physical_travel: 'unknown' });
const COMMISSIONED = Object.freeze({ pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: 15, tilt_max_deg: 90,
  limit_role: 'simulated', physical_travel: 'commissioned' });
const descriptor = (extra = {}) => ({ schema: 'home-virtual-fixture-interaction-1', generation: 1, fixture_id: 'authored-spot',
  mount_id: 'authored-mount', binding: { content_revision: 'authored-1', registration_version: 1, manifest_digest: hex('a'),
    frame_digest: hex('b'), canonical_collision_digest: hex('c') }, equipment_revision: 1, equipment_digest: hex('d'),
  display_anchor_m: [0, 0, 2.14], display_origin_m: [0, 0, 2], horizontal_world_reference: [1, 0, 0], origin_role: 'mount_anchor',
  optical_origin_m: null, state: { pan_deg: 0, tilt_deg: 90 }, target: null, truth_role: 'virtual_preview', physical_commands: false, ...extra });
const floor = point => ({ point, normal: [0, 0, 1], surfaceName: 'Floor' });
const near = (actual, expected, epsilon = 1e-9) => assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} ≈ ${expected}`);
const deg = Math.PI / 180;

test('the product vocabulary equals the SPEC copy fixed before code', { timeout: DEADLINE_MS.unit }, () => {
  assert.deepEqual(JSON.parse(JSON.stringify(A.AIM_OVERLAY_VOCABULARY)), VOCABULARY);
  assert.ok(Object.isFrozen(A.AIM_OVERLAY_VOCABULARY) && Object.isFrozen(A.AIM_OVERLAY_VOCABULARY.aria));
  const jsx = read('home-apartment-room-overlay.jsx');
  for (const [key, attribute] of Object.entries(VOCABULARY)) if (typeof attribute === 'string' && key !== 'state')
    assert.ok(jsx.includes(attribute + '='), 'overlay renders ' + attribute);
  assert.equal(A.AIM_OVERLAY_COPY.opticalOrigin, VICTORIA_ORIGIN);
  assert.equal(A.AIM_OVERLAY_COPY.travelLimits, VICTORIA_TRAVEL);
  assert.equal(A.AIM_OVERLAY_COPY.noSurface, 'No surface here');
  assert.equal(A.AIM_OVERLAY_COPY.outOfReach(80, 75), 'Out of reach · 80° from straight down, limit 75°');
  assert.equal(A.AIM_OVERLAY_COPY.blocked('Desk chair'), 'Blocked by Desk chair');
  assert.equal(A.AIM_OVERLAY_COPY.snaps('Painting'), 'Snaps to “Painting”');
  assert.equal(A.AIM_OVERLAY_COPY.grazing, 'the pool will be long and dim');
});

test('a Victoria hit reports surface, distance and tilt from the socket display point with both disclosures', { timeout: DEADLINE_MS.unit }, () => {
  const model = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([1, 0, 0]) });
  assert.equal(model.valid, true); assert.equal(model.reason, null);
  assert.equal(model.title, 'Floor'); assert.equal(model.surface, 'Floor');
  near(model.distance_m, Math.hypot(1, 2)); near(model.tilt_deg, Math.atan2(1, 2) / deg);
  assert.equal(model.metrics, `${Math.hypot(1, 2).toFixed(2)} m · ${Math.round(Math.atan2(1, 2) / deg)}° tilt`);
  assert.deepEqual(model.disclosures.map(item => [item.id, item.text]), [['optical-origin', VICTORIA_ORIGIN], ['travel-limits', VICTORIA_TRAVEL]]);
  assert.ok(Object.isFrozen(model) && Object.isFrozen(model.point_m) && Object.isFrozen(model.disclosures));
});

test('Victoria never claims reach: steep and far hits stay valid and never say out of reach', { timeout: DEADLINE_MS.unit }, () => {
  for (const angle of [0, 30, 60, 75, 80, 85, 89]) {
    const x = Math.tan(angle * deg) * 2;
    const model = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([x, 0, 0]) });
    assert.equal(model.valid, true, angle + '°');
    const text = [model.title, model.metrics, model.note, ...model.disclosures.map(item => item.text)].join(' ');
    assert.doesNotMatch(text, /out of reach|\breach(?:able|es)?\b|in range|within range|measured/i, angle + '°');
  }
  // Missing limits are not commissioned either.
  const missing = A.describeAimHit({ descriptor: descriptor(), limits: null, hit: floor([3, 0, 0]) });
  assert.deepEqual(missing.disclosures.map(item => item.id), ['optical-origin', 'travel-limits']);
  assert.equal(A.commissionedTiltLimit(VIRTUAL_LIMITS), null);
  assert.equal(A.commissionedTiltLimit({ ...COMMISSIONED, limit_role: 'virtual_preview' }), null);
  assert.equal(A.commissionedTiltLimit({ ...COMMISSIONED, physical_travel: 'unknown' }), null);
});

test('out of reach appears only with commissioned travel, with the SPEC angle and limit', { timeout: DEADLINE_MS.unit }, () => {
  const steep = A.describeAimHit({ descriptor: descriptor(), limits: COMMISSIONED, hit: floor([Math.tan(80 * deg) * 2, 0, 0]) });
  assert.equal(steep.valid, false); assert.equal(steep.reason, 'out_of_reach');
  assert.equal(steep.title, 'Out of reach · 80° from straight down, limit 75°');
  assert.deepEqual(steep.disclosures.map(item => item.id), ['optical-origin']);
  const inside = A.describeAimHit({ descriptor: descriptor(), limits: COMMISSIONED, hit: floor([Math.tan(70 * deg) * 2, 0, 0]) });
  assert.equal(inside.valid, true);
});

test('no surface, blocked, grazing, too close and a missing origin each say why', { timeout: DEADLINE_MS.unit }, () => {
  const none = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: null });
  assert.deepEqual([none.valid, none.reason, none.title, none.point_m], [false, 'no_surface', 'No surface here', null]);
  assert.equal(A.proposalTargetFromModel(none), null, 'no spot, nothing to propose');
  const traces = [];
  const blocked = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([1, 1, 0]),
    traceBeam: (from, to) => { traces.push([from, to]); return { point_m: [.5, .5, 1], name: 'Desk chair' }; } });
  assert.deepEqual([blocked.valid, blocked.reason, blocked.title], [false, 'blocked', 'Blocked by Desk chair']);
  assert.deepEqual(blocked.blocker_point_m, [.5, .5, 1]);
  assert.deepEqual(traces, [[[0, 0, 2], [1, 1, 0]]]);
  const thrown = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([1, 1, 0]), traceBeam: () => { throw Error('authored'); } });
  assert.equal(thrown.valid, true, 'a failing trace never invents a blocker');
  // A wall almost parallel to the beam: allowed, with the note.
  const grazing = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS,
    hit: { point: [2, 0, 1.75], normal: [0, 0, -1], surfaceName: 'Shelf underside' } });
  assert.equal(grazing.valid, true); assert.equal(grazing.note, 'the pool will be long and dim');
  const head = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([0.1, 0, 0]) });
  assert.equal(head.note, null);
  const close = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([0, 0, 1.99]) });
  assert.equal(close.reason, 'not_aimable');
  const lost = A.describeAimHit({ descriptor: descriptor({ display_origin_m: null, display_anchor_m: null }), limits: VIRTUAL_LIMITS, hit: floor([1, 0, 0]) });
  assert.deepEqual([lost.valid, lost.reason], [false, 'not_aimable']);
});

test('a registered optical origin measures from itself and drops the socket disclosure', { timeout: DEADLINE_MS.unit }, () => {
  const model = A.describeAimHit({ descriptor: descriptor({ optical_origin_m: [0, 0, 1] }), limits: VIRTUAL_LIMITS, hit: floor([1, 0, 0]) });
  near(model.distance_m, Math.SQRT2);
  assert.deepEqual(model.disclosures.map(item => item.id), ['travel-limits']);
});

test('near a named aim the drag snaps to it and says so', { timeout: DEADLINE_MS.unit }, () => {
  const namedAims = [{ id: 'aim-painting', name: 'Painting', point_m: [2, 3, 1.5], normal_m: [0, -1, 0], size_m: [.9, .65] },
    { id: 'aim-floor', name: 'Rug', point_m: [1, 1, 0], normal_m: [0, 0, 1], size_m: [.4, .4] }];
  const wall = { point: [2.1, 3, 1.45], normal: [0, -1, 0], surfaceName: 'Back wall' };
  const snapped = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: wall, namedAims });
  assert.equal(snapped.title, 'Snaps to “Painting”'); assert.deepEqual(snapped.snap, { id: 'aim-painting', name: 'Painting' });
  assert.deepEqual(snapped.point_m, [2, 3, 1.5]); assert.equal(snapped.surface, 'Painting');
  near(snapped.distance_m, Math.hypot(2, 3, .5));
  // A wall hit facing another way never snaps to the floor aim beside it.
  const side = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, namedAims,
    hit: { point: [1.05, 1, 0.05], normal: [1, 0, 0], surfaceName: 'Cabinet side' } });
  assert.equal(side.snap, null);
  const far = A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([2, 2, 0]), namedAims });
  assert.equal(far.snap, null);
});

test('the proposal target is exactly what the W1 store admits, valid or not', { timeout: DEADLINE_MS.unit }, () => {
  const store = P.createAimProposalStore({ now: () => 1 }), scope = store.scopeFromDescriptor('victoria', descriptor());
  assert.ok(scope);
  const valid = A.proposalTargetFromModel(A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([1, 0, 0]) }));
  assert.deepEqual(valid, { point_m: [1, 0, 0], valid: true, surface: 'Floor', normal_m: [0, 0, 1] });
  assert.equal(store.propose(scope, { origin: 'manual', target: valid }).ok, true);
  const blocked = A.proposalTargetFromModel(A.describeAimHit({ descriptor: descriptor(), limits: VIRTUAL_LIMITS, hit: floor([1, 1, 0]),
    traceBeam: () => ({ point_m: [.5, .5, 1], name: 'Desk chair' }) }));
  assert.equal(blocked.reason, 'blocked');
  const granted = store.propose(scope, { origin: 'manual', target: blocked });
  assert.equal(granted.ok, true); assert.equal(granted.proposal.target.valid, false);
  const reach = A.proposalTargetFromModel(A.describeAimHit({ descriptor: descriptor(), limits: COMMISSIONED, hit: floor([20, 0, 0]) }));
  assert.equal(store.propose(scope, { origin: 'manual', target: reach }).proposal.target.reason, 'out_of_reach');
});

test('the room bar offers Set aim and Discard, and only Discard when it cannot aim there', { timeout: DEADLINE_MS.unit }, () => {
  const bar = target => A.describeProposalBar({ target });
  assert.deepEqual(JSON.parse(JSON.stringify(bar({ point_m: [1, 0, 0], valid: true, surface: 'Painting' }))),
    { state: 'valid', title: 'Painting', actions: ['set', 'discard'] });
  assert.equal(bar({ point_m: [1, 0, 0], valid: true }).title, 'this spot');
  for (const [reason, title] of [['blocked', 'Blocked'], ['out_of_reach', 'Out of reach'], ['no_surface', 'No surface here'], ['not_aimable', 'Can’t aim here']])
    assert.deepEqual(JSON.parse(JSON.stringify(bar({ point_m: [1, 0, 0], valid: false, reason }))), { state: 'blocked', title, actions: ['discard'] });
  assert.equal(A.describeProposalBar(null), null);
});

test('Enter and Escape follow the SPEC ladder: drag first, then the proposal', { timeout: DEADLINE_MS.unit }, () => {
  const valid = { target: { valid: true } }, blocked = { target: { valid: false } };
  const rows = [
    [{ key: 'Escape', dragging: true, proposal: valid }, 'cancel_drag'],
    [{ key: 'Escape', dragging: false, proposal: valid }, 'discard'],
    [{ key: 'Escape', dragging: false, proposal: blocked }, 'discard'],
    [{ key: 'Escape', dragging: false, proposal: null }, null],
    [{ key: 'Enter', dragging: false, proposal: valid }, 'set'],
    [{ key: 'Enter', dragging: false, proposal: blocked }, null],
    [{ key: 'Enter', dragging: true, proposal: valid }, 'release'],
    [{ key: 'Enter', dragging: false, proposal: null }, null],
    [{ key: 'a', dragging: false, proposal: valid }, null],
  ];
  for (const [input, intent] of rows) assert.equal(A.aimKeyIntent(input), intent, JSON.stringify(input));
});

test('labels show for few fixtures, stack instead of overlapping and yield to the pill', { timeout: DEADLINE_MS.unit }, () => {
  const bounds = { left: 0, top: 0, right: 800, bottom: 600 };
  const entries = [{ id: 'a', screen: { x: 100, y: 200 } }, { id: 'b', screen: { x: 130, y: 205 } }, { id: 'c', screen: { x: 900, y: 200 } }];
  const placed = A.placeFixtureLabels(entries, { selectedId: 'a', hoverId: 'b', bounds });
  assert.deepEqual(placed.map(item => [item.id, item.visible, item.state]), [['a', true, 'selected'], ['b', true, 'hover'], ['c', false, 'idle']]);
  assert.equal(placed[0].y, 178); assert.equal(placed[1].y, 158, 'the second label stacks above the first');
  assert.deepEqual(placed[0].ring, { x: 100, y: 200 }); assert.deepEqual(placed[1].ring, { x: 130, y: 205 });
  const pill = A.placeFixtureLabels(entries, { selectedId: 'a', pillFixtureId: 'a', bounds });
  assert.equal(pill[0].visible, false, 'the pill carries the selected name'); assert.deepEqual(pill[0].ring, { x: 100, y: 200 });
  const many = Array.from({ length: 7 }, (_, index) => ({ id: 'f' + index, screen: { x: index * 100 + 20, y: 300 } }));
  const sparse = A.placeFixtureLabels(many, { hoverId: 'f3', bounds });
  assert.deepEqual(sparse.filter(item => item.visible).map(item => item.id), ['f3']);
});

test('the bar stays inside the visible canvas with Chat open or the sheet raised', { timeout: DEADLINE_MS.unit }, () => {
  const full = { left: 0, top: 0, right: 1366, bottom: 768 };
  const chat = { ...full, right: 1366 - 340 }, sheet = { ...full, bottom: 768 / 2 }, phone = { left: 0, top: 0, right: 390, bottom: 844 - 150 };
  for (const bounds of [full, chat, sheet, phone]) for (const x of [-50, 0, 40, 500, 1100, 1400]) for (const y of [-20, 10, 300, 700, 900]) {
    const at = A.placeAimBar({ x, y }, bounds);
    assert.ok(at.x - 110 >= bounds.left && at.x + 110 <= bounds.right, `x ${x} → ${at.x}`);
    assert.ok(at.y >= bounds.top + 40 && at.y <= bounds.bottom - 70, `y ${y} → ${at.y}`);
  }
  assert.deepEqual(A.placeAimBar({ x: 500, y: 300 }, full), { x: 500, y: 338 });
  assert.equal(A.placeAimBar({ x: 1, y: 1 }, { left: 0, top: 0, right: 0, bottom: 10 }), null);
  const chip = A.placeDragChip({ x: 1000, y: 700 }, chat);
  assert.ok(chip.x + 240 <= chat.right && chip.y + 72 <= chat.bottom);
  assert.deepEqual(A.placeDragChip({ x: 100, y: 100 }, full), { x: 118, y: 118 });
});

test('the Zones|Spot pill never covers the 44 px reticle', { timeout: DEADLINE_MS.unit }, () => {
  const reticle = { x: 400, y: 300 }, width = 150, height = 30;
  for (let dx = -200; dx <= 200; dx += 10) for (let dy = -80; dy <= 80; dy += 5) {
    const pill = { x: reticle.x + dx, y: reticle.y + dy, width, height }, at = A.pillClearOfReticle(pill, reticle);
    const overlap = at.x + width / 2 > reticle.x - 22 && at.x - width / 2 < reticle.x + 22 && at.y > reticle.y - 22 && at.y - height < reticle.y + 22;
    assert.equal(overlap, false, `pill at ${dx},${dy}`);
    assert.ok(at.y <= pill.y, 'the pill only ever moves up');
  }
  assert.deepEqual(A.pillClearOfReticle({ x: 10, y: 10, width, height }, null), { x: 10, y: 10 });
});

function fakeHost() {
  const node = () => ({ hidden: false, style: { setProperty() {} }, getAttribute: () => null, setAttribute() {} });
  return { ownerDocument: {}, clientWidth: 800, clientHeight: 600, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }), node };
}
function fakeLayer() {
  const group = new THREE.Group(), calls = { project: 0, pick: 0 };
  return { group, calls, project(point) { calls.project++; return { x: 400 + point[0] * 10, y: 300 - point[1] * 10, visible: true }; },
    pick() { calls.pick++; return { point: [1, 0, 0], normal: [0, 0, 1], surfaceName: 'Floor' }; } };
}

test('the controller refuses missing dependencies and retires on its owner predicate', { timeout: DEADLINE_MS.unit }, () => {
  assert.throws(() => A.createAimPresentation({}), /apartment_aim_presentation_dependencies_required/);
  const layer = fakeLayer(), host = fakeHost();
  let live = true, retired = 0;
  const view = A.createAimPresentation({ layer, host, isCurrent: () => live, onRetire: () => { retired++; } });
  assert.ok(Object.isFrozen(view));
  assert.equal(layer.group.children.length, 1, 'one presentation group under the borrowed layer group');
  const aim = { descriptor: descriptor(), limits: VIRTUAL_LIMITS, namedAims: [] };
  assert.equal(view.sync({ fixtures: [{ fixture_id: 'authored-spot', display_anchor_m: [0, 0, 2.14] }], selectedId: 'authored-spot', aim }), true);
  assert.equal(view.beginDrag({ pointerId: 1, clientX: 10, clientY: 10 }), true);
  const model = view.moveDrag({ pointerId: 1, clientX: 20, clientY: 20 });
  assert.equal(model.valid, true); assert.equal(view.getSnapshot().dragging, true);
  assert.equal(view.moveDrag({ pointerId: 2, clientX: 30, clientY: 30 }), null, 'another pointer is ignored');
  assert.equal(view.endDrag({ pointerId: 1 }), model);
  assert.equal(view.getSnapshot().dragging, false);
  assert.equal(view.beginDrag({ pointerId: 3, clientX: 10, clientY: 10 }), true);
  assert.equal(view.cancelDrag(), true); assert.equal(view.endDrag({ pointerId: 3 }), null, 'a cancelled drag proposes nothing');
  assert.equal(view.beginDrag({ pointerId: 4, clientX: 10, clientY: 10 }), true);
  assert.equal(view.moveDrag({ pointerId: 4, clientX: 13, clientY: 13 }), null, 'under 5 px is still a click');
  assert.equal(view.getSnapshot().model, null);
  assert.equal(view.endDrag({ pointerId: 4 }), null, 'a click on the reticle proposes nothing');
  assert.equal(view.readback().physical_commands, false);
  live = false;
  assert.equal(view.frame(), false);
  assert.equal(retired, 1); assert.equal(layer.group.children.length, 0);
  assert.equal(view.readback().disposed, true);
  assert.equal(view.dispose(), false, 'dispose is idempotent');
  assert.equal(view.sync({}), false); assert.equal(view.beginDrag({ pointerId: 1, clientX: 1, clientY: 1 }), false);
  assert.equal(retired, 1);
});

test('a throwing owner predicate retires rather than revives', { timeout: DEADLINE_MS.unit }, () => {
  let retired = 0;
  const view = A.createAimPresentation({ layer: fakeLayer(), host: fakeHost(), isCurrent: () => { throw Error('authored'); }, onRetire: () => { retired++; } });
  assert.equal(view.frame(), false); assert.equal(retired, 1);
});

test('nothing is wired: no loader, page, worker or host references the new modules', { timeout: DEADLINE_MS.unit }, () => {
  for (const file of ['home-apartment.jsx', 'home-app.jsx', 'home-feature-loader.js', 'index.html', 'home-service-worker.js']) {
    const text = read(file);
    assert.doesNotMatch(text, /home-apartment-room-overlay|apartment-aim-presentation|AptRoomOverlay|AptSpotPendingDot/, file);
  }
});

test('the overlay reads the store and never mutates it; neither module sends, stores or keeps time', { timeout: DEADLINE_MS.unit }, () => {
  const jsx = read('home-apartment-room-overlay.jsx');
  assert.doesNotMatch(jsx, /\b(?:store|\w+Store)\??\.(?:propose|take|discard|restore|retainScopes|clearSite|clearAll)\s*\(/);
  assert.deepEqual([...new Set([...jsx.matchAll(/\bstore\??\.(\w+)/g)].map(match => match[1]))].sort(),
    ['current', 'getSnapshot', 'scopeFromDescriptor', 'subscribe']);
  const forbidden = /\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|localStorage|sessionStorage|indexedDB|setTimeout|setInterval|requestAnimationFrame|Date\.now|performance\.now|postMessage)\b/;
  assert.doesNotMatch(jsx, forbidden);
  assert.doesNotMatch(source, forbidden);
  assert.doesNotMatch(source, /aimAtTarget|restoreAim|changeVirtualFixture|syncVirtualFixturePreview|physical_commands:\s*true/);
  assert.doesNotMatch(jsx, /aimAtTarget|restoreAim|changeVirtualFixture|syncVirtualFixturePreview|HAClient|__hav_haClient/);
});
