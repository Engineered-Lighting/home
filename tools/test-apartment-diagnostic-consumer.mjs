/* Public producer -> projection/player -> actual Three layer contracts.
 * The fixture came through actual Store/ASGI/original-byte verification;
 * external capture/model/calibration/room transform remain authored inputs. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import * as THREE from 'three';
import { createRig } from '../app/src/home-3d/rig.js';
import { createPicking } from '../app/src/home-3d/picking.js';
import { attachApartmentPerceptionLayer } from '../app/src/home-3d/apartment-perception-layer.mjs';
const require = createRequire(import.meta.url), View = require('../app/src/home-apartment-perception-view.js'), Player = require('../app/src/home-apartment-perception-player.js');
const Stream = require('../app/src/home-den-lab-stream.js');
const fixture = JSON.parse(readFileSync(new URL('./fixtures/apartment-diagnostic-room-pose.json', import.meta.url), 'utf8'));
const copy = value => structuredClone(value), settle = () => new Promise(resolve => setImmediate(resolve));
function envelope(packet = copy(fixture.diagnostic_room_pose)) {
  return { schema: 'home-apartment-perception-1', scope: copy(packet.diagnostic_pose.scope), session_state: packet.session_state,
    source_epoch: packet.frame.source_epoch, frame: copy(packet.frame), pose2d: null, models: [], room_pose: null,
    diagnostic_room_pose: packet, diagnostic_receipt_elapsed_s: .2 };
}
function project(env = envelope(), extra = {}) {
  return View.project({ envelope: env, scope: fixture.scope, clock: null, collisionDigest: fixture.active_collision_sha256,
    displayedFrame: { descriptor: copy(env.frame), scope: copy(fixture.scope), key: View.frameKey(env.frame, fixture.scope) },
    diagnosticReceipt: { eventKey: View.diagnosticEventKey(env.diagnostic_room_pose), remainingHoldS: 1 }, ...extra });
}
function controller() {
  let now = 0, sequence = 0, subscription = null, releases = 0, reads = 0;
  const timeouts = new Map(), intervals = new Map(), env = { AbortController, document: { hidden: false },
    performance: { now: () => now }, HomeApartmentPerceptionView: View,
    setInterval: callback => { intervals.set(++sequence, callback); return sequence; }, clearInterval: id => intervals.delete(id),
    setTimeout: (callback, ms) => { timeouts.set(++sequence, { callback, at: now + ms }); return sequence; }, clearTimeout: id => timeouts.delete(id) };
  const source = { subscribe(args) { subscription = args; return () => { subscription = null; }; }, sampleClock: () => ({ domain: 'unix', now_s: 1800000100, uncertainty_s: null }),
    async readFrame(frame) { reads++; return { bytes: Uint8Array.of(137, 80, 78, 71), type: 'image/png', frame_metadata: { media_sha256: frame.media_sha256 } }; } };
  const player = Player.create({ scope: copy(fixture.scope), source, env, collisionDigest: fixture.active_collision_sha256,
    decode: async (_value, { frame }) => ({ url: 'blob:verified-authored', width: frame.width, height: frame.height, dispose() { releases++; } }) });
  return { player, env, timeouts, intervals, get releases() { return releases; }, get reads() { return reads; },
    push: value => subscription.onUpdate(value), status: value => subscription.onStatus(value),
    async advance(ms) { now += ms; for (const [id, item] of [...timeouts]) if (item.at <= now) { timeouts.delete(id); item.callback(); } await settle(); },
    tick() { for (const callback of [...intervals.values()]) callback(); } };
}

test('verified producer fixture is a partial diagnostic picture, preserving unknown error and independent clocks', () => {
  const result = project();
  assert.equal(fixture.evidence, 'authored_external_boundaries_actual_Store_ASGI_original_byte_verifier_projector');
  assert.equal(result.diagnosticRoomPose.renderable, true); assert.equal(result.diagnosticRoomPose.status, 'timing_unverified');
  assert.equal(result.diagnosticRoomPose.joints.length, 1); assert.deepEqual(result.diagnosticRoomPose.edges, []);
  assert.deepEqual(result.diagnosticRoomPose.joints[0].position_m, fixture.diagnostic_room_pose.diagnostic_pose.joints[0].position_m);
  assert.equal(result.roomPose.admitted, false); assert.equal(result.follow.allowed, false);
  assert.equal(result.diagnosticRoomPose.timing.exposure_error_bound_s, null);
  assert.deepEqual(result.diagnosticRoomPose.originalTimes, fixture.diagnostic_room_pose.diagnostic_pose.source_times);
});

test('selected-arm observer copies the actual producer packet and charges full live-read/graph delay before adoption', async () => {
  let now = 100; const updates = [], timers = new Set(), packet = copy(fixture.diagnostic_room_pose);
  const session = { id: fixture.scope.sessionId, site_id: 'victoria', camera_id: 'den', state: 'recording', generation: fixture.scope.generation };
  const live = { site_id: 'victoria', session_id: session.id, session, server_time: 1800000001, experiment_generation: 1,
    arms: { B: { ...copy(fixture.source_event.payload.arm_snapshot), diagnostic_room_pose: packet } } };
  const graph = { contract: 'perception-lab-graph-1', site_id: 'victoria', session_id: session.id, session: copy(session), inference: 'none', sources: [], arms: {} };
  const env = { performance: { now: () => now }, document: { hidden: false },
    setTimeout(callback, ms) { const id = setTimeout(callback, ms); timers.add(id); return id; }, clearTimeout(id) { clearTimeout(id); timers.delete(id); },
    HomePerceptionLabMedia: { readFrame: async () => ({ bytes: Uint8Array.of(137, 80, 78, 71), type: 'image/png' }) } };
  const observer = Stream.createObserver({ siteId: 'victoria', sessionId: session.id, generation: session.generation,
    scope: fixture.scope, env, onState: state => updates.push(state), transport: { async request(operation) {
      if (operation === 'sessions.live') { now = 450; return live; }
      if (operation === 'sessions.graph') { now = 900; return graph; }
      if (operation === 'media.describe') return fixture.frame_descriptor;
      throw Error('unexpected operation');
    } } });
  observer.start(); await settle();
  const supplied = updates.filter(value => value.perception.diagnostic_room_pose);
  assert.equal(supplied[0].perception.diagnostic_receipt_elapsed_s, .35);
  assert.equal(supplied.at(-1).perception.diagnostic_receipt_elapsed_s, .8);
  assert.deepEqual(supplied.at(-1).perception.diagnostic_room_pose, fixture.diagnostic_room_pose);
  const image = await observer.readFrame(observer.snapshot().perception.frame);
  assert.equal(image.frame_metadata.media_sha256, fixture.frame_descriptor.sha256);
  assert.equal(observer.snapshot().perception.frame.media_sha256, fixture.frame_descriptor.sha256);
  assert.equal(observer.snapshot().perception.source_epoch, fixture.diagnostic_room_pose.frame.source_epoch);
  observer.setArm('A'); assert.equal(observer.snapshot().perception.diagnostic_room_pose, null);
  observer.stop(); assert.equal(observer.snapshot().perception.diagnostic_room_pose, null);
  observer.destroy(); assert.equal(timers.size, 0);
});

test('every scene, source, image, review, collision, authority and receipt mismatch refuses diagnostics', () => {
  for (const mutate of [env => env.diagnostic_room_pose.diagnostic_pose.scope.contentRevision = 'foreign-pack',
    env => env.diagnostic_room_pose.diagnostic_pose.scope.generation++, env => env.diagnostic_room_pose.frame.media_sha256 = '0'.repeat(64),
    env => env.diagnostic_room_pose.diagnostic_pose.source_epoch = 'foreign-epoch', env => env.diagnostic_room_pose.frame.media_id = 'foreign-media',
    env => env.diagnostic_room_pose.diagnostic_pose.registration.status = 'review_required',
    env => env.diagnostic_room_pose.diagnostic_pose.registration.canonical_collision_digest = '0'.repeat(64),
    env => env.diagnostic_room_pose.physical_permission = true, env => env.diagnostic_room_pose.follow_allowed = true,
    env => env.diagnostic_room_pose.diagnostic_pose.joints[0].position_m[0] = NaN,
    env => env.frame.frame_id = 'another-source-frame', env => env.session_state = 'stopped']) {
    const env = envelope(); mutate(env); const result = project(env);
    assert.equal(result.diagnosticRoomPose.renderable, false); assert.equal(result.roomPose.admitted, false); assert.equal(result.follow.allowed, false);
  }
  assert.equal(project(envelope(), { displayedFrame: null }).diagnosticRoomPose.renderable, false);
  assert.equal(project(envelope(), { diagnosticReceipt: null }).diagnosticRoomPose.renderable, false);
  assert.equal(project(envelope(), { diagnosticReceipt: { eventKey: View.diagnosticEventKey(fixture.diagnostic_room_pose), remainingHoldS: 0 } }).diagnosticRoomPose.renderable, false);
});

test('player waits for the exact decoded image, subtracts transport time and cannot renew one event', async () => {
  const f = controller(); f.player.start(); f.push(envelope());
  assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, false);
  await settle(); await settle();
  assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, true);
  await f.advance(1000); const same = envelope(); same.diagnostic_receipt_elapsed_s = 0; f.push(same);
  await f.advance(700);
  assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, false, 'same-event HTTP reads never renew the first receipt deadline');
  f.push(same); f.tick(); assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, false);
  assert.equal(f.reads, 1); f.player.destroy(); assert.equal(f.timeouts.size, 0); assert.equal(f.intervals.size, 0); assert.equal(f.releases, 1);
});

test('same event with changed originals refuses; stop retires diagnostics without changing original metadata', async () => {
  const f = controller(); f.player.start(); f.push(envelope()); await settle(); await settle();
  const changed = envelope(); changed.diagnostic_room_pose.diagnostic_pose.joints[0].position_m[0] += .1; f.push(changed);
  assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, false);
  assert.equal(f.player.snapshot().view.diagnosticRoomPose.reason, 'diagnostic_original_event_changed');
  f.push(envelope()); assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, false);
  f.status({ status: 'stopped' }); assert.equal(f.player.snapshot().view.diagnosticRoomPose.renderable, false);
  assert.equal(f.player.snapshot().view.follow.allowed, false); f.player.stop(); assert.equal(f.player.snapshot().view, null); f.player.destroy();
  const stopped = controller(); stopped.player.start(); stopped.push(envelope()); await settle(); await settle();
  assert.equal(stopped.player.snapshot().view.diagnosticRoomPose.renderable, true);
  stopped.status({ status: 'stopped' }); stopped.push(envelope());
  assert.equal(stopped.player.snapshot().view.diagnosticRoomPose.renderable, false, 'Stop is terminal for the diagnostic observation owner');
  assert.equal(stopped.player.snapshot().view.diagnosticRoomPose.reason, 'diagnostic_source_stopped'); stopped.player.destroy();
});

test('explicit recorded inspection remains recorded; actual Three layer renders only supplied geometry and never follows', async () => {
  const env = envelope(); env.session_state = 'stopped'; const packet = env.diagnostic_room_pose;
  packet.status = 'recorded'; packet.session_state = 'stopped'; packet.timing.status = 'recorded'; packet.timing.remaining_receipt_hold_s = null;
  const view = project(env, { diagnosticReceipt: null }); assert.equal(view.diagnosticRoomPose.status, 'recorded');
  const root = new THREE.Group(), camera = new THREE.PerspectiveCamera(35, 1, .1, 200), rig = createRig(camera);
  rig.fitBounds(new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(30, 3, 30))); rig.snapHome(0);
  const engine = { apartmentRoot: root, camera, rig, renderer: { domElement: {} }, siteId: fixture.scope.siteId,
    contentRevision: fixture.scope.contentRevision, sceneIdentity: copy(fixture.scope),
    picking: createPicking(camera, { clientWidth: 800, clientHeight: 800 }) };
  const layer = attachApartmentPerceptionLayer(engine, fixture.scope); const before = rig.snapshotPose().target.toArray();
  layer.draw(view); assert.equal(layer.readback().renderableDiagnostic, true); assert.equal(layer.readback().jointCount, 1);
  assert.equal(layer.readback().edgeCount, 0); assert.equal(layer.readback().status, 'recorded');
  layer.setFollowing(true); assert.equal(layer.readback().following, false); assert.equal(layer.readback().followAllowed, false);
  assert.deepEqual(rig.snapshotPose().target.toArray(), before); assert.equal(layer.screenAnchor(), null);
  view.diagnosticRoomPose.renderable = false; layer.draw(view); assert.equal(layer.readback().jointCount, 0);
  layer.dispose(); assert.equal(root.children.length, 0);
});
