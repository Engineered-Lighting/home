import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pipelineCards, pipelinePerceptionRows, pipelineModelRows, pipelineIntentRows, attachDenPipeline } from '../app/src/home-den-pipeline-layer.mjs';
import { createDenSourceClock } from '../app/src/home-den-model-runtime.mjs';

const T0 = 1794600000;
const health = (extra = {}) => ({ fps_in: 2.9, lag_s_median: .42, transit_s_median: .15, dropped_per_min: 1,
  timings_ms: { detector_model: { n: 30, p50: 41, p95: 63 }, detector_turnaround: { n: 30, p50: 57, p95: 82 } },
  qwen: { state: 'asking', why: null, last_reply_at: T0 + 18, reply_s_median: 1.6, asks_per_min: 28 }, ...extra });
const sam = { track_id: 'trk-3', state: 'confirmed', xy_m: [2, 3], sigma_m: .07, service_run_id: 'run-1', camera_epoch: 1, association_revision: 2,
  identity: { kind: 'household', label: 'Sam', score: .93, at: T0 + 10 }, activity: { word: 'reading', close_up: true, at: T0 + 18 } };
const event = (extra = {}) => ({ emitted_ts: T0 + 20, mode: 'rgb', camera: { status: 'ok' }, perception_status: 'fresh', tracks: [sam], health: health(), ...extra });
const spot = (extra = {}) => ({ stopped: false, subject: { track_id: 'trk-3', camera_epoch: 1, association_revision: 2, request_id: 'ask-1' },
  fixtures: [{ fixture_id: 'F3', on: true, level: .7, cct_k: 2700, reason: 'task', motion_phase: 'settled', target_m: [2, 2.7, .8] }], ...extra });
const stage = (extra = {}) => ({ id: 'den-yolo', role: 'detection', state: 'ready', configured: true, provisioned: true,
  loaded: null, device: 'cpu', model_id: 'Ultralytics/YOLO26s', model_revision: 'v26', profile_digest: null,
  queued: 0, running: 0, reason: 'ready', admission_scope: null, observed_mono: 9, valid_until_mono: 12, ...extra });
const stagesEvent = (stages = [stage()], extra = {}) => event({ ...extra,
  health: health({ model_stages: { schema: 'den-model-stages-1', sampled_mono: 10, stages } }) });
const sourceClock = () => createDenSourceClock({ schema: 'den-source-clock-1', clock_domain: 'producer_monotonic', server_mono: 10 },
  { startedAt: 1000, receivedAt: 1100 });
const context = extra => ({ sourceClock: sourceClock(), receivedAt: 1100, now: 1100, ...extra });
const intentReceipt = (extra = {}) => ({ schema: 'den.walking-intent/v1', disposition: 'ignored', selected: false,
  consumed: false, reason: 'awaiting_held_out_admission', authority: 'semantic_prior_only',
  uncertainty: 'uncalibrated_semantic_hint', clock: 'producer_monotonic', observed_at: 9, produced_at: 9.5, expires_at: 12, ...extra });
const receiptSpot = (intent = intentReceipt(), extraEvidence = {}) => spot({ path_lighting: { schema: 'den-path-lighting-1', mode: 'virtual',
  evidence: { intent, ...extraEvidence } } });

test('a live den: each card says one thing, in den-live’s own numbers', () => {
  const c = pipelineCards(event(), 0.2, spot(), { F3: 'Spot 3' });
  assert.deepEqual([c.camera.value, c.camera.sub], ['2.9 fps', '150 ms away']);
  assert.deepEqual([c.perception.value, c.perception.sub], ['1 person', '0.6 s behind']);
  assert.deepEqual([c.qwen.value, c.qwen.sub], ['Reading', '8 s left']);   // answered 2.2 s ago
  assert.deepEqual([c.coordinator.value, c.coordinator.sub, c.coordinator.fixture], ['70%', 'for Sam', 'Spot 3']);
  assert.deepEqual(c.coordinator.target, [2, 2.7, .8]);
});

test('perception details distinguish real producer model and detector round-trip timings', () => {
  const rows = Object.fromEntries(pipelinePerceptionRows(event()));
  assert.equal(rows['Detector model'], '41 ms p50 · 63 ms p95');
  assert.equal(rows['Detector round trip'], '57 ms p50 · 82 ms p95');
  const absent = Object.fromEntries(pipelinePerceptionRows(event({ health: health({
    timings_ms: { detector: { n: 30, p50: 999, p95: 999 } },
  }) })));
  assert.equal(absent['Detector model'], '—');
  assert.equal(absent['Detector round trip'], '—');
});

test('coordinator attribution rejects a colliding track from another epoch, association or service run', () => {
  for (const mismatch of [{ camera_epoch: 2 }, { association_revision: 3 }, { service_run_id: 'run-2' }]) {
    const subject = { ...spot().subject, service_run_id: 'run-1', ...mismatch };
    assert.equal(pipelineCards(event(), 0, spot({ subject })).coordinator.sub, 'for a track no longer seen');
  }
  const subject = { ...spot().subject, service_run_id: 'run-1' };
  assert.equal(pipelineCards(event(), 0, spot({ subject })).coordinator.sub, 'for Sam');
  const collided = { ...sam, camera_epoch: 2, identity: { ...sam.identity, label: 'Other' } };
  assert.equal(pipelineCards(event({ tracks: [collided, sam] }), 0, spot()).coordinator.sub, 'for Sam');
});

test('coordinator attribution verifies supplied binding fields and retains legacy subjects without them', () => {
  for (const field of ['service_run_id', 'camera_epoch', 'association_revision']) {
    const track = { ...sam };
    delete track[field];
    const subject = { ...spot().subject, service_run_id: 'run-1' };
    assert.equal(pipelineCards(event({ tracks: [track] }), 0, spot({ subject })).coordinator.sub,
      'for a track no longer seen');
  }
  assert.equal(pipelineCards(event(), 0, spot({ subject: { track_id: sam.track_id } })).coordinator.sub, 'for Sam');
  for (const malformed of [{ track_id: '' }, { service_run_id: false }, { camera_epoch: true }, { association_revision: '2' }]) {
    const track = { ...sam, ...malformed }, subject = { ...spot().subject, service_run_id: 'run-1', ...malformed };
    assert.equal(pipelineCards(event({ tracks: [track] }), 0, spot({ subject })).coordinator.sub,
      'for a track no longer seen');
  }
});

test('occupancy: Frigate’s own word when den-live sends it, else "Not reported" (never inferred from Qwen’s gate)', () => {
  assert.equal(pipelineCards(event({ health: health({ occupancy: { effective: 'off', why: 'no one' } }) }), 0, null).occupancy.value, 'Empty');
  assert.equal(pipelineCards(event({ health: health({ occupancy: { effective: 'on', why: 'person' } }) }), 0, null).occupancy.value, 'Occupied');
  const absent = pipelineCards(event(), 0, null).occupancy;
  assert.deepEqual([absent.value, absent.state], ['Not reported', 'off']);
});

test('stale, dark, expired and stopped say so instead of the last good value', () => {
  assert.equal(pipelineCards(event(), 4, spot()).camera.value, 'Stream stopped');
  assert.equal(pipelineCards(event({ mode: 'none', camera: { status: 'ok', why: 'too dark' }, tracks: [] }), 0, null).camera.value, 'Too dark');
  assert.equal(pipelineCards(event(), 2.9, spot()).qwen.value, 'Reading', 'still good 4.9 s after the answer, the stream still live');
  assert.notEqual(pipelineCards(event(), 3.1, spot()).qwen.value, 'Reading', 'gone with the stream');
  assert.equal(pipelineCards(event({ emitted_ts: T0 + 29 }), 0, spot()).qwen.value, 'Asking', 'expired 10 s after the answer');
  assert.equal(pipelineCards(event(), 0, spot({ stopped: true })).coordinator.value, 'Stop');
  assert.equal(pipelineCards(event(), 0, null).coordinator.value, 'No snapshot');
});

test('the light direction\u2019s hooks: where a fade is heading, what it does, and the room glow', () => {
  const rising = spot({ ambient: { level: .4, scale: .4, cct_k: 2700 }, fixtures: [{ fixture_id: 'F3', on: true, level: .4, cct_k: 2700, reason: 'task',
    motion_phase: 'settled', target_m: [2, 2.7, .8], fade: { from: 0, to: .79, start_mono: 1, duration_s: 2, curve: 'smooth', purpose: 'fade_in' } }] });
  const c = pipelineCards(event(), 0, rising, { F3: 'Spot 3' }).coordinator;
  assert.equal(c.value, '40% → 79%');
  assert.equal(c.change, 'rising over 2 s');
  assert.equal(c.ambient, '40% of the house · 2700 K');
  const settled = pipelineCards(event(), 0, spot(), { F3: 'Spot 3' }).coordinator;
  assert.deepEqual([settled.value, settled.change, settled.ambient], ['70%', null, null]);
});

test('model details show normalized reported facts without inferring residency or consumption', () => {
  const e = stagesEvent();
  const rows = Object.fromEntries(pipelineModelRows(e, 'perception', context()));
  assert.equal(rows['Model ID'], 'Ultralytics/YOLO26s');
  assert.equal(rows.Revision, 'v26');
  assert.equal(rows.Device, 'cpu');
  assert.equal(rows.Configured, 'Yes');
  assert.equal(rows.Provisioned, 'Yes');
  assert.equal(rows.Loaded, 'Unknown');
  assert.equal(rows.Lifecycle, 'Ready');
  assert.equal(rows.Freshness, 'Fresh');
  assert.equal(rows.Admission, 'Admission unknown');
  assert.equal(rows['Result use'], 'Not reported by model stage');
  assert.equal(Object.fromEntries(pipelinePerceptionRows(e, context()))['Detector model'], '41 ms p50 · 63 ms p95');
});

test('existing stations distinguish planned, bounded trial and explicit production stages', () => {
  const e = stagesEvent([stage({ id: 'planned', reason: 'planned', state: 'not_admitted', admission_scope: 'not_admitted' }),
    stage({ id: 'trial', role: 'motion_intent', state: 'running', running: 1, loaded: true, admission_scope: 'bounded_trial' }),
    stage({ id: 'qwen-policy', role: 'lighting_policy', state: 'externally_managed', loaded: null, admission_scope: 'production' })]);
  const c = pipelineCards(e, .1, spot(), {}, context());
  assert.deepEqual(Object.keys(c), ['camera', 'occupancy', 'perception', 'qwen', 'coordinator']);
  assert.equal(c.perception.modelStages.length, 2);
  assert.match(c.perception.modelSummary, /1 planned.*1 trial\/evaluation/);
  assert.equal(c.qwen.modelStages.length, 1);
  assert.match(c.qwen.modelSummary, /1 production admitted/);
  assert.match(JSON.stringify(pipelineModelRows(e, 'perception', context())), /Bounded trial \/ evaluation/);
  assert.equal(Object.fromEntries(pipelineModelRows(e, 'qwen', context())).Loaded, 'Unknown');
  assert.equal(c.coordinator.sub, 'for Sam', 'runtime metadata never changes the light recipient');
});

test('runtime details use source expiry without extending it on display ticks', () => {
  const e = stagesEvent();
  assert.equal(Object.fromEntries(pipelineModelRows(e, 'perception')).Freshness, 'Unknown');
  assert.equal(Object.fromEntries(pipelineModelRows(e, 'perception', context({ now: 3200 }))).Freshness, 'Stale');
  const c = pipelineCards(e, 2.1, spot(), {}, context({ now: 3200 }));
  assert.match(c.perception.modelSummary, /stale/);
  assert.equal(c.perception.modelStages[0].valid_until_mono, 12);
});

test('pipeline model rows strip private fields and bound malformed model payloads', () => {
  const e = stagesEvent([stage({ model_id: '/home/PRIVATE_MODEL', model_revision: 'https://PRIVATE_REVISION',
    device: 'PRIVATE_HOST', error: 'PRIVATE_ERROR', prompt: 'PRIVATE_PROMPT', clip: 'PRIVATE_CLIP', reason: '/PRIVATE_REASON' })]);
  const rows = pipelineModelRows(e, 'perception', context());
  assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_|\/home\/|https:/);
  assert.equal(Object.fromEntries(rows)['Model ID'], 'Unknown');
  const malformed = stagesEvent(Array.from({ length: 33 }, (_, i) => stage({ id: `stage_${i}` })));
  assert.deepEqual(pipelineModelRows(malformed, 'perception', context()), [['Model metadata', 'Unavailable']]);
});

test('intent use comes only from contracted coordinator receipts, never worker completion or motion use', () => {
  const e = event({ intent_hints: [{ schema: 'den.intent-hint/v1', disposition: 'consumed', consumed: true }],
    health: health({ worker: { state: 'ready', completed: true, consumed: true } }) });
  assert.deepEqual(pipelineIntentRows(null, context()), [['Intent receipt', 'Not reported'], ['Intent consumed', 'Unknown']]);
  assert.equal(Object.hasOwn(pipelineCards(e, 0, null).coordinator, 'consumed'), false);
  const rows = Object.fromEntries(pipelineIntentRows(receiptSpot(intentReceipt(), { motion: { disposition: 'consumed', selected: true } }), context()));
  assert.equal(rows['Intent disposition'], 'Ignored');
  assert.equal(rows['Intent consumed'], 'No · coordinator receipt');
  assert.equal(rows['Intent reason'], 'Awaiting held-out admission');
  assert.equal(rows['Intent freshness'], 'Fresh');
  const future = Object.fromEntries(pipelineIntentRows(receiptSpot(intentReceipt({ disposition: 'consumed', consumed: true, selected: true })), context()));
  assert.equal(future['Intent receipt'], 'Unavailable');
  assert.equal(future['Intent consumed'], 'Unknown');
});

test('coordinator intent diagnostics retain expiry and strip private/unsupported reason strings', () => {
  const rows = Object.fromEntries(pipelineIntentRows(receiptSpot(intentReceipt({ reason: 'PRIVATE_URL/error' }),
    { intent_rejected: intentReceipt({ reason: 'request_id_conflict', private: 'PRIVATE_CLIP' }) }), context({ now: 3200 })));
  assert.equal(rows['Intent freshness'], 'Stale');
  assert.equal(rows['Intent reason'], 'Not reported');
  assert.equal(rows['Declined hint reason'], 'Request identity conflict');
  assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_/);
  assert.equal(Object.fromEntries(pipelineIntentRows(receiptSpot(intentReceipt({ reason: { toString: {}, valueOf: {} } })), context()))['Intent reason'], 'Not reported');
  const future = receiptSpot(intentReceipt({ produced_at: 10.5 }));
  assert.equal(Object.fromEntries(pipelineIntentRows(future, context({ now: 1700 })))['Intent freshness'], 'Unknown',
    'a definitely future receipt cannot mature by display ticking with its original callback timestamp');
});

test('attached pipeline recomputes source freshness on its ordinary tick without new packets', () => {
  const previous = Object.fromEntries(['requestAnimationFrame', 'cancelAnimationFrame', 'setInterval', 'clearInterval'].map(key => [key, globalThis[key]]));
  let now = 1100, tick, getClock = () => sourceClock();
  const doc = { defaultView: {}, addEventListener() {}, removeEventListener() {} };
  const node = () => ({ ownerDocument: doc, dataset: {}, style: { setProperty() {} }, children: [], textContent: '',
    append(...children) { this.children.push(...children); }, appendChild(child) { this.children.push(child); },
    setAttribute(key, value) { (this.attributes ??= {})[key] = value; },
    getAttribute(key) { return this.attributes?.[key]; }, removeAttribute(key) { delete this.attributes?.[key]; },
    addEventListener() {}, querySelectorAll() { return []; }, remove() {},
    getBoundingClientRect() { return { left: 0, right: 500, top: 0, bottom: 400, width: 500, height: 400 }; } });
  doc.createElement = node; doc.createElementNS = node;
  const host = node(); host.closest = () => null;
  globalThis.requestAnimationFrame = () => 1;
  globalThis.cancelAnimationFrame = () => {};
  globalThis.setInterval = fn => { tick = fn; return 1; };
  globalThis.clearInterval = () => {};
  let pipeline;
  try {
    pipeline = attachDenPipeline({ engine: { camera: {}, apartmentRoot: {}, renderer: { domElement: node() } },
      host, markers: () => [], clock: () => now, getSourceClock: () => getClock() });
    pipeline.update(stagesEvent(), now);
    assert.equal(pipeline.status().cards.perception.modelStages[0].freshness, 'fresh');
    now = 3200; tick();
    assert.equal(pipeline.status().cards.perception.modelStages[0].freshness, 'stale');
    getClock = () => { throw Error('clock unavailable'); }; tick();
    assert.equal(pipeline.status().cards.perception.modelStages[0].freshness, 'unknown');
  } finally {
    pipeline?.dispose();
    for (const [key, value] of Object.entries(previous)) if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
  }
});
