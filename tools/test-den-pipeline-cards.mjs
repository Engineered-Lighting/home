import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pipelineCards } from '../app/src/home-den-pipeline-layer.mjs';

const T0 = 1794600000;
const health = (extra = {}) => ({ fps_in: 2.9, lag_s_median: .42, transit_s_median: .15, dropped_per_min: 1,
  timings_ms: { detector: { n: 30, p50: 41, p95: 63 } },
  qwen: { state: 'asking', why: null, last_reply_at: T0 + 18, reply_s_median: 1.6, asks_per_min: 28 }, ...extra });
const sam = { track_id: 'trk-3', state: 'confirmed', xy_m: [2, 3], sigma_m: .07, camera_epoch: 1, association_revision: 2,
  identity: { kind: 'household', label: 'Sam', score: .93, at: T0 + 10 }, activity: { word: 'reading', close_up: true, at: T0 + 18 } };
const event = (extra = {}) => ({ emitted_ts: T0 + 20, mode: 'rgb', camera: { status: 'ok' }, perception_status: 'fresh', tracks: [sam], health: health(), ...extra });
const spot = (extra = {}) => ({ stopped: false, subject: { track_id: 'trk-3', camera_epoch: 1, association_revision: 2, request_id: 'ask-1' },
  fixtures: [{ fixture_id: 'F3', on: true, level: .7, cct_k: 2700, reason: 'task', motion_phase: 'settled', target_m: [2, 2.7, .8] }], ...extra });

test('a live den: each card says one thing, in den-live’s own numbers', () => {
  const c = pipelineCards(event(), 0.2, spot(), { F3: 'Spot 3' });
  assert.deepEqual([c.camera.value, c.camera.sub], ['2.9 fps', '150 ms away']);
  assert.deepEqual([c.perception.value, c.perception.sub], ['1 person', '0.6 s behind']);
  assert.deepEqual([c.qwen.value, c.qwen.sub], ['Reading', '8 s left']);   // answered 2.2 s ago
  assert.deepEqual([c.coordinator.value, c.coordinator.sub, c.coordinator.fixture], ['70%', 'for Sam', 'Spot 3']);
  assert.deepEqual(c.coordinator.target, [2, 2.7, .8]);
});

test('occupancy: Frigate’s own word when den-live sends it, else the Qwen gate, marked as inferred', () => {
  assert.equal(pipelineCards(event({ health: health({ occupancy: { effective: 'off', why: 'no one' } }) }), 0, null).occupancy.value, 'Empty');
  const gate = pipelineCards(event(), 0, null).occupancy;
  assert.equal(gate.value, 'Gate open'); assert.equal(gate.inferred, true);
  assert.equal(pipelineCards(event({ health: health({ qwen: { state: 'off', why: 'no one seated' } }) }), 0, null).occupancy.value, 'Gate closed');
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
