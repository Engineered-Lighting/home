import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
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

// A people event shaped like den-live model_projection.project output (intent.IntentHint.public rows), real transform.
const RUN = 'den-run-7f3a', EPOCH = 3, TRANSFORM = 'den-oak@2026-11-13.1', HINT_REVISION = 'd059602a9cdb2d198301f103aa0c7362ff943cb7';
const generation = (run, epoch) => createHash('sha256').update(JSON.stringify([run, epoch])).digest('hex');
const denTrack = (extra = {}) => ({ track_id: 'trk-1', state: 'confirmed', xy_m: [1.2, 2.3], sigma_m: .08, service_run_id: RUN,
  camera_epoch: EPOCH, association_revision: 5, heading_deg: null, seated: false, posture: 'standing', who: null,
  head_orientation: null, torso_orientation: null, motion: null, association_status: 'confident', current_obs_ids: ['obs-1'],
  position_basis: 'stereo', motion_forecast: null, motion_forecast_reason: 'motion_unavailable', ...extra });
const denFrame = (extra = {}) => ({ service_run_id: RUN, camera_epoch: EPOCH, frame_id: 4812, camera_session: 'oak-session-1',
  mode: 'rgb', rgb: { seq: 4812, device_s: null, host_s: 9.96, width: 640, height: 360 }, right: null, depth: null,
  depth_calibration_sha256: 'c'.repeat(64), transform_id: TRANSFORM, scan_revision: 'scan-r4', capture_mono: 9.9,
  arrival_mono: 9.96, capture_ts: T0 + 19.9, ...extra });
const vjepa = (extra = {}) => ({ id: 'vjepa-lm-qwen3-8b', role: 'motion_intent', state: 'unavailable',
  model_id: 'kkt94/Llama-V-JEPA-LM-Qwen3-8B', model_revision: HINT_REVISION, profile_digest: 'e'.repeat(64), device: null,
  reason: 'worker_unreachable', admission_scope: null, observed_mono: 9.5, valid_until_mono: 12.5, clock: 'producer_monotonic',
  configured: true, provisioned: null, loaded: null, queued: null, running: null, request_id: null, ...extra });
const resident = extra => vjepa({ state: 'idle', provisioned: true, loaded: true, device: 'cuda:0', reason: 'ready',
  admission_scope: 'bounded_trial', queued: 0, running: 0, ...extra });
const denHint = (extra = {}, dependencies = {}) => ({ schema: 'den.intent-hint/v1', request_id: 'req-1', clip_id: 'clip-1',
  profile_digest: 'a'.repeat(64), snapshot_manifest_digest: 'b'.repeat(64), model_revision: HINT_REVISION,
  dependencies: { camera_id: 'den-oak', service_run_id: RUN, camera_epoch: String(EPOCH), source_generation: generation(RUN, EPOCH),
    track_id: 'trk-1', association_revision: '5', transform_id: TRANSFORM, scan_revision: 'scan-r4',
    calibration_revision: 'c'.repeat(64), ...dependencies },
  produced_at: 9.75, expires_at: 13.25, observed_at: 9.4, clock: 'producer_monotonic', intent: 'turn_left',
  raw_option_scores: { continue: 0.0731, stop: 0.0402, turn_left: 0.6113, turn_right: 0.2101, unclear: 0.0653 },
  uncertainty: 'uncalibrated_semantic_hint', authority: 'semantic_prior_only', disposition: 'advisory_only',
  reason: 'awaiting_held_out_admission', consumed: false, ...extra });
const denEvent = ({ stage = vjepa(), hints = [], tracks = [denTrack()], frame = denFrame(), epoch = EPOCH } = {}) => ({
  schema: 'den-live-people-1', seq: 4812, emitted_ts: T0 + 20, mode: 'rgb', camera: { status: 'ok' }, perception_status: 'fresh',
  transform_id: TRANSFORM, content_revision: 'scan-r4', tracks, service_run_id: RUN, camera_epoch: epoch, frame,
  health: health({ model_stages: { schema: 'den-model-stages-1', sampled_mono: 10, clock: 'producer_monotonic', stages: [stage] } }),
  intent_hints: hints });
const perception = (e, extra) => Object.fromEntries(pipelinePerceptionRows(e, context(extra)));
const hintView = rows => ['Intent hint', 'Hint type', 'Hint model', 'Hint binding', 'Hint age', 'Disposition'].map(key => rows[key] ?? '').join(' | ');

test('den-live intent hints: waiting, resident, a bound hint, an expired one and a mismatched one all read differently', () => {
  const waiting = perception(denEvent());
  assert.equal(waiting['Intent hint'], 'Waiting · no worker report');
  assert.equal(waiting['Observation reason'], 'Worker unreachable');
  const unloaded = perception(denEvent({ stage: vjepa({ state: 'provisioned', provisioned: true, loaded: false, reason: 'not_resident',
    queued: 0, running: 0 }) }));
  assert.equal(unloaded['Intent hint'], 'Waiting · model not loaded');
  assert.equal(unloaded['Observation reason'], 'Model not resident');
  const idle = perception(denEvent({ stage: resident() }));
  assert.equal(idle['Intent hint'], 'None yet · model resident');
  assert.equal(idle.Loaded, 'Yes');
  assert.equal(perception(denEvent({ stage: resident({ state: 'running', running: 1 }) }))['Intent hint'], 'None yet · model running');
  assert.equal(perception(denEvent({ stage: resident() }), { now: 4400 })['Intent hint'], 'Waiting · model report stale');
  const hint = perception(denEvent({ stage: resident(), hints: [denHint()] }));
  assert.equal(hint['Intent hint'], 'Turn left');
  assert.equal(hint['Hint type'], 'Advisory · uncalibrated');
  assert.equal(hint['Hint model'], 'd059602a');
  assert.equal(hint['Hint binding'], 'Bound · current frame and track');
  assert.equal(hint['Hint age'], '0.3 s old · expires in 3.1 s', 'producer clock: 10.05 - 9.75 old, 13.25 - 10.105 left');
  assert.equal(hint.Disposition, 'Advisory only · Awaiting held-out admission');
  const expired = perception(denEvent({ stage: resident(), hints: [denHint()] }), { now: 4400 });
  assert.equal(expired['Intent hint'], 'Turn left · Expired', 'past expires_at on the source clock, though Den said advisory at publish');
  assert.match(expired['Hint age'], /^Expired · 3\.\d s old$/);
  const otherTrack = perception(denEvent({ stage: resident(), hints: [denHint({}, { track_id: 'trk-9' })] }));
  assert.equal(otherTrack['Intent hint'], 'Turn left · Not bound');
  assert.equal(otherTrack['Hint binding'], 'Not bound · track not current');
  const views = [waiting, unloaded, idle, hint, expired, otherTrack].map(hintView);
  assert.equal(new Set(views).size, views.length, views.join('\n'));
});

test('den-live hint binding follows the carrying event: generation, association, frame and the Den decline', () => {
  const binding = e => perception(e)['Hint binding'];
  const next = { tracks: [denTrack({ camera_epoch: EPOCH + 1 })], frame: denFrame({ camera_epoch: EPOCH + 1 }), epoch: EPOCH + 1 };
  assert.equal(binding(denEvent({ ...next, stage: resident(), hints: [denHint()] })), 'Not bound · source generation changed');
  assert.equal(binding(denEvent({ stage: resident(), hints: [denHint({}, { source_generation: 'f'.repeat(64) })] })),
    'Not bound · source generation changed', 'a generation that does not recompute from the run and epoch never binds');
  assert.equal(binding(denEvent({ stage: resident(), hints: [denHint()], tracks: [denTrack({ association_revision: 6 })] })),
    'Not bound · association changed');
  assert.equal(binding(denEvent({ stage: resident(), hints: [denHint()], tracks: [denTrack({ association_status: 'ambiguous' })] })),
    'Not bound · track not current');
  assert.equal(binding(denEvent({ stage: resident(), hints: [denHint()], frame: denFrame({ transform_id: 'den-oak@2026-11-14.1' }) })),
    'Not bound · camera frame changed');
  assert.equal(binding(denEvent({ stage: resident(), hints: [denHint()], frame: null })), 'Not bound · no current frame');
  const declined = perception(denEvent({ stage: resident(), hints: [denHint({ disposition: 'ignored', reason: 'current_binding_changed' })] }));
  assert.deepEqual([declined['Intent hint'], declined['Hint binding'], declined.Disposition],
    ['Turn left · Not bound', 'Not bound · Den declined this binding', 'Ignored · Person or source binding changed']);
  const lapsed = perception(denEvent({ stage: resident(), hints: [denHint({ disposition: 'expired', reason: 'capture_lease_expired' })] }));
  assert.deepEqual([lapsed['Intent hint'], lapsed.Disposition], ['Turn left · Expired', 'Expired · Capture lease expired']);
  const newest = perception(denEvent({ stage: resident(), hints: [denHint({ intent: 'stop', produced_at: 9.6 }), denHint({ intent: 'continue' })] }));
  assert.equal(newest['Intent hint'], 'Continue');
});

test('den-live hint age needs this connection source clock and never matures a future hint', () => {
  const e = denEvent({ stage: resident(), hints: [denHint()] });
  const unanchored = Object.fromEntries(pipelinePerceptionRows(e, { receivedAt: 1100, now: 1100 }));
  assert.deepEqual([unanchored['Intent hint'], unanchored['Hint age']], ['Turn left · Age unknown', 'Unknown']);
  const future = denEvent({ stage: resident(), hints: [denHint({ produced_at: 10.5 })] });
  assert.equal(perception(future)['Hint age'], 'Unknown');
  assert.equal(perception(future, { now: 1700 })['Hint age'], 'Unknown', 'display ticks cannot make a future hint current');
});

test('den-live hints never show raw scores as probabilities, never claim use, and malformed rows fail closed', () => {
  const rows = pipelinePerceptionRows(denEvent({ stage: resident(), hints: [denHint()] }), context());
  assert.doesNotMatch(JSON.stringify(rows), /0\.61|61%|probab|consum|clip-1|req-1/i);
  for (const bad of [{ consumed: true }, { disposition: 'consumed' }, { uncertainty: 'calibrated' }, { intent: 'walk_to_door' },
    { clock: 'browser' }, { expires_at: 30 }, { model_revision: 'main' }]) {
    const shown = perception(denEvent({ stage: resident(), hints: [denHint(bad)] }));
    assert.equal(shown['Intent hint'], 'Unavailable', JSON.stringify(bad));
    assert.equal(Object.hasOwn(shown, 'Hint binding'), false);
  }
  assert.equal(perception(denEvent({ stage: resident(), hints: [denHint(), denHint(), denHint()] }))['Intent hint'], 'Unavailable');
  assert.equal(Object.hasOwn(Object.fromEntries(pipelinePerceptionRows(event(), context())), 'Intent hint'), false,
    'a people event with no hint projection or intent stage keeps its calm rows');
});

test('Den model-stage reasons read as plain words instead of Unknown', () => {
  const label = reason => Object.fromEntries(pipelineModelRows(denEvent({ stage: vjepa({ reason }) }), 'perception', context()))['Observation reason'];
  for (const [reason, words] of Object.entries({ worker_unreachable: 'Worker unreachable', optional_worker_failure: 'Optional worker failed',
    submission_unproven: 'Job submission unconfirmed', qwen_busy: 'Deferred: Qwen busy', stage_observation_stale: 'Stage report stale',
    not_resident: 'Model not resident', gpu_execution_busy: 'Deferred: GPU busy', ready: 'Ready' })) assert.equal(label(reason), words);
  assert.equal(label('/home/PRIVATE_REASON'), 'Unknown');
  assert.equal(label('constructor'), 'Unknown');
});
