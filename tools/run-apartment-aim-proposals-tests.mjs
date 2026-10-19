#!/usr/bin/env node
// Unwired aim proposal store. Authored synthetic data only: no real homes,
// coordinates or digests. Runs as plain `node`; node:test sets the exit code.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const MODULE = new URL('../app/src/home-apartment-aim-proposals.js', import.meta.url);
const P = require('../app/src/home-apartment-aim-proposals.js');
const STORE_KEYS = ['clearAll','clearSite','current','discard','getSnapshot','propose','restore','retainScopes','scopeFromDescriptor','subscribe','take'];

const hex = character => character.repeat(64);
const binding = () => ({ content_revision: 'authored-content-1', registration_version: 2,
  manifest_digest: hex('a'), frame_digest: hex('b'), canonical_collision_digest: hex('c') });
const descriptor = (fixture = 'fixture-alpha', extra = {}) => ({ schema: 'home-virtual-fixture-interaction-1', generation: 4,
  fixture_id: fixture, mount_id: 'mount-' + fixture, binding: binding(), equipment_revision: 3, equipment_digest: hex('d'),
  display_anchor_m: [0, 0, 0], state: { pan_deg: 0, tilt_deg: 0 }, target: null, truth_role: 'virtual_preview', physical_commands: false, ...extra });
const target = (extra = {}) => ({ point_m: [0.5, 0.25, 0], valid: true, ...extra });
const ticking = (start = 1000) => { let at = start; return () => at++; };
const store = (now = ticking()) => P.createAimProposalStore({ now });
const scopeOf = (s, site = 'echo', fixture = 'fixture-alpha') => s.scopeFromDescriptor(site, descriptor(fixture));
const moved = (scope, change) => { const copy = structuredClone(scope); change(copy); return copy; };
const manual = (extra) => ({ origin: 'manual', target: target(extra) });
const deepFrozen = value => !value || typeof value !== 'object' || Object.isFrozen(value) && Object.values(value).every(deepFrozen);
const deepFreeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
const trap = () => { throw Error('authored trap'); };
const label = value => { try { return JSON.stringify(value) ?? String(value); } catch (_) { return 'hostile value'; } };
const hostile = () => new Proxy({}, { get: trap, has: trap, ownKeys: trap, getOwnPropertyDescriptor: trap, getPrototypeOf: trap });
function watched(s) {
  const seen = { calls: 0 };
  s.subscribe(() => { seen.calls++; });
  return seen;
}
// Each fence field, changed alone, must retire a proposal.
const fenceChanges = [
  ['generation', s => { s.generation++; }],
  ['equipment_revision', s => { s.equipment_revision++; }],
  ['equipment_digest', s => { s.equipment_digest = hex('e'); }],
  ['mount_id', s => { s.mount_id = 'mount-other'; }],
  ['binding.content_revision', s => { s.binding.content_revision = 'authored-content-2'; }],
  ['binding.registration_version', s => { s.binding.registration_version++; }],
  ['binding.manifest_digest', s => { s.binding.manifest_digest = hex('e'); }],
  ['binding.frame_digest', s => { s.binding.frame_digest = hex('e'); }],
  ['binding.canonical_collision_digest', s => { s.binding.canonical_collision_digest = hex('e'); }],
];

test('scopeFromDescriptor copies the descriptor fence, ignores extras and never mutates input', () => {
  const s = store();
  const input = descriptor('fixture-alpha', { binding: { ...binding(), authored_note: 'ignored' } }), before = structuredClone(input);
  const scope = s.scopeFromDescriptor('echo', input);
  assert.deepEqual(scope, { site_id: 'echo', fixture_id: 'fixture-alpha', mount_id: 'mount-fixture-alpha', generation: 4,
    equipment_revision: 3, equipment_digest: hex('d'), binding: binding() });
  assert.deepEqual(Object.keys(scope), ['site_id','fixture_id','mount_id','generation','equipment_revision','equipment_digest','binding']);
  assert.deepEqual(Object.keys(scope.binding), Object.keys(binding()));
  assert.ok(deepFrozen(scope)); assert.deepEqual(input, before); assert.notEqual(scope.binding, input.binding);
  input.generation = 9; input.binding.frame_digest = hex('e');
  assert.equal(scope.generation, 4); assert.equal(scope.binding.frame_digest, hex('b'));
  assert.deepEqual(s.scopeFromDescriptor('echo', deepFreeze(descriptor())), scopeOf(s));
  assert.deepEqual(s.scopeFromDescriptor('den', descriptor()).site_id, 'den');
  const unsaved = s.scopeFromDescriptor('echo', descriptor('fixture-alpha', { equipment_revision: 0, equipment_digest: null }));
  assert.equal(unsaved.equipment_revision, 0); assert.equal(unsaved.equipment_digest, null);
  assert.equal(s.scopeFromDescriptor('s'.repeat(160), descriptor('f'.repeat(160), { mount_id: 'm'.repeat(160) })).fixture_id, 'f'.repeat(160));
  assert.equal(s.getSnapshot().revision, 0);
});

const descriptorRejections = [
  ['schema missing', d => { delete d.schema; }], ['schema wrong', d => { d.schema = 'home-virtual-fixture-interaction-2'; }],
  ['schema not a string', d => { d.schema = 1; }],
];
for (const key of ['fixture_id', 'mount_id']) descriptorRejections.push(
  [key + ' missing', d => { delete d[key]; }], [key + ' empty', d => { d[key] = ''; }],
  [key + ' too long', d => { d[key] = 'x'.repeat(161); }], [key + ' number', d => { d[key] = 7; }], [key + ' null', d => { d[key] = null; }]);
for (const [name, value] of [['negative', -1], ['fractional', 1.5], ['string', '4'], ['NaN', NaN], ['infinite', Infinity], ['unsafe', 2 ** 53], ['null', null]])
  descriptorRejections.push(['generation ' + name, d => { d.generation = value; }]);
descriptorRejections.push(['generation missing', d => { delete d.generation; }]);
for (const [name, value] of [['negative', -1], ['fractional', 1.5], ['string', '3'], ['null', null], ['NaN', NaN]])
  descriptorRejections.push(['equipment_revision ' + name, d => { d.equipment_revision = value; }]);
descriptorRejections.push(['equipment_revision missing', d => { delete d.equipment_revision; }],
  ['equipment_digest missing', d => { delete d.equipment_digest; }], ['equipment_digest uppercase', d => { d.equipment_digest = hex('D'); }],
  ['equipment_digest short', d => { d.equipment_digest = 'd'.repeat(63); }], ['equipment_digest long', d => { d.equipment_digest = 'd'.repeat(65); }],
  ['equipment_digest number', d => { d.equipment_digest = 1; }], ['equipment_digest null after a saved revision', d => { d.equipment_digest = null; }],
  ['binding missing', d => { delete d.binding; }], ['binding null', d => { d.binding = null; }],
  ['binding array', d => { d.binding = Object.values(binding()); }], ['binding string', d => { d.binding = 'binding'; }],
  ['binding.content_revision missing', d => { delete d.binding.content_revision; }], ['binding.content_revision empty', d => { d.binding.content_revision = ''; }],
  ['binding.content_revision too long', d => { d.binding.content_revision = 'x'.repeat(161); }], ['binding.content_revision number', d => { d.binding.content_revision = 1; }],
  ['binding.registration_version missing', d => { delete d.binding.registration_version; }],
  ['binding.registration_version negative', d => { d.binding.registration_version = -1; }],
  ['binding.registration_version string', d => { d.binding.registration_version = '2'; }],
  ['binding.registration_version fractional', d => { d.binding.registration_version = 1.5; }]);
for (const key of ['manifest_digest', 'frame_digest', 'canonical_collision_digest']) descriptorRejections.push(
  ['binding.' + key + ' missing', d => { delete d.binding[key]; }], ['binding.' + key + ' uppercase', d => { d.binding[key] = hex('A'); }],
  ['binding.' + key + ' short', d => { d.binding[key] = 'a'.repeat(63); }], ['binding.' + key + ' null', d => { d.binding[key] = null; }]);
test('scopeFromDescriptor refuses every missing or malformed fence field', () => {
  const s = store();
  for (const [name, change] of descriptorRejections) {
    const input = descriptor(); change(input); const before = structuredClone(input);
    assert.equal(s.scopeFromDescriptor('echo', input), null, name);
    assert.deepEqual(input, before, name + ' left input unchanged');
  }
  for (const site of [undefined, null, '', 'x'.repeat(161), 7, ['echo'], {}]) assert.equal(s.scopeFromDescriptor(site, descriptor()), null, String(site));
  for (const value of [undefined, null, 'descriptor', 42, [descriptor()]]) assert.equal(s.scopeFromDescriptor('echo', value), null, String(value));
  assert.equal(s.scopeFromDescriptor('echo', hostile()), null);
  assert.equal(s.scopeFromDescriptor('echo', Object.defineProperty(descriptor(), 'binding', { get: trap, enumerable: true })), null);
});

for (const origin of ['manual', 'chat', 'perception']) test(`propose admits a ${origin} proposal as a deep-frozen record`, () => {
  const s = store(ticking(5000)), scope = scopeOf(s), request = { origin, target: target() }, before = structuredClone(request);
  const result = s.propose(scope, request);
  assert.equal(result.ok, true); assert.ok(Object.isFrozen(result));
  assert.deepEqual(result.proposal, { id: 'aim-proposal-1', sequence: 1, site_id: 'echo', fixture_id: 'fixture-alpha', scope,
    origin, target: { point_m: [0.5, 0.25, 0], valid: true }, created_at_ms: 5000 });
  assert.deepEqual(Object.keys(result.proposal), ['id','sequence','site_id','fixture_id','scope','origin','target','created_at_ms']);
  assert.ok(deepFrozen(result.proposal)); assert.deepEqual(request, before);
  request.target.point_m[0] = 9;
  assert.equal(result.proposal.target.point_m[0], 0.5);
  assert.equal(s.current(scope), result.proposal);
});

test('preview, desk preview and unknown origins are refused without a change', () => {
  const s = store(), scope = scopeOf(s, 'den'), seen = watched(s), snapshot = s.getSnapshot();
  for (const origin of ['preview', 'desk_preview', undefined, null, '', 'Manual', 'MANUAL', 'system', 'agent', 42, {}, ['manual']])
    assert.deepEqual(s.propose(scope, { origin, target: target() }), { ok: false, reason: 'origin_refused' }, String(origin));
  for (const request of [undefined, null, 'manual', 1, hostile(), { get origin() { throw Error('authored trap'); }, target: target() }])
    assert.equal(s.propose(scope, request).reason, 'origin_refused');
  assert.equal(s.getSnapshot(), snapshot); assert.equal(seen.calls, 0); assert.equal(s.current(scope), null);
  assert.equal(s.propose(scope, manual()).proposal.id, 'aim-proposal-1');
});

test('target validation admits the authored shape and copies only its fields', () => {
  const accepted = [
    [target(), { point_m: [0.5, 0.25, 0], valid: true }],
    [target({ surface: 'authored-wall', normal_m: [0, -1, 0] }), { point_m: [0.5, 0.25, 0], valid: true, surface: 'authored-wall', normal_m: [0, -1, 0] }],
    [target({ reason: undefined, surface: undefined, normal_m: undefined }), { point_m: [0.5, 0.25, 0], valid: true }],
    [target({ surface: 's'.repeat(160) }), { point_m: [0.5, 0.25, 0], valid: true, surface: 's'.repeat(160) }],
    [target({ point_m: [-1e3, 0, 1e-9], normal_m: [0, 0, 0] }), { point_m: [-1e3, 0, 1e-9], valid: true, normal_m: [0, 0, 0] }],
    ...['blocked', 'out_of_reach', 'no_surface', 'not_aimable'].map(reason =>
      [target({ valid: false, reason }), { point_m: [0.5, 0.25, 0], valid: false, reason }]),
    [target({ valid: false, reason: 'blocked', surface: 'authored-shelf', normal_m: [1, 0, 0] }),
      { point_m: [0.5, 0.25, 0], valid: false, reason: 'blocked', surface: 'authored-shelf', normal_m: [1, 0, 0] }],
  ];
  for (const [input, expected] of accepted) {
    const s = store(), before = structuredClone(input), result = s.propose(scopeOf(s), { origin: 'chat', target: input });
    assert.equal(result.ok, true, label(input));
    assert.deepEqual(result.proposal.target, expected); assert.deepEqual(Object.keys(result.proposal.target), Object.keys(expected));
    assert.ok(deepFrozen(result.proposal.target)); assert.deepEqual(input, before);
    assert.notEqual(result.proposal.target.point_m, input.point_m);
  }
});

test('target validation refuses every malformed target', () => {
  const sparse = [, 0, 0];
  const refused = [undefined, null, 'target', 3, [], [0.5, 0.25, 0], hostile(),
    { valid: true }, target({ point_m: [0, 0] }), target({ point_m: [0, 0, 0, 0] }), target({ point_m: [NaN, 0, 0] }),
    target({ point_m: [Infinity, 0, 0] }), target({ point_m: ['0', 0, 0] }), target({ point_m: 'xyz' }), target({ point_m: sparse }),
    target({ point_m: { 0: 0, 1: 0, 2: 0, length: 3 } }), target({ point_m: null }),
    { point_m: [0, 0, 0] }, target({ valid: 'true' }), target({ valid: 1 }), target({ valid: null }),
    target({ valid: false }), target({ valid: false, reason: 'unknown' }), target({ valid: false, reason: 'Blocked' }),
    target({ valid: false, reason: null }), target({ valid: false, reason: '' }),
    target({ reason: 'blocked' }), target({ reason: null }), target({ reason: '' }),
    target({ surface: '' }), target({ surface: 's'.repeat(161) }), target({ surface: 7 }), target({ surface: null }),
    target({ normal_m: [0, 1] }), target({ normal_m: [0, NaN, 1] }), target({ normal_m: 'up' }), target({ normal_m: null }),
    target({ kind: 'registered_mesh' }), target({ fixture_id: 'fixture-alpha' }),
    { point_m: [0, 0, 0], get valid() { throw Error('authored trap'); } }];
  const s = store(), scope = scopeOf(s), seen = watched(s), snapshot = s.getSnapshot();
  for (const value of refused) assert.deepEqual(s.propose(scope, { origin: 'manual', target: value }), { ok: false, reason: 'invalid_target' }, label(value));
  assert.equal(s.propose(scope, { origin: 'manual', get target() { throw Error('authored trap'); } }).reason, 'invalid_target');
  assert.equal(s.getSnapshot(), snapshot); assert.equal(seen.calls, 0);
});

test('propose accepts only a scope this store could produce', () => {
  const s = store(), scope = scopeOf(s), seen = watched(s);
  const invalid = [undefined, null, {}, 'scope', [scope], hostile(), descriptor(), { ...scope, extra: 1 },
    moved(scope, c => { delete c.mount_id; }), moved(scope, c => { c.binding.extra = 1; }), moved(scope, c => { delete c.binding.frame_digest; }),
    moved(scope, c => { c.binding.frame_digest = hex('F'); }), moved(scope, c => { c.site_id = ''; }), moved(scope, c => { c.generation = -1; }),
    moved(scope, c => { c.equipment_revision = '3'; }), moved(scope, c => { c.binding = null; }),
    { ...scope, get generation() { throw Error('authored trap'); } }];
  for (const value of invalid) assert.deepEqual(s.propose(value, manual()), { ok: false, reason: 'invalid_scope' });
  assert.equal(seen.calls, 0);
  const clone = structuredClone(scope), result = s.propose(clone, manual());
  assert.equal(result.ok, true); assert.deepEqual(result.proposal.scope, scope); assert.notEqual(result.proposal.scope, clone);
  clone.generation = 99; assert.equal(result.proposal.scope.generation, 4); assert.equal(s.current(scope), result.proposal);
});

test('a newer proposal replaces the pending one for that fixture outright', () => {
  const s = store(), scope = scopeOf(s), first = s.propose(scope, manual()).proposal;
  const second = s.propose(scope, { origin: 'perception', target: target({ valid: false, reason: 'out_of_reach' }) }).proposal;
  assert.equal(second.id, 'aim-proposal-2'); assert.equal(second.sequence, 2);
  assert.deepEqual(s.getSnapshot().proposals, [second]); assert.equal(s.current(scope), second);
  assert.equal(s.take(scope, first.id).reason, 'not_pending');
  assert.equal(s.restore(scope, first).reason, 'invalid_proposal', 'a replaced proposal is gone, not discarded');
  const third = s.propose(moved(scope, c => { c.generation++; }), manual()).proposal;
  assert.deepEqual(s.getSnapshot().proposals, [third], 'a fence change still occupies the same fixture slot');
});

test('proposals are isolated per site and per fixture', () => {
  const s = store(), echoAlpha = scopeOf(s), echoBeta = scopeOf(s, 'echo', 'fixture-beta'), denAlpha = scopeOf(s, 'den');
  const a = s.propose(echoAlpha, manual()).proposal, b = s.propose(echoBeta, manual()).proposal, d = s.propose(denAlpha, manual()).proposal;
  assert.deepEqual(s.getSnapshot().proposals, [d, a, b]);
  assert.equal(s.current(echoAlpha), a); assert.equal(s.current(echoBeta), b); assert.equal(s.current(denAlpha), d);
  assert.equal(s.take(denAlpha, a.id).reason, 'not_pending'); assert.equal(s.take(echoBeta, a.id).reason, 'not_pending');
  assert.equal(s.take(echoAlpha, a.id).proposal, a);
  assert.equal(s.current(echoBeta), b); assert.equal(s.current(denAlpha), d); assert.equal(s.current(echoAlpha), null);
  const other = store(), mine = other.propose(echoAlpha, manual()).proposal;
  assert.equal(mine.id, 'aim-proposal-1', 'each store keeps its own counter'); assert.equal(s.current(echoAlpha), null);
});

test('current returns a proposal only for its exact fence', () => {
  const s = store(), scope = scopeOf(s), proposal = s.propose(scope, manual()).proposal, snapshot = s.getSnapshot();
  for (const [name, change] of fenceChanges) assert.equal(s.current(moved(scope, change)), null, name);
  assert.equal(s.current(moved(scope, c => { c.site_id = 'den'; })), null);
  assert.equal(s.current(scopeOf(s, 'echo', 'fixture-beta')), null);
  for (const value of [undefined, null, {}, { ...scope, extra: 1 }, hostile()]) assert.equal(s.current(value), null);
  assert.equal(s.current(scope), proposal); assert.equal(s.current(structuredClone(scope)), proposal);
  assert.equal(s.getSnapshot(), snapshot, 'reads never change the store');
});

test('take is the Set path: once only, exact fence, right id', () => {
  const s = store(), scope = scopeOf(s), proposal = s.propose(scope, manual()).proposal, seen = watched(s);
  const snapshot = s.getSnapshot();
  for (const id of ['aim-proposal-99', undefined, null, 1, proposal]) assert.deepEqual(s.take(scope, id), { ok: false, reason: 'not_pending' });
  for (const value of [null, {}, hostile()]) assert.deepEqual(s.take(value, proposal.id), { ok: false, reason: 'invalid_scope' });
  assert.equal(s.getSnapshot(), snapshot); assert.equal(seen.calls, 0); assert.equal(s.current(scope), proposal);
  const taken = s.take(scope, proposal.id);
  assert.equal(taken.ok, true); assert.equal(taken.proposal, proposal); assert.ok(Object.isFrozen(taken));
  assert.equal(seen.calls, 1); assert.equal(s.current(scope), null); assert.deepEqual(s.getSnapshot().proposals, []);
  assert.deepEqual(s.take(scope, proposal.id), { ok: false, reason: 'not_pending' }); assert.equal(seen.calls, 1);
  assert.equal(s.restore(scope, proposal).reason, 'invalid_proposal', 'a Set proposal is not discarded');
});

test('take and discard against a moved fence refuse and drop the stale proposal', () => {
  for (const method of ['take', 'discard']) for (const [name, change] of fenceChanges) {
    const s = store(), scope = scopeOf(s), proposal = s.propose(scope, manual()).proposal, seen = watched(s), revision = s.getSnapshot().revision;
    const live = moved(scope, change);
    assert.deepEqual(s[method](live, proposal.id), { ok: false, reason: 'stale_scope' }, `${method} ${name}`);
    assert.equal(seen.calls, 1); assert.equal(s.getSnapshot().revision, revision + 1); assert.deepEqual(s.getSnapshot().proposals, []);
    assert.equal(s.current(scope), null);
    assert.equal(s.take(scope, proposal.id).reason, 'not_pending'); assert.equal(s.take(live, proposal.id).reason, 'not_pending');
    assert.equal(s.restore(scope, proposal).reason, 'invalid_proposal', 'a stale proposal can never return');
  }
});

test('discard and restore round trip the same record once per discard', () => {
  const s = store(), scope = scopeOf(s), proposal = s.propose(scope, { origin: 'chat', target: target({ surface: 'authored-wall' }) }).proposal;
  const discarded = s.discard(scope, proposal.id);
  assert.equal(discarded.ok, true); assert.equal(discarded.proposal, proposal); assert.equal(s.current(scope), null);
  assert.deepEqual(s.discard(scope, proposal.id), { ok: false, reason: 'not_pending' });
  const restored = s.restore(scope, proposal);
  assert.equal(restored.ok, true); assert.equal(restored.proposal, proposal); assert.equal(s.current(scope), proposal);
  assert.deepEqual(s.getSnapshot().proposals, [proposal]);
  assert.deepEqual(s.restore(scope, proposal), { ok: false, reason: 'invalid_proposal' }, 'already restored');
  assert.equal(s.discard(scope, proposal.id).ok, true);
  const again = s.restore(structuredClone(scope), structuredClone(proposal));
  assert.equal(again.ok, true, 'an exact copy is accepted'); assert.equal(again.proposal, proposal, 'the stored original returns');
  assert.equal(s.take(scope, proposal.id).proposal, proposal);
  assert.equal(s.propose(scope, manual()).proposal.id, 'aim-proposal-2', 'restore never consumes a sequence');
});

test('restore refuses tampered, foreign, never-discarded, stale and occupied records', () => {
  const s = store(), scope = scopeOf(s), proposal = s.propose(scope, manual()).proposal;
  assert.equal(s.restore(scope, proposal).reason, 'invalid_proposal', 'a pending record was never discarded');
  s.discard(scope, proposal.id);
  const copy = () => structuredClone(proposal);
  const tampered = [
    { ...copy(), origin: 'chat' }, { ...copy(), id: 'aim-proposal-2' }, { ...copy(), sequence: 2 }, { ...copy(), created_at_ms: 1 },
    { ...copy(), site_id: 'den' }, { ...copy(), fixture_id: 'fixture-beta' }, { ...copy(), extra: true },
    (({ created_at_ms, ...rest }) => rest)(copy()), { ...copy(), target: target({ point_m: [0.5, 0.25, 1] }) },
    { ...copy(), target: target({ surface: 'authored-wall' }) }, { ...copy(), scope: moved(scope, c => { c.generation++; }) },
    { ...copy(), scope: moved(scope, c => { c.binding.frame_digest = hex('e'); }) },
    { id: proposal.id }, null, undefined, proposal.id, 1, hostile()];
  for (const value of tampered) assert.deepEqual(s.restore(scope, value), { ok: false, reason: 'invalid_proposal' }, label(value));
  assert.deepEqual(s.restore(null, proposal), { ok: false, reason: 'invalid_scope' });
  const foreign = store(), foreignScope = scopeOf(foreign, 'echo', 'fixture-beta'), theirs = foreign.propose(foreignScope, manual()).proposal;
  foreign.discard(foreignScope, theirs.id);
  assert.equal(s.restore(foreignScope, theirs).reason, 'invalid_proposal', 'issued by another store');
  assert.equal(s.restore(scope, { ...copy(), id: 'aim-proposal-99', sequence: 99 }).reason, 'invalid_proposal', 'never issued');
  for (const [name, change] of fenceChanges) assert.deepEqual(s.restore(moved(scope, change), proposal), { ok: false, reason: 'stale_scope' }, name);
  const newer = s.propose(scope, manual()).proposal;
  assert.deepEqual(s.restore(scope, proposal), { ok: false, reason: 'occupied' }); assert.equal(s.current(scope), newer);
  s.take(scope, newer.id);
  assert.equal(s.restore(scope, proposal).proposal, proposal, 'refusals leave the discarded record restorable');
});

test('discard history is bounded oldest first', () => {
  const s = store(), scope = scopeOf(s), records = [];
  for (let i = 0; i < 101; i++) { const proposal = s.propose(scope, manual()).proposal; s.discard(scope, proposal.id); records.push(proposal); }
  assert.equal(s.restore(scope, records[0]).reason, 'invalid_proposal');
  assert.equal(s.restore(scope, records[1]).proposal, records[1]);
  s.discard(scope, records[1].id);
  assert.equal(s.restore(scope, records[100]).proposal, records[100]);
});

test('retainScopes drops only that site\'s proposals outside the live fences', () => {
  const s = store(), alpha = scopeOf(s), beta = scopeOf(s, 'echo', 'fixture-beta'), gamma = scopeOf(s, 'echo', 'fixture-gamma'), den = scopeOf(s, 'den');
  const kept = s.propose(alpha, manual()).proposal; s.propose(beta, manual()); s.propose(gamma, manual());
  const denProposal = s.propose(den, manual()).proposal, seen = watched(s), revision = s.getSnapshot().revision;
  assert.equal(s.retainScopes('echo', [alpha, moved(beta, c => { c.generation++; }), 'authored-junk', null, {}, hostile()]), 2);
  assert.equal(seen.calls, 1); assert.equal(s.getSnapshot().revision, revision + 1);
  assert.deepEqual(s.getSnapshot().proposals, [denProposal, kept]);
  const snapshot = s.getSnapshot();
  assert.equal(s.retainScopes('echo', [alpha]), 0); assert.equal(s.retainScopes('echo', [structuredClone(alpha), den]), 0);
  for (const [site, live] of [['', []], [7, []], ['echo', undefined], ['echo', 'alpha'], ['echo', new Set([alpha])], ['echo', new Proxy([], { get: trap })]])
    assert.equal(s.retainScopes(site, live), 0);
  assert.equal(s.getSnapshot(), snapshot); assert.equal(seen.calls, 1);
  assert.equal(s.retainScopes('den', []), 1); assert.equal(s.retainScopes('echo', []), 1); assert.deepEqual(s.getSnapshot().proposals, []);
  const discardedScope = scopeOf(s, 'echo', 'fixture-delta'), held = s.propose(discardedScope, manual()).proposal;
  s.discard(discardedScope, held.id); assert.equal(s.retainScopes('echo', []), 0);
  assert.equal(s.restore(discardedScope, held).proposal, held, 'retainScopes leaves discard history to the fence check');
});

test('clearSite and clearAll drop proposals, forget discards and count what they dropped', () => {
  const s = store(), alpha = scopeOf(s), beta = scopeOf(s, 'echo', 'fixture-beta'), den = scopeOf(s, 'den'), denBeta = scopeOf(s, 'den', 'fixture-beta');
  s.propose(alpha, manual()); s.propose(beta, manual());
  const denProposal = s.propose(den, manual()).proposal, denHeld = s.propose(denBeta, manual()).proposal;
  s.discard(denBeta, denHeld.id);
  const seen = watched(s);
  assert.equal(s.clearSite('echo'), 2); assert.equal(seen.calls, 1); assert.deepEqual(s.getSnapshot().proposals, [denProposal]);
  const snapshot = s.getSnapshot();
  for (const site of ['echo', '', 7, null, undefined, hostile()]) assert.equal(s.clearSite(site), 0);
  assert.equal(s.getSnapshot(), snapshot); assert.equal(seen.calls, 1);
  assert.equal(s.restore(denBeta, denHeld).proposal, denHeld, 'clearing echo keeps den history');
  const echoHeld = s.propose(alpha, manual()).proposal; s.discard(alpha, echoHeld.id);
  assert.equal(s.clearSite('echo'), 0, 'forgetting a discard is not a snapshot change'); assert.equal(seen.calls, 4);
  assert.equal(s.restore(alpha, echoHeld).reason, 'invalid_proposal');
  s.discard(denBeta, denHeld.id);
  assert.equal(s.clearAll(), 1); assert.equal(seen.calls, 6); assert.deepEqual(s.getSnapshot().proposals, []);
  assert.equal(s.clearAll(), 0); assert.equal(seen.calls, 6);
  assert.equal(s.restore(denBeta, denHeld).reason, 'invalid_proposal');
});

test('getSnapshot keeps one identity until a change and revision counts each change once', () => {
  const s = store(), alpha = scopeOf(s), first = s.getSnapshot();
  assert.deepEqual(first, { revision: 0, proposals: [] }); assert.ok(deepFrozen(first)); assert.equal(s.getSnapshot(), first);
  const unsubscribe = s.subscribe(() => {}); unsubscribe();
  s.propose(alpha, { origin: 'preview', target: target() }); s.current(alpha); s.take(alpha, 'aim-proposal-1');
  s.discard(alpha, 'aim-proposal-1'); s.restore(alpha, {}); s.retainScopes('echo', []); s.clearSite('echo'); s.clearAll();
  s.scopeFromDescriptor('echo', descriptor());
  assert.equal(s.getSnapshot(), first, 'no-ops and reads keep the snapshot');
  const revisions = [];
  const step = action => { action(); const snapshot = s.getSnapshot(); assert.equal(s.getSnapshot(), snapshot); revisions.push(snapshot.revision); };
  let proposal;
  step(() => { proposal = s.propose(alpha, manual()).proposal; });
  step(() => { proposal = s.propose(alpha, manual()).proposal; });
  step(() => s.discard(alpha, proposal.id));
  step(() => s.restore(alpha, proposal));
  step(() => s.take(alpha, proposal.id));
  step(() => { proposal = s.propose(alpha, manual()).proposal; });
  step(() => s.take(moved(alpha, c => { c.generation++; }), proposal.id));
  step(() => s.propose(alpha, manual()));
  step(() => s.retainScopes('echo', []));
  step(() => s.propose(alpha, manual()));
  step(() => s.clearSite('echo'));
  step(() => s.propose(alpha, manual()));
  step(() => s.clearAll());
  assert.deepEqual(revisions, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
});

test('snapshot sorts by site then fixture and nothing returned can move store state', () => {
  const s = store(), scopes = [['echo', 'fixture-gamma'], ['den', 'fixture-beta'], ['echo', 'fixture-alpha'], ['den', 'fixture-alpha']]
    .map(([site, fixture]) => scopeOf(s, site, fixture));
  const results = scopes.map(scope => s.propose(scope, { origin: 'manual', target: target({ normal_m: [0, 0, 1] }) }));
  const snapshot = s.getSnapshot();
  assert.deepEqual(snapshot.proposals.map(p => [p.site_id, p.fixture_id]),
    [['den', 'fixture-alpha'], ['den', 'fixture-beta'], ['echo', 'fixture-alpha'], ['echo', 'fixture-gamma']]);
  assert.ok(deepFrozen(snapshot)); assert.ok(results.every(result => deepFrozen(result)));
  const record = results[0].proposal;
  for (const mutate of [() => { snapshot.revision = 99; }, () => { snapshot.proposals.push(record); }, () => { snapshot.proposals[0] = null; },
    () => { record.origin = 'chat'; }, () => { record.id = 'aim-proposal-9'; }, () => { record.scope.generation = 9; },
    () => { record.scope.binding.frame_digest = hex('e'); }, () => { record.target.valid = false; }, () => { record.target.point_m[0] = 9; },
    () => { record.target.normal_m.push(1); }, () => { results[0].ok = false; }, () => { delete record.target; },
    () => { scopes[0].generation = 9; }, () => { scopes[0].binding.registration_version = 9; }, () => { Object.defineProperty(record, 'extra', { value: 1 }); }])
    assert.throws(mutate, TypeError);
  assert.equal(s.getSnapshot(), snapshot); assert.equal(s.current(scopes[0]), record);
  assert.deepEqual(record.target, { point_m: [0.5, 0.25, 0], valid: true, normal_m: [0, 0, 1] });
  assert.equal(s.take(scopes[0], 'aim-proposal-1').proposal, record);
});

test('subscribe notifies synchronously on each change only, and isolates throwing listeners', () => {
  const s = store(), alpha = scopeOf(s), calls = [];
  s.subscribe(() => { throw Error('authored listener failure'); });
  const stop = s.subscribe((...args) => { calls.push([args.length, s.getSnapshot().revision]); });
  s.subscribe(() => { throw Error('authored listener failure'); });
  const proposal = s.propose(alpha, manual());
  assert.equal(proposal.ok, true, 'a throwing listener does not break the change'); assert.deepEqual(calls, [[0, 1]]);
  s.propose(alpha, { origin: 'desk_preview', target: target() }); s.current(alpha); s.take(alpha, 'aim-proposal-99');
  s.clearSite('den'); s.retainScopes('echo', [alpha]); s.getSnapshot();
  assert.equal(calls.length, 1, 'no notification for no-ops');
  s.discard(alpha, proposal.proposal.id); s.restore(alpha, proposal.proposal);
  assert.deepEqual(calls, [[0, 1], [0, 2], [0, 3]]);
  assert.equal(stop(), undefined); stop();
  s.clearAll(); assert.equal(calls.length, 3); assert.equal(s.getSnapshot().revision, 4);
  const twice = { calls: 0 }, counted = () => { twice.calls++; }, one = s.subscribe(counted); s.subscribe(counted);
  s.propose(alpha, manual()); assert.equal(twice.calls, 2); one(); s.clearAll(); assert.equal(twice.calls, 3);
  const order = [];
  let later = null;
  s.subscribe(() => { order.push('first'); if (later) { later(); later = null; } });
  later = s.subscribe(() => { order.push('later'); });
  s.propose(alpha, manual()); assert.deepEqual(order, ['first'], 'unsubscribed during a notification');
  for (const value of [undefined, null, 'listener', {}]) { const noop = s.subscribe(value); assert.equal(typeof noop, 'function'); noop(); }
  assert.equal(s.clearAll(), 1);
});

test('the injected clock stamps created_at_ms and a failing clock refuses without a change', () => {
  let answer = 4242;
  const s = P.createAimProposalStore({ now: () => { if (answer instanceof Error) throw answer; return answer; } }), alpha = scopeOf(s);
  assert.equal(s.propose(alpha, manual()).proposal.created_at_ms, 4242);
  const snapshot = s.getSnapshot();
  for (const value of [NaN, Infinity, '5000', null, undefined, Error('authored clock failure')]) {
    answer = value; assert.deepEqual(s.propose(alpha, manual()), { ok: false, reason: 'clock_unavailable' });
  }
  assert.equal(s.getSnapshot(), snapshot); answer = 4300;
  assert.deepEqual([s.propose(alpha, manual()).proposal.id, s.current(alpha).created_at_ms], ['aim-proposal-2', 4300], 'a refused propose consumes no sequence');
  const before = Date.now(), stamped = P.createAimProposalStore().propose(alpha, manual()).proposal.created_at_ms, after = Date.now();
  assert.ok(stamped >= before && stamped <= after, 'Date.now is the default clock');
  for (const options of [undefined, null, {}, { now: undefined }, hostile()]) assert.equal(typeof P.createAimProposalStore(options).propose, 'function');
  for (const now of [5, 'now', null, {}]) assert.throws(() => P.createAimProposalStore({ now }), TypeError);
});

test('the module owns no storage, network, timer, DOM or apartment data authority', () => {
  const source = fs.readFileSync(MODULE, 'utf8');
  for (const token of ['localStorage', 'sessionStorage', 'indexedDB', 'fetch(', 'XMLHttpRequest', 'WebSocket', 'setTimeout',
    'setInterval', 'requestAnimationFrame', 'HomeApartmentData', 'callService', 'document.']) assert.ok(!source.includes(token), token);
  assert.doesNotMatch(source, /\bimport\b|\brequire\s*\(/);
  assert.match(source, /'use strict'/);
});

test('browser path: a plain script in a fresh global defines exactly HomeApartmentAimProposals', () => {
  const sandbox = {}, context = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(MODULE, 'utf8'), context, { filename: 'home-apartment-aim-proposals.js' });
  assert.deepEqual(Object.keys(sandbox), ['HomeApartmentAimProposals']);
  const api = sandbox.HomeApartmentAimProposals;
  assert.ok(Object.isFrozen(api)); assert.deepEqual(Object.keys(api), Object.keys(P));
  const s = api.createAimProposalStore({ now: () => 7 }), scope = s.scopeFromDescriptor('echo', descriptor());
  assert.deepEqual(Object.keys(s).sort(), STORE_KEYS); assert.ok(Object.isFrozen(s));
  const result = s.propose(scope, manual());
  assert.equal(result.ok, true); assert.equal(result.proposal.created_at_ms, 7); assert.equal(s.getSnapshot().proposals.length, 1);
  assert.equal(vm.runInContext('typeof module', context), 'undefined');
});

test('node path: require returns the same frozen API', () => {
  assert.deepEqual(Object.keys(P), ['createAimProposalStore']); assert.ok(Object.isFrozen(P));
  assert.equal(require('../app/src/home-apartment-aim-proposals.js'), P);
  assert.equal(globalThis.HomeApartmentAimProposals, P, 'the UMD idiom also names the API on the Node global');
  const s = P.createAimProposalStore();
  assert.deepEqual(Object.keys(s).sort(), STORE_KEYS); assert.ok(Object.isFrozen(s));
  assert.ok(Object.values(s).every(value => typeof value === 'function'));
});
