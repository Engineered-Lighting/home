// APT-A01/A05 (W2): the one hand-change switch point. Every fixture change a
// person makes in the Apartment UI calls releaseFixtureForHandChange first,
// synchronously; on the UI line it does nothing. Den binds that one function
// to manualTakeover(), and later to aimPreview.releaseForManual(), when it
// composes its desk preview (Den coordination agreement, 2026-10-19).
// Source contract over the actual home-apartment.jsx, plus the actual call
// sites run in a VM with authored stand-ins. No browser, room or transport.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'app/src/home-apartment.jsx'), 'utf8');
const babel = require(path.join(ROOT, 'app/src/vendor/babel-7.29.0/babel.min.js'));
const program = babel.transform(SOURCE, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
const text = node => SOURCE.slice(node.start, node.end);
// Values made inside a VM context have that context's prototypes.
const plain = value => JSON.parse(JSON.stringify(value));
const HOOK = 'releaseFixtureForHandChange';
// The producer's fixture writes. Each one changes what a fixture shows.
const WRITES = new Set(['setVirtualFixtureState', 'aimVirtualFixtureAtTarget', 'restoreVirtualFixtureAim']);

function walk(node, visit, parents = []) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node, parents);
  const next = node.type ? [...parents, node] : parents;
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit, next));
    else if (value && typeof value === 'object') walk(value, visit, next);
  }
}
const owners = program.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === 'HomeApartmentLiveView');
assert.equal(owners.length, 1, 'one actual Apartment owner');
const owner = owners[0], declarations = new Map();
for (const statement of owner.body.body) if (statement.type === 'VariableDeclaration')
  for (const declaration of statement.declarations) if (declaration.id.type === 'Identifier') declarations.set(declaration.id.name, declaration.init);
const calleeName = call => call.callee.type === 'Identifier' ? call.callee.name
  : call.callee.type === 'MemberExpression' && !call.callee.computed ? call.callee.property.name
  : call.callee.type === 'OptionalMemberExpression' ? call.callee.property.name : null;
// Every write and hook call in the owner, with the owner-level binding or
// effect that contains it.
function sites() {
  const rows = [];
  walk(owner.body, (node, parents) => {
    if (!['CallExpression', 'OptionalCallExpression'].includes(node.type)) return;
    const name = calleeName(node);
    if (!WRITES.has(name) && name !== 'changeVirtualFixture' && name !== HOOK) return;
    const top = parents.find(parent => parent.type === 'VariableDeclarator' && owner.body.body.some(statement =>
      statement.type === 'VariableDeclaration' && statement.declarations.includes(parent)));
    const effect = parents.find(parent => parent.type === 'CallExpression' && parent.callee?.name === 'useEffect');
    const fn = [...parents].reverse().find(parent => ['ArrowFunctionExpression', 'FunctionExpression'].includes(parent.type));
    rows.push({ name, node, owner: top?.id.name || (effect ? 'useEffect' : null), effect, fn, start: node.start,
      reason: node.arguments[1]?.properties?.find(item => item.key?.name === 'reason')?.value });
  });
  return rows;
}

test('the switch point is one owner-level function bound to the Den takeover and never throws', () => {
  const hook = declarations.get(HOOK);
  assert.ok(hook && hook.type === 'ArrowFunctionExpression', 'one actual ' + HOOK);
  // Den binds the seam to the desk-preview takeover and the brain's handChange.
  const log = [], refs = (preview, follower) => ({ denDeskAimPreviewRef: { current: preview }, lightingBrainRef: { current: follower } });
  const idle = vm.runInNewContext('(' + text(hook) + ')', refs(null, null));
  for (const args of [['fixture-a', { reason: 'change' }], ['fixture-a'], [undefined, undefined], [null, {}]])
    assert.deepEqual(plain(idle(...args)), { released: false, restored: false, excluded: false });
  assert.deepEqual(Object.keys(idle('fixture-a', { reason: 'aim' })), ['released', 'restored', 'excluded']);
  const preview = { readback: () => ({ active: true, fixtureId: 'fixture-a' }), manualTakeover: reason => log.push(reason) };
  const follower = { follower: { handChange: (id, { reason }) => log.push(id + ':' + reason) } };
  const bound = vm.runInNewContext('(' + text(hook) + ')', refs(preview, follower));
  assert.deepEqual(plain(bound('fixture-a', { reason: 'aim' })), { released: true, restored: false, excluded: false });
  assert.deepEqual(plain(bound('fixture-b', { reason: 'undo' })), { released: false, restored: false, excluded: false });
  assert.deepEqual(log, ['manual_aim_gesture', 'fixture-a:aim', 'manual_aim_history', 'fixture-b:undo']);
  const throwing = vm.runInNewContext('(' + text(hook) + ')', refs({ readback() { throw Error('x'); } }, { follower: { handChange() { throw Error('y'); } } }));
  assert.deepEqual(plain(throwing('fixture-a', { reason: 'change' })), { released: false, restored: false, excluded: false });
});

test('every UI-originated fixture write passes the switch point first, in the same function', () => {
  const rows = sites(), writes = rows.filter(row => row.name !== HOOK);
  const allowed = [], gated = [];
  for (const row of writes) {
    // Den-reserved hunks, unchanged by W2: the re-apply effect replays the
    // remembered state of a reloaded preview, and changeVirtualFixture's own
    // producer write is reached only through the wrapped call below.
    // Den's composition moves the same replay into reapplyVirtualFixtureMemory.
    if (row.owner === 'reapplyVirtualFixtureMemory'
        || row.owner === 'useEffect' && text(row.effect).includes('virtualFixtureStatesRef.current.targets) overlay.aimVirtualFixtureAtTarget')) {
      allowed.push({ name: row.name, site: 'virtualFixtureStatesRef re-apply effect (Den-reserved replay)' }); continue;
    }
    if (row.owner === 'changeVirtualFixture' && row.name === 'setVirtualFixtureState') {
      allowed.push({ name: row.name, site: 'inside changeVirtualFixture (Den-reserved)' }); continue;
    }
    const hooks = rows.filter(item => item.name === HOOK && item.fn === row.fn && item.start < row.start);
    assert.equal(hooks.length, 1, `${row.name} in ${row.owner} is preceded by exactly one ${HOOK} in the same function`);
    // Den gate: the release comes before the light's aim is read (aimScopeFor).
    const reads = []; walk(row.fn, node => { if (node.type === 'CallExpression' && node.callee?.name === 'aimScopeFor') reads.push(node.start); });
    assert.ok(reads.every(start => start > hooks[0].start), `${row.owner} releases before it reads the light's aim`);
    gated.push({ name: row.name, owner: row.owner, reason: text(hooks[0].reason || hooks[0].node.arguments[1]) });
  }
  assert.deepEqual([...new Set(allowed.map(item => item.name))].sort(), ['aimVirtualFixtureAtTarget', 'setVirtualFixtureState']);
  assert.equal(allowed.filter(item => item.site.startsWith('inside changeVirtualFixture')).length, 1);
  // The complete set of switch-point call sites Den signs off (2026-10-19).
  // TODO(Den): future Chat-originated and perception-Set fixture writes must
  // call the switch point too, and join this list when they land.
  assert.deepEqual(gated.map(({ name, owner, reason }) => [owner, name, reason]).sort(), [
    ['applyAimHistoryState', 'restoreVirtualFixtureAim', 'reason'],
    ['changeVirtualFixtureByHand', 'changeVirtualFixture', '"change"'],
    ['setAimProposal', 'aimVirtualFixtureAtTarget', '"aim"'],
  ]);
  assert.equal(rows.filter(row => row.name === HOOK).length, 3, 'no other switch-point calls');
});

test('changeVirtualFixture is called only through the wrapped hand change', () => {
  const calls = sites().filter(row => row.name === 'changeVirtualFixture');
  assert.deepEqual(calls.map(row => row.owner), ['changeVirtualFixtureByHand']);
  // Channel edits, sliders, room power, radial painting and the Pan/Tilt fine-tune all use it.
  const wrapped = [];
  walk(owner.body, node => {
    if (node.type === 'Identifier' && node.name === 'changeVirtualFixtureByHand') wrapped.push(node.start);
  });
  assert.ok(wrapped.length >= 9, 'every channel, slider, power and painting path names the wrapped change');
  assert.doesNotMatch(SOURCE.replace(/const changeVirtualFixture = [\s\S]*?\n  };\n/, ''), /\bonPatch=\{changeVirtualFixture\}/);
});

test('the fixture-body gesture controller can no longer preview, restore or commit an aim', () => {
  let gesture = null;
  walk(owner.body, node => {
    if (node.type === 'CallExpression' && calleeName(node) === 'createVirtualFixtureAimGesture') gesture = node.arguments[0];
  });
  assert.ok(gesture, 'the existing gesture controller stays');
  const option = key => gesture.properties.find(item => item.key?.name === key)?.value;
  const context = vm.createContext({});
  assert.deepEqual(plain(vm.runInContext('(' + text(option('previewTarget')) + ')', context)('fixture-a', { point: [1, 1, 0] }, {})),
    { ok: false, reason: 'reticle_only' });
  assert.deepEqual(plain(vm.runInContext('(' + text(option('restoreSnapshot')) + ')', context)('fixture-a', {})), { ok: true });
  assert.equal(option('onCommit'), undefined, 'nothing is committed from a fixture-body drag');
  for (const key of ['previewTarget', 'restoreSnapshot']) assert.doesNotMatch(text(option(key)), /overlay|aimVirtualFixtureAtTarget|restoreVirtualFixtureAim/);
});

// Run the actual call sites with authored stand-ins and a recording switch
// point: the switch point runs first, once, with the fixture id and reason.
function install(name, context) {
  const init = declarations.get(name);
  assert.ok(init, 'actual declaration: ' + name);
  context[name] = vm.runInContext('(' + text(init) + ')', context);
  return context[name];
}
function authored() {
  const calls = [], descriptor = id => ({ fixture_id: id, generation: 3, state: { pan_deg: 10, tilt_deg: 40, radials: [] }, target: null });
  const scope = { authored: 'scope' };
  const overlay = {
    aimVirtualFixtureAtTarget: (id, target, options) => { calls.push(['aim', id, options.expectedGeneration]); return { ok: true, descriptor: descriptor(id) }; },
    restoreVirtualFixtureAim: (id, snapshot) => { calls.push(['restore', id, snapshot.state.pan_deg]); return { ok: true }; },
  };
  let pending = { id: 'p1', origin: 'manual', target: { point_m: [1, 2, 0], valid: true, normal_m: [0, 0, 1] } };
  const store = { take: (s, id) => { calls.push(['take', id]); pending = null; return { ok: true }; },
    propose: () => ({ ok: true, proposal: pending }), restore: () => ({ ok: false }), current: () => pending };
  const context = vm.createContext({
    calls, aimProposals: store, showToast: text => calls.push(['toast', text]), setVirtualAimNotice() {},
    syncVirtualFixturePreview: () => calls.push(['sync']), recordAimHistory: () => calls.push(['history']),
    changeVirtualFixture: (patch, id) => { calls.push(['change', id, patch]); return true; },
    selectedVirtualFixture: { fixture_id: 'fixture-a' },
    aimScopeFor: id => ({ context: { engine: { overlay } }, descriptor: descriptor(id), scope, proposal: pending }),
    releaseFixtureForHandChange: (id, { reason } = {}) => { calls.push(['switch', id, reason]); return { released: false, restored: false, excluded: false }; },
  });
  return { context, calls, set pending(value) { pending = value; }, get pending() { return pending; } };
}

test('actual channel change, aim Set and history restore call the switch point before the fixture write', () => {
  let f = authored();
  assert.equal(install('changeVirtualFixtureByHand', f.context)({ spotlight: { on: true } }), true);
  assert.deepEqual(f.calls, [['switch', 'fixture-a', 'change'], ['change', 'fixture-a', { spotlight: { on: true } }]]);

  f = authored();
  assert.equal(install('setAimProposal', f.context)({ fixture_id: 'fixture-a', proposal: f.pending }), true);
  assert.deepEqual(f.calls.slice(0, 3), [['switch', 'fixture-a', 'aim'], ['take', 'p1'], ['aim', 'fixture-a', 3]]);

  for (const direction of ['undo', 'redo']) {
    f = authored(); f.pending = null;
    const apply = install('applyAimHistoryState', f.context);
    const aim = { fixture_id: 'fixture-a', generation: 3, state: { pan_deg: -25, tilt_deg: 40, radials: [] }, target: { point: [0, 1, 0] } };
    assert.equal(apply('fixture-a', { aim, proposal: null }, direction), true);
    assert.deepEqual(f.calls, [['switch', 'fixture-a', direction], ['restore', 'fixture-a', -25]]);
  }
});

test('proposals change no fixture, so proposing and discarding skip the switch point; an unchanged history step releases but never rewrites', () => {
  let f = authored();
  install('proposeAim', f.context)({ fixture_id: 'fixture-a', target: { point_m: [1, 2, 0], valid: true, normal_m: [0, 0, 1] } });
  assert.equal(f.calls.some(call => call[0] === 'switch'), false);
  f = authored(); f.pending = null;
  const same = { fixture_id: 'fixture-a', generation: 3, state: { pan_deg: 10, tilt_deg: 40, radials: [] }, target: null };
  assert.equal(install('applyAimHistoryState', f.context)('fixture-a', { aim: same, proposal: null }, 'undo'), true);
  assert.deepEqual(f.calls, [['switch', 'fixture-a', 'undo']], 'the release still comes first; an unchanged aim is not rewritten');
});

test('a refused Set leaves the fixture alone after the switch point and keeps the proposal', () => {
  const f = authored();
  f.context.aimScopeFor = id => ({ context: { engine: { overlay: { aimVirtualFixtureAtTarget: () => { f.calls.push(['aim-refused']); return { ok: false }; } } } },
    descriptor: { fixture_id: id, generation: 3, state: {}, target: null }, scope: {}, proposal: f.pending });
  const proposal = f.pending;
  f.context.aimProposals.propose = (scope, request) => { f.calls.push(['repropose', request.target === proposal.target]); return { ok: true }; };
  assert.equal(install('setAimProposal', f.context)({ fixture_id: 'fixture-a', proposal }), false);
  assert.deepEqual(f.calls.map(call => call[0]), ['switch', 'take', 'aim-refused', 'repropose']);
  assert.equal(f.calls.at(-1)[1], true);
});
