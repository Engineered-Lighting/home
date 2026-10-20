/* docs/contracts/den: the reason vocabulary and synthetic fixtures stay
 * consistent with the producers and Home's validators. Docs only; no host. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { sealRegisteredDeskTarget } from '../app/src/home-3d/registered-desk-target.mjs';

const require = createRequire(import.meta.url);
const Core = require('../app/src/home-equipment-registration.js');
const Client = require('../app/src/home-lighting-brain-client.js');
const Sofa = require('../app/src/home-lighting-sofa-target.js');
const ROOT = fileURLToPath(new URL('../', import.meta.url));
const DIR = path.join(ROOT, 'docs/contracts/den');
const read = name => JSON.parse(fs.readFileSync(path.join(DIR, name), 'utf8'));
const vocabulary = read('reason-vocabulary.json');
const group = id => vocabulary.groups.find(g => g.id === id);

// The lookup rule documented in reason-vocabulary.md.
function shown(groupId, code) {
  const g = group(groupId);
  assert.ok(g, groupId);
  const exact = g.codes.find(entry => entry.code === code || entry.aliases?.includes(code));
  const pattern = exact || g.codes.find(entry => entry.pattern && new RegExp('^' + entry.pattern
    .replace('{n}', '\\d+').replace('{state}', '[a-z][a-z0-9_]*') + '$').test(code));
  if (!pattern) return { text: 'Unavailable', unknown: true };
  return { text: pattern.legacy ? `${pattern.text} (retired preview)` : pattern.text, severity: pattern.severity, legacy: !!pattern.legacy };
}

test('the vocabulary is well formed: unique codes per group, text everywhere, legacy group fully legacy', () => {
  assert.equal(vocabulary.schema, 'den-reason-vocabulary-1');
  for (const g of vocabulary.groups) {
    const keys = g.codes.map(entry => entry.code || entry.pattern);
    assert.equal(new Set(keys).size, keys.length, g.id);
    for (const entry of g.codes) {
      assert.ok(typeof entry.text === 'string' && entry.text.length > 0 && entry.text.length <= 80, `${g.id}:${entry.code}`);
      assert.ok(!entry.severity || ['warn', 'alert'].includes(entry.severity));
      assert.ok(entry.code ? /^[a-z][a-z0-9_]*$/.test(entry.code) : /^[a-z_]+\{(n|state)\}$/.test(entry.pattern));
    }
    if (g.legacy) assert.ok(g.codes.every(entry => entry.legacy === true), g.id);
    else assert.ok(g.codes.every(entry => !entry.legacy), g.id);
  }
});

test('vocabulary covers what the producers emit today', (t) => {
  assert.deepEqual(group('brain.mode').codes.map(e => e.code), [...Client.MODES]);
  for (const label of ['reading', 'computer_work', 'phone_use', 'resting', 'transition', 'other', 'insufficient_evidence'])
    assert.equal(shown('activity.label', label).unknown, undefined, label);
  // Activity-loop health details, read from the producer source as literals.
  // AL-3 lands on the backend stack; skip the producer check until it is present.
  if (!fs.existsSync(path.join(ROOT, 'stack/services/perception-lab/activity_loop.py'))) return t.skip('AL-3 producer source not on this branch');
  const loop = ['activity_loop.py', 'activity_loop_runtime.py'].map(f =>
    fs.readFileSync(path.join(ROOT, 'stack/services/perception-lab', f), 'utf8')).join('\n');
  const details = new Set([...loop.matchAll(/\(False, '([a-z_]+)'\)|'(jev_stale|not_reported|paused_room_empty)'/g)].map(m => m[1] || m[2]));
  assert.ok(details.size >= 8, 'producer health literals found');
  for (const reason of ['operator_stop', 'monitor_backoff']) details.add('stopped_' + reason);
  for (const state of ['requested', 'starting', 'stopping', 'stopped', 'failed', 'interrupted', 'blocked', 'indeterminate']) details.add('deep_stack_' + state);
  details.delete('deep_stack_'); details.delete('jev_');
  for (const detail of details) assert.equal(shown('activity_loop.health', detail).unknown, undefined, detail);
  assert.equal(shown('activity_loop.health', 'jev_released_resuming').text, 'JEV released · resuming');
  // Home's own refusal codes from the brain client.
  const client = fs.readFileSync(path.join(ROOT, 'app/src/home-lighting-brain-client.js'), 'utf8');
  for (const [, code] of client.matchAll(/(?:refuse|return) ?\(?'((?:state|fixture)_[a-z_]+)'/g))
    assert.equal(shown('brain.refusal', code).unknown, undefined, code);
  // The approximate-placement integrity reasons Home still surfaces.
  const Desk = require('../app/src/home-apartment-desk-aim-preview.js');
  for (const reason of Desk.INTEGRITY_REASONS.filter(r => r.startsWith('approximate_')))
    assert.equal(shown('perception.placement', reason).unknown, undefined, reason);
  for (const reason of Desk.INTEGRITY_REASONS.filter(r => r.startsWith('desk_preview_')))
    assert.equal(shown('legacy.desk_preview', reason).legacy, true, reason);
});

test('the example status rows render with the documented lookup', () => {
  for (const row of read('fixtures/reason-rows.json').rows) {
    const result = shown(row.group, row.code);
    assert.equal(result.text, row.shown, `${row.group}:${row.code}`);
    assert.equal(result.severity, row.severity, `${row.group}:${row.code}`);
    assert.equal(!!result.unknown, !!row.unknown); assert.equal(!!result.legacy, !!row.legacy);
  }
});

test('brain, monitor and activity fixtures are valid and use vocabulary codes', () => {
  const state = read('fixtures/brain-state-reading.json');
  assert.equal(Client.validateState(state).ok, true);
  assert.equal(shown('brain.mode', state.mode).unknown, undefined);
  for (const d of state.decisions) assert.equal(shown('brain.rule', d.rule).unknown, undefined, d.rule);
  for (const f of state.fixtures) if (f.pause_reason) assert.equal(shown('fixture.pause', f.pause_reason).unknown, undefined);
  for (const name of ['den-ops-banner-alert.json', 'den-ops-banner-silent.json']) {
    const doc = read('fixtures/' + name), banner = Client.opsBanner(doc);
    assert.ok(banner && ['warn', 'alert'].includes(banner.severity), name);
    assert.equal(group('den_ops.level').codes.find(e => e.code === doc.step).level, doc.level);
  }
  assert.equal(Client.opsBanner(read('fixtures/den-ops-banner-silent.json')).text, 'Ops monitor silent on victoria-jetson');
  for (const e of read('fixtures/activity-health-events.json').events)
    if (e.detail) assert.equal(shown('activity_loop.health', e.detail).unknown, undefined, e.detail);
  for (const e of read('fixtures/person-pose-event.json').events) {
    assert.equal(e.type, 'person_pose');
    for (const person of e.people) {
      assert.equal(person.approximate, true);
      assert.ok(person.head_pitch_deg === null || person.head_pitch_deg >= -30 && person.head_pitch_deg <= 60);
    }
  }
});

test('named-aim, sofa and geometry fixtures agree and re-seal', () => {
  const sofa = read('fixtures/sofa-target-synthetic.json'), aim = read('fixtures/named-aim-sofa.json');
  const geometry = read('fixtures/lighting-brain-geometry-synthetic.json');
  const seal = { sealDesk: sealRegisteredDeskTarget, digest: Core.digest };
  assert.deepEqual(Sofa.validateSofaTarget(sofa, { binding: sofa.binding, equipment_ref: sofa.equipment_ref }, seal), sofa);
  assert.equal(aim.schema, 'home-named-aim-1'); assert.equal(aim.revision, aim.base_revision + 1);
  assert.deepEqual(aim.geometry.vertices_m, sofa.zone.vertices_m); assert.deepEqual(aim.seat_m, sofa.seat.point_m);
  assert.deepEqual(aim.source, { schema: sofa.schema, digest: sofa.digest });
  assert.equal(geometry.schema, 'lighting-brain-geometry-1'); assert.equal(geometry.synthetic, true);
  assert.deepEqual(geometry.fixtures.map(f => f.fixture_id), Client.FIXTURE_IDS);
  assert.deepEqual(geometry.zones.sofa.seat_m, sofa.seat.point_m); assert.deepEqual(geometry.sources.sofa.digest, sofa.digest);
  const unsigned = { ...geometry }; delete unsigned.digest; delete unsigned.synthetic;
  assert.equal(Core.digest(unsigned), geometry.digest);
  const caps = read('fixtures/home-den-capabilities-victoria.json');
  assert.equal(caps.engineered.value, 'modeled'); assert.ok(caps.engineered.fixtures.every(f => f.physical_output === false));
  assert.equal(caps.body.profile, null); assert.equal(caps.bench.supported, false);
  assert.equal(shown('perception.age', caps.perception.frame.age.reason).unknown, undefined);
});

test('contract docs and fixtures stay synthetic: every fixture is marked or re-sealed, and docs link only existing files', () => {
  for (const name of fs.readdirSync(path.join(DIR, 'fixtures'))) {
    const doc = read('fixtures/' + name);
    assert.ok(doc.synthetic === true || doc.schema === 'home-registered-sofa-target-1', name);
    if (doc.binding) assert.match(doc.binding.content_revision, /^synthetic-/, name);
  }
  for (const name of fs.readdirSync(DIR).filter(n => n.endsWith('.md'))) {
    const text = fs.readFileSync(path.join(DIR, name), 'utf8');
    for (const [, link] of text.matchAll(/\]\(((?!https?:)[^)#]+)\)/g))
      assert.ok(fs.existsSync(path.join(DIR, link)), `${name} -> ${link}`);
  }
});
