#!/usr/bin/env node
// Named aims and Echo's engineered aim on the shared proposal path (APT-A01,
// A05, A07, A10, R04). Authored synthetic data only: an authored Echo room
// whose spotlight is placed so the HANDOFF "Drag aim" example reads exactly,
// an authored narrow-limit Echo profile for "Unreachable", and an authored
// Den adapter. Real proposal store and real aim presentation; no DOM,
// network, timer, household data or hardware. Runs as plain `node`.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const P = require('../app/src/home-apartment-aim-proposals.js');
const N = require('../app/src/home-apartment-named-aims.js');
const E = require('../app/src/home-apartment-echo-aim.js');
const vendor = new URL('../app/src/vendor/three-0.180.0/three.module.js', import.meta.url).href;
const source = fs.readFileSync(new URL('../app/src/home-3d/apartment-aim-presentation.mjs', import.meta.url), 'utf8');
const A = await import('data:text/javascript;base64,' + Buffer.from(source.replace("import * as THREE from 'three';",
  `import * as THREE from ${JSON.stringify(vendor)};`)).toString('base64'));

// Fixed before the first run (2026-10-19); never raised to make a run pass.
const DEADLINE_MS = 5000;

// Authored Echo room (z-up, floor 0, 4 × 4 × 2.5 m). The spotlight sits so
// the painting on the back wall is 1.85 m away at 59° from straight down.
const SPOT = [2, 2.404, 2.453], PAINTING = [2, 3.99, 1.5];
const engineered = (id, limits) => ({ id, type: 'light', name: 'Authored engineered spot', ha_entity_id: null, pos: SPOT,
  fixture_kind: 'engineered_gimbal_v1', aiming_origin: 'fixture_bottom',
  gimbal: { limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90, tilt_positive: 'down', ...limits },
    product_profile_sha256: 'a'.repeat(64), default_target_id: 'authored-floor-spot' } });
const model = (limits = {}) => ({ schema_version: 1, revision: 7, exists: true, zones: [],
  devices: [engineered('authored-echo-spot', limits), { id: 'authored-lamp', type: 'light', name: 'Lamp', pos: [1, 1, 2.3] }],
  targets: [
    { id: 'authored-painting', name: 'Painting', category: 'art', shape: 'surface', pos: PAINTING, normal: [0, -1, 0], up: [0, 0, 1], size_m: [0.9, 0.65], source: 'authored' },
    { id: 'authored-floor-spot', name: 'Floor spot', category: 'custom', shape: 'point', pos: [2, 2, 0], normal: [0, 0, 1], up: [0, 1, 0], size_m: [0, 0], source: 'authored' },
  ] });

// The Simulation Mode runtime's aim surface, authored: it only remembers.
function runtime(initial = { kind: 'named', id: 'authored-floor-spot', name: 'Floor spot', pos: [2, 2, 0] }) {
  const state = { current_destination: initial }, calls = [];
  return { calls, setDestination(id, destination) { calls.push([id, destination]); state.current_destination = { ...destination };
    return { current_destination: { ...destination } }; }, read: () => JSON.parse(JSON.stringify(state)) };
}

function echo({ limits = {}, sim = true } = {}) {
  const store = P.createAimProposalStore({ now: () => 1000 }), sim_ = runtime(), current = model(limits);
  const notices = [], toasts = [], sweeps = [];
  const getContext = id => N.echoAimContext({ model: current, fixtureId: id, runtime: sim_.read(), contentRevision: 'authored-echo-r1', sim });
  const host = E.createEchoAimHost({ store, getContext, runtime: sim_, simulated: () => sim,
    sweep: value => sweeps.push(value), onNotice: value => notices.push(value), onToast: value => toasts.push(value), now: () => 2000 });
  return { store, runtime: sim_, model: current, host, getContext, notices, toasts, sweeps };
}
const hit = (point, normal) => ({ point, normal, surfaceName: 'Scan surface' });

test('Echo context: the engineered light gets an admitted scope, its configured limits and the named aims', { timeout: DEADLINE_MS }, () => {
  const { store, getContext } = echo();
  const context = getContext('authored-echo-spot');
  assert.ok(store.scopeFromDescriptor('echo', context.descriptor), 'the shared store admits the Echo descriptor');
  assert.deepEqual(context.descriptor.target.point, [2, 2, 0], 'current aim from the simulation');
  assert.equal(context.limits.limit_role, 'engineered_profile'); assert.notEqual(context.limits.physical_travel, 'commissioned');
  assert.deepEqual(context.namedAims.map(aim => aim.name), ['Painting', 'Floor spot']);
  assert.equal(getContext('authored-lamp'), null, 'an ordinary light has no aim context');
  assert.equal(context.descriptor.physical_commands, false);
});

test('HANDOFF Drag aim on the Echo painting: Snaps to “Painting” · 1.85 m · 59° tilt; release proposes; Set is simulated', { timeout: DEADLINE_MS }, () => {
  const { store, host, runtime: sim, getContext, toasts, sweeps } = echo();
  const context = getContext('authored-echo-spot');
  // Near the painting, not on its centre.
  const model_ = A.describeAimHit({ descriptor: context.descriptor, limits: context.limits, namedAims: context.namedAims,
    hit: hit([2.12, 3.99, 1.43], [0, -1, 0]) });
  assert.equal(model_.title, 'Snaps to “Painting”');
  assert.equal(model_.metrics, '1.85 m · 59° tilt');
  assert.deepEqual(model_.disclosures, [], 'a configured engineered profile needs neither Victoria disclosure');
  const proposal = host.propose({ fixture_id: 'authored-echo-spot', target: A.proposalTargetFromModel(model_) });
  assert.deepEqual([proposal.origin, proposal.target.valid, proposal.target.surface], ['manual', true, 'Painting']);
  assert.deepEqual(proposal.target.point_m, PAINTING, 'the snap takes the named aim’s own point');
  assert.deepEqual(sim.read().current_destination.pos, [2, 2, 0], 'the warm current aim stays until Set');
  assert.deepEqual(A.describeProposalBar(proposal), { state: 'valid', title: 'Painting', actions: ['set', 'discard'] });
  const result = host.set({ fixture_id: 'authored-echo-spot', proposal });
  assert.equal(result.ok, true);
  assert.deepEqual(sim.read().current_destination, { kind: 'named', id: 'authored-painting', name: 'Painting', pos: PAINTING,
    normal: [0, -1, 0], key: 'named:authored-painting' });
  assert.deepEqual(toasts, ['Aim set · simulated, no hardware command sent']);
  assert.equal(sweeps.length, 1, 'Set plays the sweep');
  assert.equal(store.getSnapshot().proposals.length, 0);
  assert.equal(getContext('authored-echo-spot').current.target_id, 'authored-painting', 'the Aim tab can mark Painting current');
});

test('Unreachable on an authored narrow-limit Echo profile: out of reach with angle and limit; Set unavailable', { timeout: DEADLINE_MS }, () => {
  const { host, store, runtime: sim, getContext } = echo({ limits: { tilt_min_deg: 15 } });
  const context = getContext('authored-echo-spot');
  const steep = A.describeAimHit({ descriptor: context.descriptor, limits: context.limits, namedAims: context.namedAims,
    hit: hit([0, 2.404, 2.1], [1, 0, 0]) });
  assert.equal(steep.valid, false); assert.equal(steep.reason, 'out_of_reach');
  assert.equal(steep.title, 'Out of reach · 80° from straight down, limit 75°');
  assert.equal(steep.note, 'Choose a spot closer to straight below the light.', 'the reason carries a suggestion');
  const proposal = host.propose({ fixture_id: 'authored-echo-spot', target: A.proposalTargetFromModel(steep) });
  assert.deepEqual(A.describeProposalBar(proposal), { state: 'blocked', title: 'Out of reach', actions: ['discard'] });
  assert.deepEqual(host.set({ fixture_id: 'authored-echo-spot', proposal }), { ok: false, reason: 'stale' });
  assert.equal(sim.calls.length, 0, 'nothing moved');
  assert.equal(store.getSnapshot().proposals[0].id, proposal.id, 'the invalid proposal stays for Discard');
  // Full-range Victoria-style limits never say out of reach.
  assert.equal(A.describeAimHit({ descriptor: context.descriptor, limits: { ...context.limits, tilt_min_deg: -90 },
    hit: hit([0, 2.404, 2.1], [1, 0, 0]) }).valid, true);
});

test('Validity reasons carry SPEC copy and a suggestion', { timeout: DEADLINE_MS }, () => {
  const { getContext } = echo();
  const context = getContext('authored-echo-spot');
  const none = A.describeAimHit({ descriptor: context.descriptor, limits: context.limits, hit: null });
  assert.deepEqual([none.title, none.note], ['No surface here', 'Drag onto a wall, furniture or the floor.']);
  const blocked = A.describeAimHit({ descriptor: context.descriptor, limits: context.limits, hit: hit([2, 3.99, 1.5], [0, -1, 0]),
    traceBeam: () => ({ point_m: [2, 3, 2], name: 'Shelf' }) });
  assert.deepEqual([blocked.title, blocked.note], ['Blocked by Shelf', 'Move the spot clear of it, or try another light.']);
});

test('Outside Simulation Mode the Echo light is physical: Set refuses and moves nothing', { timeout: DEADLINE_MS }, () => {
  const { host, runtime: sim, getContext, notices } = echo({ sim: false });
  const context = getContext('authored-echo-spot');
  const proposal = host.recall({ fixture_id: 'authored-echo-spot', aim: context.namedAims[0] });
  assert.equal(proposal.target.valid, true);
  assert.deepEqual(host.set({ fixture_id: 'authored-echo-spot', proposal }), { ok: false, reason: 'not_simulated' });
  assert.equal(sim.calls.length, 0);
  assert.match(notices.at(-1), /simulated only/);
});

test('Discard, undo and redo step per-light {current, proposal}; ⌘Z restores a discarded proposal', { timeout: DEADLINE_MS }, () => {
  const { host, store, runtime: sim, getContext } = echo();
  const id = 'authored-echo-spot', painting = getContext(id).namedAims[0];
  const proposal = host.recall({ fixture_id: id, aim: painting });
  assert.equal(host.discard({ fixture_id: id, proposal }), true);
  assert.equal(store.getSnapshot().proposals.length, 0);
  assert.equal(host.undo(id), true);
  assert.equal(store.getSnapshot().proposals[0].id, proposal.id, '⌘Z restores the same proposal');
  assert.equal(host.set({ fixture_id: id, proposal }).ok, true);
  assert.deepEqual(sim.read().current_destination.pos, PAINTING);
  assert.equal(host.undo(id), true, 'undo the Set');
  assert.deepEqual(sim.read().current_destination.pos, [2, 2, 0]);
  assert.equal(store.getSnapshot().proposals[0].target.surface, 'Painting', 'the proposal comes back with the old aim');
  assert.equal(host.redo(id), true);
  assert.deepEqual(sim.read().current_destination.pos, PAINTING);
  for (let index = 0; index < 40; index++) host.recall({ fixture_id: id, aim: painting });
  assert.equal(host.historyLength('undo', id), E.HISTORY_STEPS, '30 steps per light');
});

test('Save as named aim: a targets[] record from the proposal; name conflict offers Replace existing or Keep both', { timeout: DEADLINE_MS }, () => {
  const { host, getContext, model: current } = echo();
  const id = 'authored-echo-spot';
  const proposal = host.propose({ fixture_id: id, target: { point_m: [1.5, 3.99, 1.2], valid: true, surface: 'Back wall', normal_m: [0, -1, 0] } });
  const basis = N.aimBasis({ contentRevision: 'authored-echo-r1', fixture: getContext(id).fixture });
  const built = N.namedAimRecord({ proposal, name: 'Reading', basis, nowMs: Date.UTC(2026, 9, 19) });
  assert.equal(built.ok, true);
  assert.deepEqual({ ...built.record, id: null, updated_at: null }, { id: null, name: 'Reading', category: 'custom', shape: 'point',
    pos: [1.5, 3.99, 1.2], normal: [0, -1, 0], up: [0, 0, 1], size_m: [0, 0], room_id: null, surface: 'Back wall', source: 'aimed',
    confidence: 1, updated_at: null, aim_basis: { scene: 'authored-echo-r1', collision: null, fixture_id: id, profile: 'a'.repeat(64) } });
  const saved = N.applyNamedAimSave(current, built.record);
  assert.equal(saved.ok, true); assert.equal(saved.model.targets.length, 3);
  assert.equal(current.targets.length, 2, 'the input model is not mutated');
  // An existing name is a conflict until the person chooses.
  const again = N.namedAimRecord({ proposal, name: 'painting', nowMs: Date.UTC(2026, 9, 19, 1) }).record;
  const conflict = N.applyNamedAimSave(current, again);
  assert.deepEqual([conflict.ok, conflict.conflict, conflict.existing.id], [false, true, 'authored-painting']);
  const replaced = N.applyNamedAimSave(current, again, 'replace');
  assert.equal(replaced.target_id, 'authored-painting', 'Replace existing keeps the id, so references survive');
  const painting = replaced.model.targets.find(target => target.id === 'authored-painting');
  assert.deepEqual([painting.name, painting.pos, painting.source], ['Painting', [1.5, 3.99, 1.2], 'aimed']);
  const both = N.applyNamedAimSave(current, again, 'keep_both');
  assert.deepEqual(both.model.targets.map(target => target.name), ['Painting', 'Floor spot', 'painting 2']);
  assert.equal(N.namedAimRecord({ proposal: { target: { valid: false, reason: 'blocked', point_m: [0, 0, 0] } }, name: 'x', nowMs: 1 }).ok, false,
    'an invalid proposal cannot be saved');
});

test('Per-aim states use SPEC copy: saving → saved, local, conflict, not saved, changed with Preview it / Keep as is', { timeout: DEADLINE_MS }, () => {
  const target = { id: 'target-reading-1', name: 'Reading', pos: [1, 1, 1],
    aim_basis: { scene: 'authored-echo-r1', collision: null, fixture_id: 'authored-echo-spot', profile: 'a'.repeat(64) } };
  const pending = { target_id: target.id, name: 'Reading' };
  const state = save => N.namedAimState(target, { pending, save });
  assert.deepEqual([state({ state: 'saving' }).state, state({ state: 'saving' }).badge], ['saving', 'Saving']);
  assert.deepEqual([state({ state: 'saved' }).state, state({ state: 'saved' }).badge], ['saved', 'Saved']);
  assert.deepEqual([state({ state: 'offline' }).badge, state({ state: 'offline' }).flag], ['Saved locally', '“Reading” is saved in this browser only.']);
  assert.equal(state({ state: 'simulation' }).state, 'local');
  const revision = state({ state: 'conflict' });
  assert.equal(revision.state, 'not_saved'); assert.match(revision.flag, /nothing was overwritten and your draft is kept/);
  const named = N.namedAimState(target, { pending: { ...pending, conflict: true } });
  assert.deepEqual([named.state, named.badge, named.flag, named.actions], ['conflict', 'Conflict', '“Reading” already exists.', ['replace', 'keep_both']]);
  assert.deepEqual(N.COPY.action.replace, 'Replace existing'); assert.deepEqual(N.COPY.action.keepBoth, 'Keep both');
  const scan = N.namedAimState(target, { basis: { scene: 'authored-echo-r2', collision: null, fixture_id: 'authored-echo-spot', profile: 'a'.repeat(64) } });
  assert.deepEqual([scan.state, scan.badge, scan.flag, scan.actions], ['changed', 'Check point', '“Reading” predates the latest scan.', ['preview', 'keep']]);
  assert.deepEqual([N.COPY.action.preview, N.COPY.action.keep], ['Preview it', 'Keep as is']);
  const profile = N.namedAimState(target, { basis: { scene: 'authored-echo-r1', collision: null, fixture_id: 'authored-echo-spot', profile: 'b'.repeat(64) } });
  assert.equal(profile.flag, '“Reading” predates the light\'s profile change.');
  assert.equal(N.namedAimState(target, { basis: { scene: 'authored-echo-r2' }, kept: [target.id] }).state, 'saved', 'Keep as is clears the flag');
  assert.equal(N.namedAimState({ id: 'authored', name: 'Old' }, { basis: { scene: 'authored-echo-r2' } }).state, 'saved',
    'aims without a recorded basis are never guessed changed');
});

test('A10: the automation reference is the stable target id and survives rename; which automations use it stays unavailable', { timeout: DEADLINE_MS }, () => {
  const current = model(), painting = current.targets[0];
  const renamed = { ...current, targets: current.targets.map(target => target.id === painting.id ? { ...target, name: 'Gallery wall' } : target) };
  assert.equal(N.automationReference(renamed.targets[0]), N.automationReference(painting));
  assert.equal(N.automationReference(painting), 'authored-painting');
  assert.equal(current.devices[0].gimbal.default_target_id, 'authored-floor-spot', 'fixture references are by id');
  assert.equal(N.proposedFixtureReference('authored-echo-spot', 'authored-painting'), 'aim authored-echo-spot → authored-painting');
  assert.match(N.COPY.inspector.automationProposed, /proposed/);
  assert.match(N.COPY.inspector.automationUsers, /isn’t available/);
});

test('R04 users and reach: Echo reach from its profile; Victoria says travel limits not commissioned', { timeout: DEADLINE_MS }, () => {
  const current = model({ tilt_min_deg: 15 }), [spot] = current.devices;
  assert.deepEqual(N.reachOfAim(current.targets[0], spot, { siteId: 'echo' }).state, 'reachable');
  assert.equal(N.reachOfAim({ pos: [0, 2.404, 2.1] }, spot, { siteId: 'echo' }).text, 'Out of reach · 80° from straight down, limit 75°');
  assert.equal(N.reachOfAim(current.targets[0], spot, { siteId: 'victoria' }).text, 'travel limits not commissioned');
  assert.equal(N.reachOfAim(current.targets[0], current.devices[1], { siteId: 'echo' }).state, 'not_aimable');
  const users = N.lightsUsingAim('authored-floor-spot', current.devices, fixture => fixture.gimbal ? { target_id: fixture.gimbal.default_target_id } : null);
  assert.deepEqual(users.map(fixture => fixture.id), ['authored-echo-spot']);
});

test('Den named aims (A07 default) on the authored adapter: bound to the registration revision, desk read only, no evidence', { timeout: DEADLINE_MS }, async () => {
  const den = N.createAuthoredDenNamedAimStore({ registrationRevision: 'authored-registration-r2', now: () => 5000,
    configuredDesk: { id: 'authored-desk', name: 'Desk', point_m: [1, 1, 0.75], normal_m: [0, 0, 1] } });
  const view = den.view('authored-registration-r2');
  assert.deepEqual(view.aims.map(aim => [aim.name, aim.read_only, aim.source]), [['Desk', true, 'configured_desk']]);
  assert.ok(Object.isFrozen(view.aims[0]) && Object.isFrozen(view.aims[0].point_m), 'the desk cannot be edited through the adapter');
  const pending = den.save({ name: 'Reading chair', point_m: [2, 1, 0.9], fixture_id: 'authored-den-spot' }, { currentRevision: 'authored-registration-r2' });
  assert.ok(pending instanceof Promise, 'saving is asynchronous, so Saving shows first');
  assert.equal((await pending).state, 'saved');
  const desk = await den.save({ name: 'desk', point_m: [2, 2, 0.75] }, { currentRevision: 'authored-registration-r2' });
  assert.deepEqual([desk.conflict, desk.read_only], [true, true]);
  assert.equal((await den.save({ name: 'desk', point_m: [2, 2, 0.75] }, { currentRevision: 'authored-registration-r2', resolution: 'replace' })).reason,
    N.COPY.den.deskReadOnly, 'Replace never rewrites the configured desk');
  assert.equal((await den.save({ name: 'desk', point_m: [2, 2, 0.75] }, { currentRevision: 'authored-registration-r2', resolution: 'keep_both' })).aim.name, 'desk 2');
  const recall = den.recall('authored-desk', { currentRevision: 'authored-registration-r2' });
  assert.deepEqual([recall.ok, recall.evidence], [true, null], 'recall is only an aim reference');
  // A new registration revision: names become unavailable, never re-projected.
  const moved = den.view('authored-registration-r3');
  assert.deepEqual([moved.available, moved.aims.length, moved.reason], [false, 0, N.COPY.den.revisionChanged]);
  assert.equal((await den.save({ name: 'Late', point_m: [1, 1, 1] }, { currentRevision: 'authored-registration-r3' })).ok, false);
  assert.equal(den.recall('authored-desk', { currentRevision: 'authored-registration-r3' }).ok, false);
  assert.deepEqual(den.writes(), { named_aims: 2, desk: 0, candidate: 0, registration: 0, evidence: 0 });
  // The production binding until Den's contract: no aims and a reason.
  const live = N.denNamedAimsUnavailable();
  assert.deepEqual([live.view().available, live.view().reason], [false, N.COPY.den.unavailable]);
  assert.equal((await live.save({ name: 'x', point_m: [0, 0, 0] })).ok, false);
});
