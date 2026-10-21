/* AL-4: Home follows the lighting brain. A mock brain (contract fixture JSON,
 * fake fetch and a fake SSE source) drives the actual client, follower, panel
 * JSX and the real virtual-fixture overlay. No browser, host, camera or device. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { createEquipmentVirtualFixtures } from '../app/src/home-3d/equipment-virtual-fixtures.mjs';
import { sealRegisteredDeskTarget } from '../app/src/home-3d/registered-desk-target.mjs';
import { buildBrainGeometry, prepareBrainGeometryFile, parseArgs } from './den-room/brain-targets.mjs';

const require = createRequire(import.meta.url);
const Core = require('../app/src/home-equipment-registration.js');
const Client = require('../app/src/home-lighting-brain-client.js');
const Follower = require('../app/src/home-lighting-brain-follower.js');
const Sofa = require('../app/src/home-lighting-sofa-target.js');
const SRC = fileURLToPath(new URL('../app/src/', import.meta.url));
const copy = structuredClone, hash = c => c.repeat(64), pause = () => new Promise(resolve => setImmediate(resolve));
const [F1, F2, F3, F4] = Client.FIXTURE_IDS;
const BASE = '/api/lighting-brain/v1/homes/victoria';

/* ---------- mock brain ---------- */
const OFF = { ambient: { radial_level: 0, down_level: 0, cct_k: 2700, fade_s: 3 },
  spot: { on: false, level: 0, cct_k: 3500, target_m: null, fade_s: 1.5 }, paused_until: null, pause_reason: null };
function brainState({ at = 1000, why = 'Room empty · all off', mode = 'off', fixtures = {}, decisions = null, brain = 'online' } = {}) {
  return { schema: 'activity-lighting-state-1', home: 'victoria', room: 'den', generated_at: at, brain, mode, why,
    occupancy: { present: mode !== 'off', people_count: mode === 'off' ? 0 : 1, zones: { sofa: mode !== 'off', desk: false } },
    activity: { label: mode === 'reading' ? 'reading' : null, confidence: 0.8, age_s: 1.2, source: 'jev' },
    reading_evidence: { bound: true, activity: { session_id: 'authored-session' }, pose: { session_id: 'authored-session' },
      activity_valid_until: at + 15, pose_valid_until: at + 15 },
    fixtures: Client.FIXTURE_IDS.map(fixture_id => ({ fixture_id, ...copy(OFF), ...copy(fixtures[fixture_id] || {}) })),
    decisions: decisions || [{ at, rule: mode === 'reading' ? 'reading_spot_on' : 'room_off', why, inputs: {}, change: null }],
    cost: { jev_calls_last_hour: 0, est_usd_last_hour: null },
    inputs: { occupancy_age_s: 0, activity_age_s: 0, pose_age_s: 0, health: { jev: true, deep_stack: true, frigate: true } },
    settings_version: 'v1' };
}
const AMBIENT = { ambient: { radial_level: 0.3, down_level: 0.3, cct_k: 2700, fade_s: 2 } };
const READING = { ...AMBIENT, spot: { on: true, level: 0.8, cct_k: 3500, target_m: [2, 2, 0.8], fade_s: 1.5 } };

function clock() {
  let now = 0, seq = 0; const timers = new Map();
  return {
    nowMs: () => now,
    setTimeout: (fn, ms) => { timers.set(++seq, { fn, at: now + ms }); return seq; },
    clearTimeout: id => { timers.delete(id); },
    size: () => timers.size,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!due) break;
        timers.delete(due[0]); now = due[1].at; due[1].fn(); await pause();
      }
      now = end; await pause();
    },
  };
}
function mockBrain(initial = brainState()) {
  const brain = { state: initial, requests: [], sources: [], getStatus: 200, overrideStatus: 200 };
  brain.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    brain.requests.push({ url, method, headers: options.headers || {}, body: options.body ? JSON.parse(options.body) : null });
    if (method === 'GET') {
      if (brain.getStatus === 0) throw Error('network');
      return { ok: brain.getStatus === 200, status: brain.getStatus, text: async () => JSON.stringify(brain.state) };
    }
    return { ok: brain.overrideStatus === 200, status: brain.overrideStatus, text: async () => '{}' };
  };
  brain.EventSource = class {
    constructor(url, options) { this.url = url; this.options = options; this.closed = false; brain.sources.push(this); }
    close() { this.closed = true; }
    addEventListener() {}
  };
  brain.live = () => brain.sources.filter(s => !s.closed).at(-1);
  brain.push = async state => { brain.state = state; brain.live().onmessage({ data: JSON.stringify(state) }); await pause(); };
  brain.pushRaw = async raw => { brain.live().onmessage({ data: raw }); await pause(); };
  return brain;
}

/* ---------- real virtual-fixture overlay ---------- */
function registrationModel({ camera = true } = {}) {
  const references = { frame: 'z_up_metric_floor0', units: 'm', confirmed: true, provenance: { kind: 'authored_test' },
    floor: { id: 'floor', normal: [0, 0, 1], offset_m: .4 },
    walls: [[1, 0, 0], [0, 1, 0], [-1, 0, -4], [0, -1, -5]].map(([x, y, offset_m], index) =>
      ({ id: 'wall-' + index, label: 'Authored wall ' + index, normal_xy: [x, y], offset_m })) };
  const binding = { content_revision: 'authored-brain-1', registration_version: 1,
    manifest_digest: hash('a'), frame_digest: hash('b'), canonical_collision_digest: hash('c') };
  let model = Core.toModel(Core.createDocument(binding, references));
  model = Core.addSocket(Core.addSocket(model));
  for (let i = 1; i <= 4; i++) model = Core.updateModelEquipment(model, 'victoria-den-mount-0' + i, {
    physical_kind: 'socket', mount_type: 'ceiling', measurements: {
      wall_distances: [{ wall_id: 'wall-0', distance_m: i < 3 ? i : 3 }, { wall_id: 'wall-1', distance_m: i < 3 ? 1 : i }],
      height_above_floor_m: 2.4, method: 'laser', uncertainty_m: .005 } });
  if (camera) model = Core.updateModelEquipment(model, 'victoria-den-oak-d-pro-01', { mount_type: 'wall', measurements: {
    wall_distances: [{ wall_id: 'wall-0', distance_m: 2 }, { wall_id: 'wall-1', distance_m: .1 }],
    height_above_floor_m: 2, method: 'tape', uncertainty_m: .01 } });
  return model;
}
function equipment() {
  const preview = createEquipmentVirtualFixtures(new THREE.Group());
  preview.setDocument(Core.toDocument(registrationModel()));
  const calls = { set: 0, aim: 0 };
  const overlay = { virtualFixturesReadback: () => preview.readback(),
    virtualFixtureInteractionDescriptor: id => preview.interactionDescriptor(id),
    setVirtualFixtureState: (id, patch) => { calls.set++; return preview.setFixtureState(id, patch); },
    aimVirtualFixtureAtTarget: (id, target, options) => { calls.aim++; return preview.aimAtTarget(id, target, options); } };
  const state = id => preview.readback().fixtures.find(row => row.fixture_id === id).state;
  return { preview, overlay, calls, state };
}

async function following(initial, { configure = () => {}, ...options } = {}) {
  const t = clock(), brain = mockBrain(initial), room = equipment(), views = [], applied = [];
  configure(room);
  const wired = Follower.createBrainFollowing({ Client, fetch: brain.fetch, EventSource: brain.EventSource,
    setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, nowMs: t.nowMs, getOverlay: () => room.overlay,
    onChange: view => views.push(view), onApplied: id => applied.push(id), ...options });
  await pause(); await pause();
  return { t, brain, room, views, applied, wired, view: () => wired.follower.readback() };
}
const near = (a, b, tol = 1e-9) => assert.ok(Math.abs(a - b) <= tol, `${a} != ${b}`);

test('authored reading observations pass through the real publisher/brain into moving Home virtual aims',async()=>{
  const run=spawnSync(process.env.PYTHON||'python',['tools/fixtures/den-reading-coordinator.py'],{
    cwd:fileURLToPath(new URL('../',import.meta.url)),encoding:'utf8',timeout:15000,windowsHide:true});
  assert.ifError(run.error);assert.equal(run.status,0,run.stderr);
  const fixture=JSON.parse(run.stdout);assert.deepEqual(fixture.schemas,['activity-lighting-events-2']);
  assert.equal(fixture.camera_calls,0);assert.equal(fixture.model_calls,0);
  const f=await following(fixture.states[0].state);let previousTarget=null,moved=false;
  try{
    for(const row of fixture.states){
      await f.brain.push(row.state);await f.t.advance(3100);
      const lit=row.state.fixtures.filter(x=>x.spot.on);
      assert.equal(f.view().physical_commands,false);
      for(const state of row.state.fixtures){
        assert.equal(f.room.state(state.fixture_id).spotlight.on,state.spot.on,row.phase);
      }
      if(lit.length){
        assert.equal(lit.length,1);
        const selected=lit[0],target=f.room.preview.interactionDescriptor(selected.fixture_id).target;
        assert.equal(target.kind,'lighting_brain_target');
        assert.deepEqual(target.point,selected.spot.target_m);
        if(previousTarget){assert.notDeepEqual(target.point,previousTarget,'a moved shoulder pose must move the virtual aim');moved=true;}
        previousTarget=target.point;
      }
    }
    assert.equal(moved,true);assert.ok(f.room.calls.aim>=2);
  }finally{f.wired.dispose();}
});

/* ---------- schema ---------- */
test('activity-lighting-state-1 is accepted and malformed documents are refused with a reason', () => {
  assert.equal(Client.validateState(brainState({ mode: 'reading', fixtures: { [F3]: READING } })).ok, true);
  const cases = [
    [s => { s.schema = 'activity-lighting-state-2'; }, 'state_schema_unsupported'],
    [s => { s.home = 'la'; }, 'state_scope_mismatch'],
    [s => { s.fixtures.pop(); }, 'state_fixtures_incomplete'],
    [s => { s.fixtures[0].fixture_id = 'victoria-den-mount-09'; }, 'fixture_id_unknown'],
    [s => { s.fixtures[1] = copy(s.fixtures[0]); }, 'state_fixtures_duplicate'],
    [s => { s.fixtures[2].ambient.radial_level = 1.4; }, 'fixture_ambient_invalid'],
    [s => { s.fixtures[2].spot.target_m = [1, 2]; }, 'fixture_spot_invalid'],
    [s => { s.mode = 'party'; }, 'state_header_invalid'],
    [s => { s.decisions = Array.from({ length: 21 }, (_, i) => ({ at: i, rule: 'r', why: 'w' })); }, 'state_decisions_invalid'],
  ];
  for (const [mutate, reason] of cases) {
    const doc = brainState(); mutate(doc);
    assert.deepEqual(Client.validateState(doc), { ok: false, reason, state: null }, reason);
  }
  assert.equal(Client.parse('{not json').reason, 'state_json_invalid');
});

/* ---------- apply ---------- */
test('ambient and spot states reach the real virtual fixtures through the existing seams, with fades', async () => {
  const f = await following(brainState());
  assert.equal(f.view().online, true);
  assert.equal(f.room.state(F1).spotlight.on, true, 'the off fade keeps the channel on until it ends');
  await f.t.advance(3000);
  for (const id of Client.FIXTURE_IDS) {
    assert.ok(f.room.state(id).radials.every(z => !z.on && z.brightness === 0));
    assert.equal(f.room.state(id).spotlight.on, false);
  }

  await f.brain.push(brainState({ at: 1010, mode: 'reading', why: 'Reading at sofa · spotlight on', fixtures: { [F3]: READING, [F4]: AMBIENT } }));
  await f.t.advance(1000);
  const mid = f.room.state(F3);
  assert.ok(mid.radials[0].brightness > 0.1 && mid.radials[0].brightness < 0.2, 'ambient half-way through its 2 s fade');
  await f.t.advance(1200);
  const s3 = f.room.state(F3), s4 = f.room.state(F4);
  for (const zone of s3.radials) { assert.equal(zone.on, true); near(zone.brightness, 0.3); assert.equal(zone.color_temp_kelvin, 2700); }
  assert.deepEqual([s3.downward.on, s3.downward.color_temp_kelvin], [true, 2700]); near(s3.downward.brightness, 0.3);
  assert.deepEqual([s3.spotlight.on, s3.spotlight.color_temp_kelvin], [true, 3500]); near(s3.spotlight.brightness, 0.8);
  assert.deepEqual(f.room.preview.interactionDescriptor(F3).target.point, [2, 2, 0.8]);
  assert.equal(f.room.preview.interactionDescriptor(F3).target.kind, 'lighting_brain_target');
  assert.equal(s4.spotlight.on, false); near(s4.radials[0].brightness, 0.3);
  assert.equal(f.view().why, 'Reading at sofa · spotlight on');
  assert.ok(f.applied.includes(F3));

  // Heartbeat pushes of the same decision never re-impose it.
  const calls = f.room.calls.set;
  await f.brain.push({ ...f.brain.state, generated_at: 1015 });
  await f.t.advance(3000);
  assert.equal(f.room.calls.set, calls);

  // Reading stops: the spot fades out over 1.5 s, ambient stays.
  await f.brain.push(brainState({ at: 1030, mode: 'ambient', why: 'Sofa · ambient', fixtures: { [F3]: AMBIENT, [F4]: AMBIENT } }));
  await f.t.advance(800);
  assert.equal(f.room.state(F3).spotlight.on, true, 'still fading');
  await f.t.advance(1000);
  assert.equal(f.room.state(F3).spotlight.on, false); near(f.room.state(F3).radials[0].brightness, 0.3);
  f.wired.dispose(); assert.equal(f.t.size(), 0);
});

test('brain offline after the heartbeat window preserves ambient and disables automatic spots', async () => {
  const f = await following(brainState({ mode: 'ambient', why: 'Sofa · ambient', fixtures: { [F3]: AMBIENT } }));
  await f.t.advance(3500);
  const held = JSON.stringify(f.room.preview.readback().fixtures.map(row => row.state)), calls = f.room.calls.set;
  f.brain.getStatus = 0;
  await f.t.advance(Client.HEARTBEAT_MS + 10);
  const offlineCalls = f.room.calls.set;
  assert.equal(offlineCalls, calls + 4, 'one conservative spotlight stop per automatic fixture');
  assert.equal(f.view().online, false); assert.equal(f.view().status, 'offline');
  assert.equal(f.view().why, 'Sofa · ambient', 'last why kept');
  assert.equal(f.view().fixtures.length, 4, 'last state kept for display');
  // A refused document while offline never applies or revives the brain.
  await f.brain.pushRaw(JSON.stringify({ ...brainState({ at: 2000, mode: 'reading', fixtures: { [F1]: READING } }), schema: 'nope' }));
  await f.t.advance(5000);
  assert.equal(f.view().online, false); assert.equal(f.view().refused_reason, 'state_schema_unsupported');
  assert.equal(JSON.stringify(f.room.preview.readback().fixtures.map(row => row.state)), held);
  assert.equal(f.room.calls.set, offlineCalls);
  // A brain that reports itself not online is not followed either.
  await f.brain.push(brainState({ at: 2100, mode: 'reading', brain: 'degraded', fixtures: { [F1]: READING } }));
  await f.t.advance(3000);
  assert.equal(f.view().online, false); assert.equal(f.room.calls.set, offlineCalls);
  f.wired.dispose();
});

test('stream loss reconnects with backoff and resumes following', async () => {
  const f = await following(brainState());
  assert.equal(f.brain.sources.length, 1); assert.equal(f.brain.sources[0].url, `${BASE}/stream`);
  f.brain.getStatus = 503;
  f.brain.sources[0].onerror(); assert.equal(f.brain.sources[0].closed, true);
  await f.t.advance(999); assert.equal(f.brain.sources.length, 1);
  await f.t.advance(1); assert.equal(f.brain.sources.length, 2, 'first retry after 1 s');
  f.brain.live().onerror();
  await f.t.advance(1999); assert.equal(f.brain.sources.length, 2);
  await f.t.advance(1); assert.equal(f.brain.sources.length, 3, 'second retry after 2 s');
  f.brain.getStatus = 200;
  await f.brain.push(brainState({ at: 1100, mode: 'ambient', why: 'Sofa · ambient', fixtures: { [F2]: AMBIENT } }));
  assert.equal(f.view().online, true);
  await f.t.advance(2100);
  near(f.room.state(F2).radials[0].brightness, 0.3);
  // Backoff resets after a good document.
  f.brain.live().onerror();
  await f.t.advance(1000); assert.equal(f.brain.sources.length, 4);
  const gets = f.brain.requests.filter(r => r.method === 'GET');
  assert.ok(gets.length >= 4 && gets.every(r => r.url === `${BASE}/state`));
  f.wired.dispose(); assert.equal(f.t.size(), 0);
});

/* ---------- overrides ---------- */
test('failed or missing aim keeps the automatic spotlight off while ambient still applies', async () => {
  const f = await following(brainState({ mode: 'reading', fixtures: { [F3]: READING } }), {
    diagnostics: true, configure: room => { room.overlay.aimVirtualFixtureAtTarget = () => ({ ok: false, reason: 'target_out_of_range' }); },
  });
  assert.equal(f.room.state(F3).spotlight.on, false, 'a previously on spot is disabled immediately');
  await f.t.advance(2100);
  near(f.room.state(F3).radials[0].brightness, .3);
  const row = f.view().fixtures.find(row => row.fixture_id === F3);
  assert.equal(row.requested_spot_on, true); assert.equal(row.spot_on, false);
  assert.equal(row.application_error, 'target_out_of_range');
  assert.match(f.view().notice, /Spotlight withheld/);
  assert.ok(f.wired.follower.diagnosticReadback().events.some(e => e.stage === 'aim_refused' && e.fixture_id === F3));
  f.wired.dispose();
});

test('fixture write refusal is never remembered as successfully applied and is retried', async () => {
  let reject = true;
  const f = await following(brainState({ mode: 'reading', fixtures: { [F3]: READING } }), {
    diagnostics: true, configure: room => {
      const original = room.overlay.setVirtualFixtureState;
      room.overlay.setVirtualFixtureState = (id, patch) => id === F3 && reject ? { ok: false, reason: 'write_refused' } : original(id, patch);
    },
  });
  await f.t.advance(2100);
  assert.equal(f.applied.includes(F3), false);
  assert.equal(f.wired.follower.diagnosticReadback().events.some(e => e.fixture_id === F3 && e.stage === 'applied'), false);
  reject = false;
  await f.brain.push({ ...f.brain.state, generated_at: 1010 });
  await f.t.advance(2100);
  assert.equal(f.applied.includes(F3), true); assert.equal(f.room.state(F3).spotlight.on, true);
  f.wired.dispose();
});

test('stream disconnect disables automatic spots immediately and preserves manual choice', async () => {
  const f = await following(brainState({ mode: 'reading', fixtures: { [F3]: READING, [F4]: READING } }));
  await f.t.advance(2100);
  f.wired.follower.handChange(F4); await pause();
  const staleStream = f.brain.live();
  staleStream.onerror();
  assert.equal(f.view().online, false); assert.equal(f.view().reason, 'stream_disconnected');
  assert.equal(f.room.state(F3).spotlight.on, false); assert.equal(f.room.state(F4).spotlight.on, true);
  staleStream.onmessage({ data: JSON.stringify(brainState({ at: 1200, mode: 'reading', fixtures: { [F3]: READING } })) });
  assert.equal(f.view().online, false, 'late events from a retired stream cannot revive following');
  f.wired.dispose();
});

test('a replayed state cannot refresh original freshness and a retired GET cannot revive the client', async () => {
  const t = clock(), brain = mockBrain(brainState({ at: 1 }));
  const client = Client.createBrainClient({ fetch: brain.fetch, EventSource: brain.EventSource,
    setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, nowMs: () => 1000 + t.nowMs() });
  client.start(); await pause();
  await t.advance(10000); await brain.push(brain.state);
  assert.equal(client.readback().online, true);
  await t.advance(5000);
  assert.equal(client.readback().online, false, 'original timestamp expiry is not extended by arrival');
  await t.advance(1001);
  assert.equal(client.readback().refused_reason, 'state_expired');
  client.stop();

  let response;
  const b = mockBrain();
  const late = Client.createBrainClient({ fetch: () => new Promise(resolve => { response = resolve; }), EventSource: b.EventSource,
    setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, nowMs: t.nowMs });
  late.start(); b.live().onerror();
  response({ ok: true, text: async () => JSON.stringify(brainState()) }); await pause(); await pause();
  assert.equal(late.readback().online, false); late.stop();
});

test('active spots require bound source evidence; original source expiry stops silent-stream authority', async () => {
  const legacy = brainState({ mode: 'reading', fixtures: { [F3]: READING } }); delete legacy.reading_evidence;
  assert.equal(Client.validateState(legacy).reason, 'state_reading_evidence_unbound');
  const ambient = brainState({ mode: 'ambient' }); delete ambient.reading_evidence;
  assert.equal(Client.validateState(ambient).ok, true, 'legacy ambient remains usable');
  const t = clock(), state = brainState({ at: 1, mode: 'reading', fixtures: { [F3]: READING } });
  state.reading_evidence.activity_valid_until = 3; state.reading_evidence.pose_valid_until = 4;
  const brain = mockBrain(state), client = Client.createBrainClient({ fetch: brain.fetch, EventSource: brain.EventSource,
    setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, nowMs: () => 1000 + t.nowMs() });
  client.start(); await pause(); assert.equal(client.readback().online, true);
  await t.advance(1000); await brain.push({ ...state, generated_at: 2 });
  await t.advance(999); assert.equal(client.readback().online, true);
  await t.advance(1); assert.equal(client.readback().online, false); assert.equal(client.readback().reason, 'state_expired');
  client.stop();
});

test('source restart resets revision scope and a stale response body cannot revive a retired connection', async () => {
  const t = clock(), brain = mockBrain({ ...brainState(), decision_id: 'epoch-one:20', state_revision: 20 });
  const client = Client.createBrainClient({ fetch: brain.fetch, EventSource: brain.EventSource,
    setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, nowMs: t.nowMs });
  client.start(); await pause();
  await brain.push({ ...brainState({ at: 1010 }), decision_id: 'epoch-one:19', state_revision: 19 });
  assert.equal(client.readback().state.state_revision, 20, 'earlier same-process revision is refused');
  await brain.push({ ...brainState({ at: 1020 }), decision_id: 'epoch-two:1', state_revision: 1 });
  assert.equal(client.readback().state.decision_id, 'epoch-two:1', 'a valid new process epoch can resume'); client.stop();
  let resolveBody;
  const deferred = mockBrain(), late = Client.createBrainClient({
    fetch: async () => ({ ok: true, text: () => new Promise(resolve => { resolveBody = resolve; }) }),
    EventSource: deferred.EventSource, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout, nowMs: t.nowMs });
  late.start(); await pause(); deferred.live().onerror(); resolveBody(JSON.stringify(brainState()));
  await pause(); assert.equal(late.readback().online, false); late.stop();
});

test('generation changes stop an old fade and refuse old-generation writes before refresh', async () => {
  const f = await following(brainState({ mode: 'reading', fixtures: { [F3]: READING } }));
  await f.t.advance(300);
  const generation = f.room.preview.interactionDescriptor(F3).generation;
  f.room.preview.setDocument(null); f.room.preview.setDocument(Core.toDocument(registrationModel()));
  const before = copy(f.room.state(F3));
  const refused = f.room.preview.setFixtureState(F3, { spotlight: { on: false } }, { expectedGeneration: generation });
  assert.equal(refused.ok, false); assert.equal(refused.reason, 'equipment_preview_generation_changed');
  await f.t.advance(300);
  assert.deepEqual(f.room.state(F3), before, 'the retired fade never writes the replacement generation');
  assert.equal(f.view().fixtures.find(row => row.fixture_id === F3).application_error, 'equipment_preview_generation_changed');
  f.wired.follower.refresh(); await f.t.advance(2100);
  near(f.room.state(F3).spotlight.brightness, .8); f.wired.dispose();
});

test('diagnostics correlate applied and GPU-completed frames, and discard manual-takeover receipts', async () => {
  const pending = [];
  const f = await following({ ...brainState({ mode: 'reading', fixtures: { [F3]: READING } }), state_revision: 7,
    reading_evidence: { bound: true, activity: { session_id: 'test-session' }, pose: { session_id: 'test-session' },
      activity_valid_until: 1010, pose_valid_until: 1010 } }, {
    diagnostics: true, requestRenderEvidence: context => new Promise(resolve => pending.push({ context, resolve })),
  });
  f.wired.follower.annotateActivity({ phase: 'onset', at_ms: 12 });
  await f.t.advance(3100);
  const target = pending.find(row => row.context.fixture_id === F3);
  assert.equal(target.context.state_revision, 7); assert.equal(target.context.reading_evidence.activity.session_id, 'test-session');
  assert.equal(f.view().fixtures.find(row => row.fixture_id === F3).rendered, null, 'state application is not a render');
  target.resolve({ ok: true, stage: 'gpu_completed_frame', frame_id: 4, gpu_completed_at_ms: 3200 }); await pause();
  const rendered = f.view().fixtures.find(row => row.fixture_id === F3).rendered;
  assert.equal(rendered.state_revision, 7); assert.equal(rendered.frame_id, 4); assert.equal(rendered.presentation_observed, false);
  const other = pending.find(row => row.context.fixture_id === F4);
  f.wired.follower.handChange(F4);
  other.resolve({ ok: true, stage: 'gpu_completed_frame', frame_id: 5 }); await pause();
  assert.equal(f.view().fixtures.find(row => row.fixture_id === F4).rendered, null);
  const events = f.wired.follower.diagnosticReadback().events;
  assert.ok(events.some(row => row.stage === 'independent_annotation' && row.at_ms === 12));
  assert.ok(events.some(row => row.stage === 'applied' && row.fixture_id === F3));
  assert.ok(events.some(row => row.stage === 'gpu_completed_frame' && row.fixture_id === F3));
  f.wired.dispose();
});

test('an unchanged desired output cannot renew the source expiry captured by an old render attempt', async () => {
  const pending = [], initial = brainState({ at: 1, mode: 'reading', fixtures: { [F3]: READING } });
  initial.reading_evidence.activity_valid_until = 3; initial.reading_evidence.pose_valid_until = 3;
  const f = await following(initial, { diagnostics: true,
    requestRenderEvidence: context => new Promise(resolve => pending.push({ context, resolve })) });
  await f.t.advance(2500);
  const receipt = pending.find(row => row.context.fixture_id === F3); assert.ok(receipt);
  const refreshed = brainState({ at: 2, mode: 'reading', fixtures: { [F3]: READING } });
  refreshed.reading_evidence.activity_valid_until = 10; refreshed.reading_evidence.pose_valid_until = 10;
  await f.brain.push(refreshed); await f.t.advance(600); assert.equal(f.view().online, true);
  receipt.resolve({ ok: true, stage: 'gpu_completed_frame', frame_id: 9 }); await pause();
  assert.equal(f.view().fixtures.find(row => row.fixture_id === F3).rendered, null);
  f.wired.dispose();
});

test('a manual change pauses only that fixture, posts the override and holds until the brain clears it', async () => {
  const f = await following(brainState({ mode: 'ambient', fixtures: { [F3]: AMBIENT, [F4]: AMBIENT } }));
  await f.t.advance(2500);
  f.wired.follower.handChange(F3, { reason: 'change' });
  f.room.overlay.setVirtualFixtureState(F3, { radials: [{ number: 1, brightness: 1 }] });
  await pause(); await pause();
  const post = f.brain.requests.find(r => r.method === 'POST');
  assert.equal(post.url, `${BASE}/overrides`);
  assert.equal(post.headers['X-Home-Lighting-Brain-CSRF'], '1');
  assert.deepEqual({ ...post.body, at: 0 }, { schema: 'activity-lighting-override-1', fixture_id: F3, reason: 'change', at: 0 });
  assert.deepEqual(f.view().fixtures.map(row => row.auto), [true, true, false, true]);
  assert.equal(f.view().fixtures[2].override_sent, true);
  // Repeated aim steps of one drag post once.
  f.wired.follower.handChange(F4, { reason: 'aim' }); f.wired.follower.handChange(F4, { reason: 'aim' });
  await pause(); await pause();
  assert.equal(f.brain.requests.filter(r => r.method === 'POST').length, 2);
  f.wired.follower.returnToAuto(F4); await pause(); await pause();

  // The brain moves every fixture; the paused one is untouched.
  const bright = { ambient: { radial_level: 0.6, down_level: 0.6, cct_k: 3000, fade_s: 0 } };
  await f.brain.push(brainState({ at: 1100, mode: 'ambient', fixtures: { [F1]: bright, [F2]: bright, [F3]: bright, [F4]: bright } }));
  await f.t.advance(10);
  near(f.room.state(F1).radials[0].brightness, 0.6); near(f.room.state(F4).radials[0].brightness, 0.6);
  assert.equal(f.room.state(F3).radials[0].brightness, 1); near(f.room.state(F3).radials[1].brightness, 0.3);
  // Acknowledged by the brain, still paused; then the brain clears it (room empty / 30 min).
  await f.brain.push(brainState({ at: 1110, mode: 'ambient',
    fixtures: { [F1]: bright, [F2]: bright, [F3]: { ...bright, paused_until: 2900, pause_reason: 'manual_change' }, [F4]: bright } }));
  assert.equal(f.view().fixtures[2].paused, 'manual'); assert.equal(f.room.state(F3).radials[0].brightness, 1);
  await f.brain.push(brainState({ at: 1120, mode: 'ambient', fixtures: { [F1]: bright, [F2]: bright, [F3]: bright, [F4]: bright } }));
  await f.t.advance(10);
  assert.equal(f.view().fixtures[2].auto, true); near(f.room.state(F3).radials[0].brightness, 0.6);
  f.wired.dispose();
});

test('Return to Auto sends DELETE and the fixture follows the brain again once it reports the pause cleared', async () => {
  const f = await following(brainState({ mode: 'ambient', fixtures: { [F1]: AMBIENT } }));
  await f.t.advance(2500);
  f.wired.follower.handChange(F1, { reason: 'undo' }); await pause(); await pause();
  await f.brain.push(brainState({ at: 1100, mode: 'ambient', fixtures: { [F1]: { ...READING, paused_until: 2900, pause_reason: 'manual_undo' } } }));
  assert.equal(f.room.state(F1).spotlight.on, false, 'paused fixture not driven');
  await f.wired.follower.returnToAuto(F1);
  const del = f.brain.requests.find(r => r.method === 'DELETE');
  assert.equal(del.url, `${BASE}/overrides/${F1}`);
  assert.equal(f.view().fixtures[0].paused, 'brain', 'still paused until the brain reports it cleared');
  await f.brain.push(brainState({ at: 1110, mode: 'reading', fixtures: { [F1]: READING } }));
  await f.t.advance(2100);
  assert.equal(f.view().fixtures[0].auto, true);
  assert.equal(f.room.state(F1).spotlight.on, true); assert.deepEqual(f.room.preview.interactionDescriptor(F1).target.point, [2, 2, 0.8]);
  // A failed override is kept locally (never fights the hand change) and reported.
  f.brain.overrideStatus = 503;
  f.wired.follower.handChange(F2, { reason: 'redo' }); await pause(); await pause();
  assert.equal(f.view().fixtures[1].override_error, 'override_http_503'); assert.equal(f.view().fixtures[1].auto, false);
  f.wired.dispose();
});

test('a hand change while the brain is offline stays paused locally and is sent when the brain is back', async () => {
  const f = await following(brainState({ mode: 'ambient', fixtures: { [F3]: AMBIENT } }));
  f.brain.getStatus = 0;
  await f.t.advance(Client.HEARTBEAT_MS + 10);
  assert.equal(f.view().online, false);
  f.wired.follower.handChange(F3, { reason: 'aim' }); f.wired.follower.handChange(F1, { reason: 'change' });
  await pause(); await pause();
  assert.equal(f.brain.requests.some(r => r.method !== 'GET'), false, 'no override reaches an offline brain');
  assert.deepEqual(f.view().fixtures.map(row => row.auto), [false, true, false, true]);
  // Return to Auto for a pause the brain never saw needs no request.
  await f.wired.follower.returnToAuto(F1);
  assert.equal(f.brain.requests.some(r => r.method !== 'GET'), false); assert.equal(f.view().fixtures[0].auto, true);
  f.brain.getStatus = 200;
  await f.t.advance(1000);
  await f.brain.push(brainState({ at: 1200, mode: 'ambient', fixtures: { [F3]: AMBIENT } }));
  await pause(); await pause();
  const posts = f.brain.requests.filter(r => r.method === 'POST');
  assert.deepEqual(posts.map(r => [r.body.fixture_id, r.body.reason]), [[F3, 'aim']]);
  assert.equal(posts[0].body.at, (Client.HEARTBEAT_MS + 10) / 1000, 'the override carries the hand-change time, not the send time');
  assert.equal(f.view().fixtures[2].override_sent, true); assert.equal(f.view().fixtures[2].auto, false);
  f.wired.dispose();
});

/* ---------- panel ---------- */
async function panel(brain, onReturnToAuto = () => {}) {
  const { JSDOM } = require('jsdom'), React = require('react'), Babel = require('../app/src/vendor/babel-7.29.0/babel.min.js');
  const dom = new JSDOM('<div id="root"></div>', { pretendToBeVisual: true, runScripts: 'outside-only', url: 'http://localhost/' }), w = dom.window;
  const keys = ['window', 'document', 'navigator', 'IS_REACT_ACT_ENVIRONMENT'], old = Object.fromEntries(keys.map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  for (const [k, v] of Object.entries({ window: w, document: w.document, navigator: w.navigator, IS_REACT_ACT_ENVIRONMENT: true }))
    Object.defineProperty(globalThis, k, { value: v, writable: true, configurable: true });
  w.React = React;
  vm.runInContext(Babel.transform(fs.readFileSync(path.join(SRC, 'home-lighting-brain-panel.jsx'), 'utf8'), { presets: ['react'] }).code, dom.getInternalVMContext());
  const { createRoot } = require('react-dom/client'), root = createRoot(w.document.getElementById('root'));
  const render = async (value, ops = null) => { await React.act(async () => { root.render(React.createElement(w.HomeLightingBrainPanel, { brain: value, ops, onReturnToAuto,
    labels: { [F1]: 'Mount 1', [F2]: 'Mount 2', [F3]: 'Mount 3', [F4]: 'Mount 4' } })); await pause(); }); };
  await render(brain);
  return { w, render, React, cleanup: async () => {
    await React.act(async () => { root.unmount(); await pause(); }); w.close();
    for (const [k, d] of Object.entries(old)) if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k];
  } };
}

test('the why line, brain status, Auto/Paused markers and the last-20 decision log render', async () => {
  const decisions = Array.from({ length: 20 }, (_, i) => ({ at: 1000 - i, rule: i ? 'zone_ambient' : 'reading_spot_on',
    why: i ? 'Sofa occupied' : 'Reading at sofa', inputs: {}, change: i ? null : 'mount 3 spot on' }));
  const f = await following(brainState({ at: 1000, mode: 'reading', why: 'Reading at sofa · spotlight on', decisions, fixtures: { [F3]: READING } }));
  f.wired.follower.handChange(F2, { reason: 'aim' }); await pause(); await pause();
  const returned = [], p = await panel(f.view(), id => returned.push(id));
  try {
    const doc = p.w.document;
    assert.equal(doc.querySelector('[data-lighting-brain-why]').textContent, 'Brain decision: Reading at sofa · spotlight on');
    assert.match(doc.querySelector('[data-lighting-brain-online]').textContent, /^Brain online · reading/);
    assert.equal(doc.querySelectorAll('[data-lighting-brain-decision]').length, 20);
    assert.equal(doc.querySelector('[data-lighting-brain-decision]').getAttribute('data-lighting-brain-decision'), 'reading_spot_on');
    assert.match(doc.querySelector('[data-lighting-brain-log] summary').textContent, /Decisions \(20\)/);
    assert.match(doc.querySelector('[data-lighting-brain-log]').textContent, /Reading at sofa · mount 3 spot on/);
    assert.deepEqual([...doc.querySelectorAll('[data-lighting-brain-auto]')].map(n => n.getAttribute('data-lighting-brain-auto')), ['1', '0', '1', '1']);
    const back = doc.querySelector('button[aria-label="Return Mount 2 to Auto"]');
    await p.React.act(async () => { back.click(); await pause(); });
    assert.deepEqual(returned, [F2]);
    f.brain.getStatus = 0;
    await f.t.advance(Client.HEARTBEAT_MS + 10); await p.render(f.view());
    assert.match(doc.querySelector('[data-lighting-brain-online]').textContent, /Brain offline · automatic following stopped/);
    assert.equal(doc.querySelector('[data-lighting-brain-why]').textContent, 'Brain decision: Reading at sofa · spotlight on');
  } finally { await p.cleanup(); f.wired.dispose(); }
});

/* ---------- AL-5 always-on monitor banner ---------- */
function opsStatus({ level = 0, banner = null, stale = false } = {}) {
  return { schema: 'den-ops-banner-1', generated_at: 1800000000, level, step: ['normal', 'cadence_reduced', 'deep_stack_paused', 'loop_stopped'][level],
    banner, hosts: [{ host: 'la-host', level, stale }] };
}
function opsServer() {
  const server = { doc: opsStatus(), status: 200, calls: [] };
  server.fetch = async (url, options = {}) => {
    server.calls.push({ url, method: options.method || 'GET' });
    if (server.status === 0) throw Error('network');
    return { ok: server.status === 200, status: server.status, text: async () => typeof server.doc === 'string' ? server.doc : JSON.stringify(server.doc) };
  };
  return server;
}

test('the monitor banner shows warn and alert levels and a silent monitor, and shows nothing for ok, notices or failures', async () => {
  const t = clock(), server = opsServer(), seen = [];
  let hidden = false;
  const poller = Client.createOpsStatusPoller({ fetch: server.fetch, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout,
    isHidden: () => hidden, onChange: value => seen.push(value) });
  poller.start(); await pause();
  assert.deepEqual(server.calls, [{ url: '/api/den-ops/v1/status', method: 'GET' }]);
  assert.equal(poller.readback(), null, 'level ok shows no banner');

  server.doc = opsStatus({ level: 1, banner: { severity: 'warn', host: 'la-host', text: 'LA GPU hot · JEV cadence reduced' } });
  await t.advance(Client.OPS_POLL_MS); assert.equal(server.calls.length, 2, 'polls every 30 s');
  assert.deepEqual(poller.readback(), { severity: 'warn', host: 'la-host', text: 'LA GPU hot · JEV cadence reduced', level: 1, step: 'cadence_reduced' });

  server.doc = opsStatus({ level: 3, banner: { severity: 'alert', host: 'victoria-jetson', text: 'Activity loop stopped on victoria-jetson' } });
  await t.advance(Client.OPS_POLL_MS); assert.equal(poller.readback().severity, 'alert');

  server.doc = opsStatus({ level: 0, stale: true, banner: { severity: 'warn', host: 'victoria-jetson', text: 'Ops monitor silent on victoria-jetson' } });
  await t.advance(Client.OPS_POLL_MS); assert.equal(poller.readback().text, 'Ops monitor silent on victoria-jetson', 'a silent monitor counts');

  server.doc = opsStatus({ level: 0, banner: { severity: 'notice', host: 'la-host', text: 'Back to normal' } });
  await t.advance(Client.OPS_POLL_MS); assert.equal(poller.readback(), null, 'a notice at level ok is not a banner');

  for (const failure of [() => { server.status = 503; }, () => { server.status = 0; }, () => { server.status = 200; server.doc = '{oops'; },
    () => { server.doc = { ...opsStatus({ level: 2, banner: { severity: 'alert', host: 'la-host', text: 'x' } }), schema: 'other' }; }]) {
    server.status = 200; server.doc = opsStatus({ level: 1, banner: { severity: 'warn', host: 'la-host', text: 'Memory high' } });
    await t.advance(Client.OPS_POLL_MS); assert.equal(poller.readback().text, 'Memory high');
    failure(); await t.advance(Client.OPS_POLL_MS); assert.equal(poller.readback(), null, 'a failed read shows nothing, not an error');
  }

  // Hidden tab: no polling; becoming visible polls at once.
  server.status = 200; server.doc = opsStatus({ level: 1, banner: { severity: 'warn', host: 'la-host', text: 'Memory high' } });
  hidden = true; const before = server.calls.length;
  await t.advance(Client.OPS_POLL_MS * 4); assert.equal(server.calls.length, before);
  hidden = false; poller.wake(); await pause(); await pause();
  assert.equal(server.calls.length, before + 1); assert.equal(poller.readback().text, 'Memory high');
  poller.stop(); assert.equal(t.size(), 0); assert.equal(poller.readback(), null);
  assert.ok(seen.length >= 6);
});

test('the Victoria panel shows the monitor banner beside the why line', async () => {
  const f = await following(brainState({ mode: 'reading', why: 'Reading at sofa · spotlight on', fixtures: { [F3]: READING } }));
  const p = await panel(f.view());
  try {
    const doc = p.w.document;
    assert.equal(doc.querySelector('[data-den-ops-banner]'), null, 'no banner while the monitor is ok');
    const render = ops => p.render(f.view(), ops);
    await render({ severity: 'alert', host: 'la-host', text: 'Deep stack paused: LA GPU at 88 °C', level: 2, step: 'deep_stack_paused' });
    const banner = doc.querySelector('[data-den-ops-banner]');
    assert.equal(banner.getAttribute('data-den-ops-banner'), 'alert');
    assert.equal(banner.textContent, 'Deep stack paused: LA GPU at 88 °C · la-host');
    assert.equal(banner.nextElementSibling.getAttribute('data-lighting-brain-why'), '1', 'banner sits next to the why line');
    await render({ severity: 'warn', host: 'victoria-jetson', text: 'Ops monitor silent on victoria-jetson', level: 0, step: 'normal' });
    assert.equal(doc.querySelector('[data-den-ops-banner]').textContent, 'Ops monitor silent on victoria-jetson');
    await render(null); assert.equal(doc.querySelector('[data-den-ops-banner]'), null);
    const apartment = fs.readFileSync(path.join(SRC, 'home-apartment.jsx'), 'utf8');
    assert.match(apartment, /Client\.createOpsStatusPoller\(\{[\s\S]{0,300}isHidden: \(\) => document\.hidden === true, onChange: setDenOpsBanner \}\)/);
    assert.match(apartment, /<window\.HomeLightingBrainPanel brain=\{lightingBrain\} ops=\{denOpsBanner\}/);
  } finally { await p.cleanup(); f.wired.dispose(); }
});

/* ---------- sofa mark ---------- */
function sofaInput() {
  const binding = { content_revision: 'authored-sofa-room', registration_version: 1,
    manifest_digest: hash('a'), frame_digest: hash('b'), canonical_collision_digest: hash('c') };
  const corners = [[1, 1, .45], [2.5, 1, .45], [2.5, 1.9, .47], [1, 1.9, .46]].map(point_m => ({ point_m, normal_unit: [0, 0, 1] }));
  return { binding, equipment_ref: { revision: 2, digest: hash('e') }, corners, confirmed: true };
}
const seal = { sealDesk: sealRegisteredDeskTarget, digest: Core.digest };

test('the sofa mark seals through the desk selector, round-trips and exports privately for the brain', () => {
  const input = sofaInput(), record = Sofa.sealSofaTarget(input, seal);
  assert.equal(record.schema, 'home-registered-sofa-target-1');
  assert.equal(record.zone.vertices_m.length, 4); assert.deepEqual(record.seat.point_m, record.zone.centre_m);
  assert.equal(record.physical_commands, false);
  const reread = JSON.parse(JSON.stringify(record));
  assert.deepEqual(Sofa.validateSofaTarget(reread, { binding: input.binding, equipment_ref: input.equipment_ref }, seal), record);
  const moved = copy(record); moved.seat.point_m[0] += .2;
  assert.throws(() => Sofa.validateSofaTarget(moved, { binding: input.binding, equipment_ref: input.equipment_ref }, seal), /sofa_target_changed/);
  assert.throws(() => Sofa.validateSofaTarget(record, { binding: { ...input.binding, registration_version: 2 }, equipment_ref: input.equipment_ref }, seal),
    /sofa_target_binding_changed/);
  assert.throws(() => Sofa.sealSofaTarget({ ...input, confirmed: false }, seal), /confirmation_required/);

});

/* ---------- private lighting-brain-geometry-1 export ---------- */
// The shape AL-1's parse_geometry accepts (stack/services/lighting-brain,
// config/geometry.example.json): extra keys are allowed, limits are closed.
function assertGeometryShape(g) {
  const vec = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
  assert.equal(g.schema, 'lighting-brain-geometry-1');
  for (const key of ['home', 'room', 'frame']) assert.ok(typeof g[key] === 'string' && g[key], key);
  assert.deepEqual([g.home, g.room, g.frame], ['victoria', 'den', 'victoria-den']);
  assert.ok(g.camera_m === null || vec(g.camera_m), 'camera_m');
  assert.deepEqual(g.fixtures.map(f => f.fixture_id), Client.FIXTURE_IDS);
  for (const f of g.fixtures) {
    assert.ok(f.aim_origin_m === null || vec(f.aim_origin_m), f.fixture_id);
    assert.ok(vec(f.horizontal_world_reference) && Math.hypot(...f.horizontal_world_reference.slice(0, 2)) > 1e-6);
    if (f.limits) assert.deepEqual(Object.keys(f.limits).sort(), ['pan_max_deg', 'pan_min_deg', 'tilt_max_deg', 'tilt_min_deg']);
  }
  assert.deepEqual(Object.keys(g.zones).sort(), ['desk', 'sofa']);
  for (const zone of Object.values(g.zones)) {
    assert.ok(zone.seat_m === null || vec(zone.seat_m));
    assert.ok(zone.near_fixtures.length && zone.near_fixtures.every(id => Client.FIXTURE_IDS.includes(id)));
  }
}
function registrationFor(options) { return Core.sealDocument({ ...Core.normalizeDocument(Core.toDocument(registrationModel(options))), revision: 2 }); }
function marksFor(registration) {
  const scope = { binding: registration.binding, equipment_ref: { revision: registration.revision, digest: registration.digest }, confirmed: true };
  const sofa = Sofa.sealSofaTarget({ ...scope, corners: sofaInput().corners }, seal);
  const desk = sealRegisteredDeskTarget({ ...scope, corners: [[3, 3, .75], [3.8, 3, .75], [3.8, 3.6, .75], [3, 3.6, .75]]
    .map(point_m => ({ point_m, normal_unit: [0, 0, 1] })) }, Core.digest);
  return { sofa, desk };
}

test('a synthetic registration and the owner marks export one valid lighting-brain-geometry-1 file', () => {
  const registration = registrationFor(), { sofa, desk } = marksFor(registration);
  assert.equal(Core.validateDocument(registration).ok, true);
  const { geometry: g, near_check } = buildBrainGeometry({ sofa, desk, registration, generatedAt: 1800000000 });
  assertGeometryShape(g);
  // The fixtures are exactly Home's own aimAtTarget descriptors for this registration.
  const preview = createEquipmentVirtualFixtures(new THREE.Group(), { binding: registration.binding }); preview.setDocument(registration);
  for (const f of g.fixtures) {
    const d = preview.interactionDescriptor(f.fixture_id);
    assert.deepEqual(f.aim_origin_m, d.display_origin_m); assert.deepEqual(f.horizontal_world_reference, d.horizontal_world_reference);
    // The brain's pan/tilt from these values matches what the renderer solves.
    const target = [1.75, 1.45, .6], dir = target.map((v, i) => v - f.aim_origin_m[i]), ref = f.horizontal_world_reference;
    const pan = Math.atan2(dir[0] * ref[1] - dir[1] * ref[0], dir[0] * ref[0] + dir[1] * ref[1]) * 180 / Math.PI;
    const tilt = Math.atan2(-dir[2], Math.hypot(dir[0], dir[1])) * 180 / Math.PI;
    const aimed = preview.aimAtTarget(f.fixture_id, { point: target, normal: [0, 0, 1] }).descriptor.state;
    near(aimed.pan_deg, pan, 1e-9); near(aimed.tilt_deg, tilt, 1e-9);
    assert.deepEqual(f.limits, { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90 });
  }
  preview.dispose();
  assert.ok(Array.isArray(g.camera_m), 'camera position comes from the registered OAK-D record');
  assert.deepEqual(g.zones.sofa.seat_m, sofa.seat.point_m); assert.deepEqual(g.zones.sofa.zone_vertices_m, sofa.zone.vertices_m);
  assert.deepEqual(g.zones.sofa.near_fixtures, ['victoria-den-proposed-fixture-02', 'victoria-den-mount-03-virtual-fixture']);
  assert.deepEqual(g.zones.desk.near_fixtures, ['victoria-den-proposed-fixture-01', 'victoria-den-mount-04-virtual-fixture']);
  assert.deepEqual(g.zones.desk.seat_m, desk.target.centre_m);
  assert.deepEqual(g.binding, registration.binding); assert.deepEqual(g.equipment_ref, { revision: 2, digest: registration.digest });
  assert.equal(g.digest, Core.digest(Object.fromEntries(Object.entries(g).filter(([k]) => k !== 'digest'))));
  // In this synthetic room the sofa sits by sockets 1 and 2, so the wall-B default is flagged for the owner.
  assert.equal(near_check.sofa.status, 'differs_from_two_nearest');
  assert.deepEqual([...near_check.sofa.nearest].sort(), ['victoria-den-proposed-fixture-01', 'victoria-den-proposed-fixture-02']);
  const edited = buildBrainGeometry({ sofa, registration, generatedAt: 1, nearFixtures: { sofa: near_check.sofa.nearest } });
  assert.equal(edited.near_check.sofa.status, 'two_nearest');
  assert.equal(edited.geometry.zones.desk.seat_m, null); assert.equal(edited.near_check.desk.status, 'no_seat');
  const bare = registrationFor({ camera: false });
  assert.equal(buildBrainGeometry({ sofa: marksFor(bare).sofa, registration: bare, generatedAt: 1 }).geometry.camera_m, null);
});

test('the geometry export refuses a mismatched registration, changed marks and unknown near fixtures', () => {
  const registration = registrationFor(), { sofa, desk } = marksFor(registration);
  const other = Core.sealDocument({ ...Core.normalizeDocument(registration), revision: 3 });
  assert.throws(() => buildBrainGeometry({ sofa, registration: other, generatedAt: 1 }), /sofa_target_binding_changed/);
  const rebound = copy(registration); rebound.binding.registration_version = 9;
  assert.throws(() => buildBrainGeometry({ sofa, registration: rebound, generatedAt: 1 }), /valid_saved_equipment_required/);
  assert.throws(() => buildBrainGeometry({ sofa, desk: marksFor(other).desk, registration, generatedAt: 1 }), /desk_target_scope_mismatch/);
  const moved = copy(sofa); moved.seat.point_m[1] += .3;
  assert.throws(() => buildBrainGeometry({ sofa: moved, registration, generatedAt: 1 }), /sofa_target_changed/);
  const shifted = copy(desk); shifted.target.centre_m[0] += .3;
  assert.throws(() => buildBrainGeometry({ sofa, desk: shifted, registration, generatedAt: 1 }), /desk_target_changed/);
  assert.throws(() => buildBrainGeometry({ sofa, registration, generatedAt: 1, nearFixtures: { desk: ['victoria-den-mount-09'] } }), /desk_near_fixtures_invalid/);
  assert.deepEqual(parseArgs(['--sofa', 'S', '--equipment', 'E', '--out', 'O', '--sofa-near', 'victoria-den-proposed-fixture-01, victoria-den-proposed-fixture-02']),
    { sofaFile: 'S', equipmentFile: 'E', outputFile: 'O', nearFixtures: { sofa: ['victoria-den-proposed-fixture-01', 'victoria-den-proposed-fixture-02'] } });
  assert.throws(() => parseArgs(['--sofa', 'S', '--out', 'O']), /usage/);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'al4-geometry-'));
  try {
    const write = (name, value) => { const file = path.join(dir, name); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 }); return file; };
    const sofaFile = write('victoria-den-sofa-target.json', sofa), deskFile = write('victoria-den-desk-target.json', desk);
    const equipmentFile = write('equipment.json', { ok: true, registration }), out = path.join(dir, 'lighting-brain-geometry.json');
    const summary = prepareBrainGeometryFile({ sofaFile, deskFile, equipmentFile, outputFile: out, generatedAt: 1800000000 });
    assert.equal(summary.schema, 'lighting-brain-geometry-1'); assert.equal(summary.fixture_count, 4);
    assert.equal(/_m"|point|\[\s*-?\d/.test(JSON.stringify(summary)), false, 'the console summary carries no coordinates');
    const written = JSON.parse(fs.readFileSync(out, 'utf8'));
    assertGeometryShape(written);
    assert.deepEqual(written, buildBrainGeometry({ sofa, desk, registration, generatedAt: 1800000000 }).geometry);
    assert.throws(() => prepareBrainGeometryFile({ sofaFile, equipmentFile, outputFile: out }), /EEXIST/);
    const mismatched = write('other-equipment.json', other);
    assert.throws(() => prepareBrainGeometryFile({ sofaFile, equipmentFile: mismatched, outputFile: path.join(dir, 'never.json') }), /sofa_target_binding_changed/);
    assert.equal(fs.existsSync(path.join(dir, 'never.json')), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

/* ---------- parent wiring and the retired desk preview ---------- */
test('Victoria view wires the brain, the hand-change seam and the sofa mark; the fixed-desk preview is not armed', () => {
  const apartment = fs.readFileSync(path.join(SRC, 'home-apartment.jsx'), 'utf8');
  const perception = fs.readFileSync(path.join(SRC, 'home-apartment-den-perception.jsx'), 'utf8');
  const loader = fs.readFileSync(path.join(SRC, 'home-feature-loader.js'), 'utf8');
  const body = name => { const start = apartment.indexOf(`const ${name} = `); assert.ok(start > 0, name); return apartment.slice(start, start + 1600); };
  // Main's single hand-change seam: every hand path calls it first, and Den
  // binds it to the desk-preview takeover and the brain's handChange.
  assert.match(body('releaseFixtureForHandChange'), /lightingBrainRef\.current\?\.follower\.handChange\(fixtureId, \{ reason: reason \|\| "change" \}\)/);
  assert.match(body('changeVirtualFixtureByHand'), /releaseFixtureForHandChange\(fixtureId, \{ reason: "change" \}\);\s*return changeVirtualFixture\(/);
  assert.match(body('applyAimHistoryState'), /releaseFixtureForHandChange\(id, \{ reason \}\);/);
  assert.match(apartment, /releaseFixtureForHandChange\(id, \{ reason: "aim" \}\);/);
  assert.match(apartment, /Follower\.createBrainFollowing\(/);
  assert.match(apartment, /<window\.HomeLightingBrainPanel brain=\{lightingBrain\} ops=\{denOpsBanner\}/);
  assert.match(apartment, /onClick=\{\(\) => \{ setTargetMarkKind\("sofa"\); startDeskTargetSelection\("sofa"\); \}\}/);
  assert.match(apartment, /"victoria-den-sofa-target\.json"/);
  assert.doesNotMatch(apartment, /legacyDeskAimPreview/);
  assert.match(perception, /legacyDeskAimPreview=false/);
  assert.match(perception, /if\(legacyDeskAimPreview&&global\.HomeApartmentDeskAimPreview\?\.create/);
  const at = name => loader.indexOf(`"${name}"`);
  for (const name of ['home-lighting-brain-client.js', 'home-lighting-brain-follower.js', 'home-lighting-sofa-target.js', 'home-lighting-brain-panel.jsx'])
    assert.ok(at(name) > 0 && at(name) < at('home-apartment.jsx'), name);
});
