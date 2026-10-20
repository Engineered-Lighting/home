/* Real unchanged equipment/Three/aiming, authored inputs and clock only.
 * No browser, capture, device, host, native executable or physical authority. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import Core from '../app/src/home-equipment-registration.js';
import Aiming from '../app/src/home-apartment-aiming.js';
import Controller from '../app/src/home-apartment-desk-aim-preview.js';
import Preview from '../app/src/home-apartment-desk-preview.js';
import { createEquipmentVirtualFixtures } from '../app/src/home-3d/equipment-virtual-fixtures.mjs';

const copy = v => JSON.parse(JSON.stringify(v)), hash = c => c.repeat(64);
const flags = Object.fromEntries(['operational_geometry','geometry_authority','identity_authority','engagement_authority',
  'absence_authority','protection_authority','beam_authority','physical_permission','follow_allowed',
  'lighting_authority','physical_commands'].map(k => [k, false]));
function fixture(t) {
  const binding = { content_revision: 'authored-preview-room', registration_version: 1,
    manifest_digest: hash('a'), frame_digest: hash('b'), canonical_collision_digest: hash('c') };
  const references = { frame: 'z_up_metric_floor0', units: 'm', confirmed: true, provenance: { kind: 'authored_test' },
    floor: { id: 'floor', normal: [0,0,1], offset_m: 0 },
    walls: [[1,0,0],[0,1,0],[-1,0,-4],[0,-1,-5]].map(([x,y,offset_m], i) =>
      ({ id: `wall-${i}`, label: `Authored wall ${i}`, normal_xy: [x,y], offset_m })) };
  let model = Core.toModel(Core.createDocument(binding, references));
  model = Core.addSocket(Core.addSocket(model));
  for (let i = 1; i <= 4; i++) model = Core.updateModelEquipment(model, `victoria-den-mount-0${i}`, {
    physical_kind: 'socket', mount_type: 'ceiling', measurements: { wall_distances:
      [{ wall_id: 'wall-0', distance_m: i < 3 ? i : 3 },{ wall_id: 'wall-1', distance_m: i < 3 ? 1 : i }],
    height_above_floor_m: 2.4, method: 'laser', uncertainty_m: .005 } });
  const doc = Core.sealDocument({ ...Core.normalizeDocument(Core.toDocument(model)), revision: 2 });
  assert.equal(Core.validateDocument(doc).ok, true);
  const parent = new THREE.Group(), equipment = createEquipmentVirtualFixtures(parent, { binding });
  equipment.setDocument(doc); t.after(() => equipment.dispose());
  const calls = { aim: 0, restore: 0 }, timers = new Map(), notices = [];
  let now = 0, nextTimer = 0;
  const overlay = { virtualFixturesReadback: () => equipment.readback(),
    virtualFixtureInteractionDescriptor: id => equipment.interactionDescriptor(id),
    aimVirtualFixtureAtTarget(...args) { calls.aim++; return equipment.aimAtTarget(...args); },
    restoreVirtualFixtureAim(...args) { calls.restore++; return equipment.restoreAim(...args); } };
  const context = { active: true, hidden: false, committedOwnerActive: false,
    identity: { siteId: 'victoria', contentRevision: binding.content_revision,
      registrationVersion: binding.registration_version, frameDigest: binding.frame_digest } };
  const controller = Controller.create({ overlay, solveAim: Aiming.solveAim, getContext: () => context,
    nowMs: () => now, setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout: id => timers.delete(id), onChange: value => notices.push(value) });
  t.after(() => controller.dispose());
  const ids = equipment.readback().fixtures.map(v => v.fixture_id).sort();
  const descriptors = () => ids.map(id => equipment.interactionDescriptor(id));
  const before = descriptors();
  function packet(event = 1, hold = 1) {
    return { available: true, status: 'preview', label: 'Person visible · configured desk preview',
      virtual_preview_only: true, qualification: null, sourceKey: JSON.stringify(['authored-event',event,hash('f')]),
      remainingHoldS: hold, validUntil: 1800000002, ...flags,
      observation: { schema: 'home-lab-arm-observation-1', session_id: 'authored-session',
        capture_epoch: 'authored-capture', generation: 1, engine_epoch: 'authored-engine',
        request_id: `request-${event}`, frame_id: `frame-${event}`, valid_until: 1800000002 },
      provenance: { source_event_id: event, configured_candidate_sha256: hash('f') },
      personPoint: { method: 'approximate_supported_shoulder_midpoint', joint_ids: [5,6],
        point_m: [2,2,1.3], instance_ref: 'object-0', error_bound_m: null },
      target: { kind: 'configured_desk_centre', frame: 'z_up_metric_floor0', unit: 'm', point_m: [2,2,.75],
        normal_unit: [0,0,1], target_ref: { schema: 'home-registered-desk-target-2', digest: hash('d') },
        equipment_ref: { revision: doc.revision, digest: doc.digest }, binding: copy(binding) } };
  }
  function tick(value) {
    now = value;
    for (const [id, timer] of [...timers].sort((a,b) => a[1].at - b[1].at)) {
      if (timer.at <= now && timers.delete(id)) timer.fn();
    }
  }
  // Development-policy validator output and player evidence, authored.
  function dev(event = 1, { warnings = ['clock_uncertainty'], hold = 1, observedAt = 1800000000 + event } = {}) {
    const p = packet(event, hold);
    p.timingPolicy = 'development'; p.warnings = [...warnings]; p.originalAgeS = .5; p.observation.observed_at = observedAt;
    return p;
  }
  function evidence(over = {}) {
    return { policy: 'development', connection: 'live', sessionState: 'recording', sourceEpoch: 'authored-capture',
      armB: null, integrityReason: null, ...over };
  }
  return { equipment, parent, overlay, controller, doc, ids, before, descriptors, calls, context, packet, dev, evidence,
    tick, notices, timers, setNow: value => { now = value; } };
}

test('starts disabled, then one actual fixture reaches existing solver/Three direction without channel or placement changes', t => {
  const f = fixture(t), p = f.packet();
  assert.equal(f.controller.update(p).active, false); assert.equal(f.calls.aim, 0);
  f.controller.setEnabled(true); const result = f.controller.update(p);
  assert.equal(result.status, 'preview'); assert.equal(result.fixtureId, f.ids[0]); assert.equal(result.physical_commands, false);
  assert.equal(f.calls.aim, 1); assert.equal(f.descriptors().length, 4);
  for (const [i, d] of f.descriptors().entries()) {
    assert.deepEqual(d.display_anchor_m, f.before[i].display_anchor_m);
    assert.deepEqual(d.display_origin_m, f.before[i].display_origin_m);
    for (const channel of ['radials','downward','spotlight']) assert.deepEqual(d.state[channel], f.before[i].state[channel]);
    if (d.fixture_id !== result.fixtureId) assert.deepEqual(d, f.before[i]);
  }
  const d = f.equipment.interactionDescriptor(result.fixtureId);
  const expected = new THREE.Vector3(...p.target.point_m).sub(new THREE.Vector3(...d.display_origin_m)).normalize();
  const beam = f.equipment.root.children.find(v => v.userData.fixture_id === d.fixture_id).getObjectByName('modeled-aim-volume');
  assert.ok(new THREE.Vector3(...beam.userData.direction_unit).distanceTo(expected) < 1e-9);
  assert.deepEqual(Aiming.directionFromAim(d.state.pan_deg, d.state.tilt_deg,
    { horizontal_world_reference: d.horizontal_world_reference }).map(v => Math.round(v * 1e8)),
  expected.toArray().map(v => Math.round(v * 1e8)));
});

test('unchanged source polls cannot renew the first monotonic deadline and expiry restores exact prior state', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  f.tick(600); f.controller.update(f.packet(1, 1));
  assert.equal(f.calls.aim, 1); assert.ok(f.controller.readback().remainingHoldS <= .4);
  f.tick(1000); assert.equal(f.controller.readback().active, false);
  assert.equal(f.controller.readback().restoreStatus, 'restored'); assert.deepEqual(f.descriptors(), f.before);
  assert.equal(f.controller.update(f.packet(1, 2)).active, false);
});

test('a genuine later original extends only its own hold, retaining the single original preimage', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  f.tick(600); assert.equal(f.controller.update(f.packet(2)).active, true);
  f.tick(1000); assert.equal(f.controller.readback().active, true); assert.equal(f.calls.aim, 1);
  f.tick(1600); assert.equal(f.controller.readback().active, false); assert.deepEqual(f.descriptors(), f.before);
});

test('lost/ambiguous person withdraws, cannot revive the same original, and accepts a new validated original', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  f.controller.update({ available: false, reason: 'desk_preview_single_person_required' });
  assert.deepEqual(f.descriptors(), f.before); assert.equal(f.controller.update(f.packet()).active, false);
  assert.equal(f.controller.update(f.packet(2)).active, true);
});

test('explicit manual takeover restores first, remains disabled through new frames, and can be explicitly re-enabled', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  const chosen = f.controller.readback().fixtureId; f.controller.manualTakeover('manual_knob');
  assert.deepEqual(f.descriptors(), f.before); assert.equal(f.controller.readback().enabled, false);
  f.equipment.setFixtureState(chosen, { pan_deg: 30, spotlight: { brightness: .23 } });
  const manual = f.descriptors(); f.controller.update(f.packet(2)); f.tick(2000);
  assert.deepEqual(f.descriptors(), manual);
  f.controller.setEnabled(true); assert.equal(f.controller.update(f.packet(3)).active, true);
  f.controller.withdraw('source_stopped'); assert.deepEqual(f.descriptors(), manual);
});

test('unannounced manual channel/aim edits lose CAS ownership and are never overwritten on expiry or later input', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  f.equipment.setFixtureState(f.ids[0], { spotlight: { brightness: .17 } });
  const manual = f.descriptors(); f.tick(1000);
  assert.equal(f.controller.readback().restoreStatus, 'not_owned'); assert.equal(f.controller.readback().enabled, false);
  assert.deepEqual(f.descriptors(), manual); f.controller.update(f.packet(2)); assert.deepEqual(f.descriptors(), manual);
});

test('equipment generation or scene changes forget the snapshot rather than restoring into another scene', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  const next = copy(f.doc); next.revision++; next.digest = hash('e'); f.equipment.setDocument(next);
  const changed = f.descriptors(); f.controller.withdraw('equipment_changed');
  assert.equal(f.controller.readback().restoreStatus, 'scope_changed'); assert.deepEqual(f.descriptors(), changed);
  f.context.identity.frameDigest = hash('d'); assert.equal(f.controller.update(f.packet(2)).active, false);
  assert.deepEqual(f.descriptors(), changed);
});

test('wrong frame/equipment binding, hidden context, and committed owner all refuse or withdraw', t => {
  const f = fixture(t); f.controller.setEnabled(true);
  const wrong = f.packet(); wrong.target.equipment_ref.digest = hash('e');
  assert.equal(f.controller.update(wrong).active, false); assert.equal(f.calls.aim, 0);
  assert.equal(f.controller.update(f.packet(2)).active, true);
  f.context.hidden = true; assert.equal(f.controller.update(f.packet(3)).active, false); assert.deepEqual(f.descriptors(), f.before);
  f.context.hidden = false; f.context.committedOwnerActive = true;
  assert.equal(f.controller.update(f.packet(4)).active, false); assert.deepEqual(f.descriptors(), f.before);
});

test('same original with changed target or older event cannot acquire a new lease', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet(3));
  const changed = f.packet(3); changed.target.point_m[0] += .1;
  assert.equal(f.controller.update(changed).reason, 'preview_original_changed'); assert.deepEqual(f.descriptors(), f.before);
  assert.equal(f.controller.update(f.packet(2)).active, false); assert.equal(f.calls.aim, 1);
  assert.equal(f.controller.update(f.packet(4)).active, true);
});

test('selection skips a vertical indeterminate first fixture using existing solver, preserving it untouched', t => {
  const f = fixture(t); f.controller.setEnabled(true); const p = f.packet();
  p.target.point_m = [f.before[0].display_origin_m[0],f.before[0].display_origin_m[1],.75];
  const result = f.controller.update(p); assert.equal(result.active, true); assert.equal(result.fixtureId, f.ids[1]);
  assert.deepEqual(f.equipment.interactionDescriptor(f.ids[0]), f.before[0]);
});

test('dispose cancels its timer, restores only its own aim, and cannot reactivate', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.packet());
  f.controller.dispose(); assert.equal(f.timers.size, 0); assert.deepEqual(f.descriptors(), f.before);
  f.controller.setEnabled(true); f.controller.update(f.packet(2)); f.tick(5000);
  assert.equal(f.controller.readback().status, 'disposed'); assert.equal(f.calls.aim, 1);
});

// Development timing hold (virtual preview only). Authored packets/evidence.
const armB = (event, personCount, observedAt) => ({ key: JSON.stringify([`request-${event}`, `frame-${event}`]),
  observedAt, captureEpoch: 'authored-capture', personCount, mergedDuplicates: 0, countSource: 'persons2d' });
const NEUTRAL = ['desk_preview_original_expired','desk_preview_receipt_retired','desk_preview_receipt_expired',
  'desk_preview_parent_retired','desk_preview_observation_changed','desk_preview_awaiting_observation',
  'desk_preview_displayed_frame_mismatch','desk_preview_unavailable','desk_preview_clock_unavailable',
  'desk_preview_shoulders_unavailable','desk_preview_single_person_required'];

test('setEnabled(true) before any observation gives preview_awaiting_observation and zero aims', t => {
  const f = fixture(t), armed = f.controller.setEnabled(true);
  assert.equal(armed.enabled, true); assert.equal(armed.active, false); assert.equal(armed.reason, 'preview_awaiting_observation');
  f.tick(5000); assert.equal(f.controller.readback().reason, 'preview_awaiting_observation'); assert.equal(f.calls.aim, 0);
  assert.deepEqual(f.descriptors(), f.before);
  const r = f.controller.update(f.dev(1), f.evidence());
  assert.equal(r.active, true); assert.equal(r.policy, 'development'); assert.equal(f.calls.aim, 1);
});

test('development hold survives neutral reasons and withdraws preview_support_timeout exactly 10 s after the last new support', t => {
  const f = fixture(t); f.controller.setEnabled(true);
  let r = f.controller.update(f.dev(1), f.evidence());
  assert.equal(r.active, true); assert.equal(r.holding, true); assert.equal(r.status, 'preview'); assert.equal(r.remainingHoldS, 10);
  for (const [at, input] of [[1000, { available: false, reason: 'desk_preview_receipt_retired' }],
    [2500, { available: false, reason: 'desk_preview_original_expired' }],
    [4000, { available: false, reason: 'desk_preview_shoulders_unavailable' }],
    [6000, { available: false, reason: 'desk_preview_single_person_required' }],
    [7000, { available: false, reason: 'desk_preview_approximation_unavailable', parentReason: 'approximate_receipt_retired' }],
    [8000, { available: false, reason: 'desk_preview_displayed_frame_mismatch' }]]) {
    f.tick(at); r = f.controller.update(input, f.evidence());
    assert.equal(r.active, true, input.reason); assert.equal(r.status, 'holding'); assert.equal(r.reason, input.reason);
  }
  assert.equal(r.remainingHoldS, 2); assert.equal(f.calls.aim, 1);
  f.tick(9999); assert.equal(f.controller.readback().active, true);
  f.tick(10000); r = f.controller.readback();
  assert.equal(r.active, false); assert.equal(r.reason, 'preview_support_timeout'); assert.equal(r.enabled, true);
  assert.equal(r.restoreStatus, 'restored'); assert.deepEqual(f.descriptors(), f.before);
});

test('duplicate polls never extend lastSeen; a new original resets it without a second aim', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
  f.tick(4000);
  let r = f.controller.update(f.dev(1, { warnings: ['clock_uncertainty', 'home_original_deadline_passed'] }),
    f.evidence({ armB: armB(1, 1, 1800000001) }));
  assert.equal(r.status, 'preview'); assert.equal(r.lastSeenS, 4); assert.equal(r.remainingHoldS, 6);
  f.tick(6000); r = f.controller.update(f.dev(2), f.evidence({ armB: armB(2, 1, 1800000002) }));
  assert.equal(r.active, true); assert.equal(r.lastSeenS, 0); assert.equal(r.remainingHoldS, 10); assert.equal(f.calls.aim, 1);
  assert.deepEqual(r.warningCounts, { clock_uncertainty: 2, home_original_deadline_passed: 1 }); assert.equal(r.warningsRecorded, 2);
  f.tick(10000); f.controller.update(f.dev(2), f.evidence()); assert.equal(f.controller.readback().active, true);
  f.tick(15999); assert.equal(f.controller.readback().active, true);
  f.tick(16000); assert.equal(f.controller.readback().reason, 'preview_support_timeout'); assert.deepEqual(f.descriptors(), f.before);
});

test('newer arm-B output with 0 people withdraws immediately, stays armed, recovers only from a fresh original', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence({ armB: armB(1, 1, 1800000001) }));
  f.tick(500);
  let r = f.controller.update({ available: false, reason: 'desk_preview_single_person_required' }, f.evidence({ armB: armB(9, 0, 1800000001.5) }));
  assert.equal(r.active, false); assert.equal(r.reason, 'preview_no_person'); assert.equal(r.enabled, true);
  assert.deepEqual(f.descriptors(), f.before);
  assert.equal(f.controller.update(f.dev(1), f.evidence()).active, false, 'the retired original cannot revive');
  r = f.controller.update(f.dev(2), f.evidence({ armB: armB(2, 1, 1800000002) }));
  assert.equal(r.active, true); assert.equal(r.holding, true); assert.equal(f.calls.aim, 2);
});

test('newer output with 2 people withdraws; evidence not newer than the support is ignored', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(3), f.evidence());
  const neutral = { available: false, reason: 'desk_preview_single_person_required' };
  assert.equal(f.controller.update(neutral, f.evidence({ armB: armB(2, 2, 1800000002) })).active, true, 'older output');
  assert.equal(f.controller.update(neutral, f.evidence({ armB: armB(3, 0, 1800000003) })).active, true, 'the support itself');
  assert.equal(f.controller.update(neutral, f.evidence({ armB: armB(4, 2, 1800000003) })).active, true, 'not newer');
  assert.equal(f.controller.update(neutral, f.evidence({ armB: armB(4, null, 1800000004) })).active, true, 'malformed count is neutral');
  assert.equal(f.controller.update(neutral, f.evidence({ armB: armB(4, 1, 1800000004) })).active, true, 'one person');
  const r = f.controller.update(neutral, f.evidence({ armB: armB(5, 2, 1800000005) }));
  assert.equal(r.active, false); assert.equal(r.reason, 'preview_multiple_people'); assert.equal(r.enabled, true);
  assert.deepEqual(f.descriptors(), f.before);
});

test('integrity table: camera epoch/identity/calibration, mount signature, scene/mount identity, source epoch change withdraw', t => {
  const rows = [
    ...['approximate_camera_epoch_changed','approximate_camera_epoch_unverified','approximate_camera_identity_changed',
      'approximate_camera_calibration_changed','approximate_mount_signature_changed','approximate_mount_signature_unknown',
      'approximate_scene_or_mount_identity_changed','approximate_successor_mount_check_required','desk_preview_target_binding_changed']
      .map(reason => [{ integrityReason: reason }, reason]),
    [{ sourceEpoch: 'replacement-capture' }, 'preview_camera_identity_changed'],
    [{ connection: 'stopped' }, 'preview_source_stopped'], [{ connection: 'disconnected' }, 'preview_source_stopped'],
    [{ connection: 'refused' }, 'preview_source_stopped'], [{ sessionState: 'stopping' }, 'preview_source_stopped'],
    [{ policy: 'strict' }, 'preview_timing_policy_changed'], [null, 'preview_evidence_unavailable'],
    [{ armB: { key: 'x' } }, 'preview_evidence_unavailable']];
  for (const [over, why] of rows) {
    const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
    const r = f.controller.update({ available: false, reason: 'desk_preview_receipt_retired' }, over === null ? undefined : f.evidence(over));
    assert.equal(r.active, false, why); assert.equal(r.reason, why); assert.equal(r.enabled, true, why);
    assert.deepEqual(f.descriptors(), f.before, why);
  }
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
  assert.equal(f.controller.update({ available: false, reason: 'desk_preview_receipt_retired' }, f.evidence({ sourceEpoch: null })).active, true,
    'an unknown epoch is not an identity change');
  const replaced = f.dev(2); replaced.observation.capture_epoch = 'replacement-capture';
  assert.equal(f.controller.update(replaced, f.evidence()).reason, 'preview_camera_identity_changed');
});

test('neutral allow-list holds; unknown codes and desk_preview_source_stopped withdraw', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
  for (const reason of NEUTRAL) {
    const r = f.controller.update({ available: false, reason }, f.evidence());
    assert.equal(r.active, true, reason); assert.equal(r.status, 'holding', reason);
  }
  for (const [input, why] of [[{ available: false, reason: 'desk_preview_source_stopped' }, 'desk_preview_source_stopped'],
    [{ available: false, reason: 'desk_preview_authority_invalid' }, 'desk_preview_authority_invalid'],
    [{ available: false, reason: 'desk_preview_future_code' }, 'desk_preview_future_code'],
    [{ available: false, reason: 'desk_preview_approximation_unavailable', parentReason: 'approximate_camera_identity_changed' }, 'desk_preview_approximation_unavailable'],
    [{ available: false, reason: 'desk_preview_approximation_unavailable', parentReason: 'approximate_mount_signature_unknown' }, 'desk_preview_approximation_unavailable'],
    [null, 'preview_input_unavailable'], [undefined, 'preview_input_invalid']]) {
    const g = fixture(t); g.controller.setEnabled(true); g.controller.update(g.dev(1), g.evidence());
    const r = g.controller.update(input, g.evidence());
    assert.equal(r.active, false, why); assert.equal(r.reason, why); assert.deepEqual(g.descriptors(), g.before, why);
  }
  const strict = fixture(t); strict.controller.setEnabled(true); strict.controller.update(strict.packet());
  assert.equal(strict.controller.update({ available: false, reason: 'desk_preview_receipt_retired' }).active, false,
    'neutral reasons hold only under a development hold');
});

test('policy switch mid-hold withdraws preview_timing_policy_changed', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
  let r = f.controller.update(f.packet(2), f.evidence({ armB: armB(2, 1, 1800000002) }));
  assert.equal(r.active, false); assert.equal(r.reason, 'preview_timing_policy_changed'); assert.deepEqual(f.descriptors(), f.before);
  const g = fixture(t); g.controller.setEnabled(true); g.controller.update(g.packet(1));
  r = g.controller.update(g.dev(2), g.evidence());
  assert.equal(r.active, false); assert.equal(r.reason, 'preview_timing_policy_changed'); assert.deepEqual(g.descriptors(), g.before);
  for (const bad of [p => { p.warnings = ['Bad Code']; }, p => { p.warnings = ['dup', 'dup']; }, p => { p.timingPolicy = 'relaxed'; },
    p => { delete p.observation.observed_at; }]) {
    const h = fixture(t); h.controller.setEnabled(true); const p = h.dev(1); bad(p);
    assert.equal(h.controller.update(p, h.evidence()).active, false); assert.equal(h.calls.aim, 0);
  }
  const s = fixture(t); s.controller.setEnabled(true); const p = s.packet(1); p.warnings = ['clock_uncertainty'];
  assert.equal(s.controller.update(p).active, false, 'a strict input cannot carry warnings');
});

test('warnings excluded from fingerprint (no preview_original_changed)', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
  const later = f.dev(1, { warnings: ['clock_uncertainty', 'home_original_deadline_passed'] }); later.originalAgeS = 3;
  const r = f.controller.update(later, f.evidence());
  assert.equal(r.active, true); assert.equal(r.reason, null);
  assert.deepEqual(r.warnings, ['clock_uncertainty', 'home_original_deadline_passed']);
  assert.deepEqual(r.warningCounts, { clock_uncertainty: 1, home_original_deadline_passed: 1 });
  const changed = f.dev(1); changed.target.point_m[0] += .1;
  assert.equal(f.controller.update(changed, f.evidence()).reason, 'preview_original_changed'); assert.deepEqual(f.descriptors(), f.before);
});

test('readback lastSeenLabel uses Home monotonic time; clock regression withdraws', t => {
  const f = fixture(t); f.setNow(1000); f.controller.setEnabled(true);
  const old = f.dev(1, { observedAt: 1700000000 }); old.originalAgeS = 1e8;
  f.controller.update(old, f.evidence());
  f.setNow(4700); let r = f.controller.readback();
  assert.equal(r.lastSeenLabel, 'last seen 3s ago'); assert.ok(Math.abs(r.lastSeenS - 3.7) < 1e-9); assert.ok(Math.abs(r.remainingHoldS - 6.3) < 1e-9);
  f.setNow(4000); r = f.controller.update({ available: false, reason: 'desk_preview_receipt_retired' }, f.evidence());
  assert.equal(r.active, false); assert.equal(r.reason, 'preview_clock_unavailable'); assert.deepEqual(f.descriptors(), f.before);
});

test('manual CAS edit during a hold sets not_owned and disables', t => {
  const f = fixture(t); f.controller.setEnabled(true); const id = f.controller.update(f.dev(1), f.evidence()).fixtureId;
  f.equipment.setFixtureState(id, { spotlight: { brightness: .21 } }); const manual = f.descriptors();
  const r = f.controller.update({ available: false, reason: 'desk_preview_receipt_retired' }, f.evidence());
  assert.equal(r.restoreStatus, 'not_owned'); assert.equal(r.enabled, false); assert.equal(r.active, false);
  assert.deepEqual(f.descriptors(), manual); f.tick(20000); assert.deepEqual(f.descriptors(), manual);
  f.controller.update(f.dev(2), f.evidence()); assert.deepEqual(f.descriptors(), manual); assert.equal(f.calls.aim, 1);
});

test('backend evidence is preferred: contrary/integrity withdraw at once, supporting/neutral hold whatever Home counts', t => {
  const backend = (evidence, eventId = 2) => ({ evidence, eventId });
  const neutral = { available: false, reason: 'desk_preview_observation_changed' };
  for (const [evidence, why] of [['contrary', 'preview_evidence_contrary'], ['integrity', 'preview_evidence_integrity']]) {
    const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence({ backend: backend('supporting', 1) }));
    f.tick(500);
    const r = f.controller.update(neutral, f.evidence({ backend: backend(evidence), armB: armB(2, 1, 1800000002) }));
    assert.equal(r.active, false, evidence); assert.equal(r.reason, why, 'Home counted one person; the backend wins');
    assert.equal(r.enabled, true, evidence); assert.deepEqual(f.descriptors(), f.before, evidence);
  }
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence({ backend: backend('supporting', 1) }));
  for (const [at, evidence, count] of [[1000, 'neutral', 0], [2000, 'supporting', 2], [3000, 'neutral', null]]) {
    f.tick(at);
    const r = f.controller.update(neutral, f.evidence({ backend: backend(evidence, 1 + at / 1000), armB: armB(1 + at / 1000, count, 1800000001 + at / 1000) }));
    assert.equal(r.active, true, evidence + ' with a Home count of ' + count); assert.equal(r.status, 'holding');
  }
  f.tick(4000);
  assert.equal(f.controller.update(neutral, f.evidence({ backend: { evidence: 'maybe', eventId: 5 } })).reason, 'preview_evidence_unavailable');
  const g = fixture(t); g.controller.setEnabled(true); g.controller.update(g.dev(1), g.evidence());
  assert.equal(g.controller.update(neutral, g.evidence({ backend: null, armB: armB(2, 0, 1800000002) })).reason, 'preview_no_person',
    'without backend evidence Home counts the raw latest output');
  for (const bad of [p => { p.evidence = 'maybe'; }, p => { p.evidenceEventId = 0; }]) {
    const h = fixture(t); h.controller.setEnabled(true); const p = h.dev(1); bad(p);
    assert.equal(h.controller.update(p, h.evidence()).active, false); assert.equal(h.calls.aim, 0);
  }
  const k = fixture(t); k.controller.setEnabled(true); const p = k.dev(1); p.evidence = 'supporting'; p.evidenceEventId = 7;
  assert.equal(k.controller.update(p, k.evidence()).active, true);
});

test('backend desk_preview_hold_elapsed withdraws: the shorter of the two 10 s windows ends the hold', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence({ backend: { evidence: 'supporting', eventId: 1 } }));
  f.tick(9800);
  const r = f.controller.update({ available: false, reason: 'desk_preview_hold_elapsed', evidence: 'supporting', evidenceEventId: 1 },
    f.evidence({ backend: { evidence: 'supporting', eventId: 1 } }));
  assert.equal(r.active, false); assert.equal(r.reason, 'desk_preview_hold_elapsed'); assert.equal(r.enabled, true, 'stays armed');
  assert.deepEqual(f.descriptors(), f.before);
  assert.equal(f.controller.update(f.dev(2), f.evidence({ backend: { evidence: 'supporting', eventId: 2 } })).active, true,
    'a newly published original re-aims');
});

test('policy is reset when disarmed, kept while armed between supports', t => {
  const f = fixture(t); f.controller.setEnabled(true); f.controller.update(f.dev(1), f.evidence());
  assert.equal(f.controller.readback().policy, 'development');
  f.controller.update({ available: false, reason: 'desk_preview_authority_invalid' }, f.evidence());
  let r = f.controller.readback(); assert.equal(r.active, false); assert.equal(r.enabled, true);
  assert.equal(r.policy, 'development', 'an armed controller keeps reporting the last policy');
  r = f.controller.setEnabled(false); assert.equal(r.policy, null); assert.equal(r.warningsRecorded, 1);
  for (const exit of ['manual', 'not_owned', 'dispose']) {
    const g = fixture(t); g.controller.setEnabled(true); const id = g.controller.update(g.dev(1), g.evidence()).fixtureId;
    if (exit === 'manual') r = g.controller.manualTakeover('manual_knob');
    if (exit === 'not_owned') { g.equipment.setFixtureState(id, { spotlight: { brightness: .2 } });
      r = g.controller.update({ available: false, reason: 'desk_preview_receipt_retired' }, g.evidence()); }
    if (exit === 'dispose') r = g.controller.dispose();
    assert.equal(r.enabled, false, exit); assert.equal(r.policy, null, exit);
  }
});

test('hold integrity reasons are the validator evidence reasons', () => {
  assert.deepEqual(Controller.INTEGRITY_REASONS, Preview.INTEGRITY_REASONS); assert.equal(Controller.HOLD_MS, 10000);
  for (const reason of ['approximate_mount_signature_changed', 'approximate_successor_matrix_changed', 'desk_preview_scope_mismatch',
    'approximate_scene_scope_required', 'approximate_camera_epoch_unverified', 'desk_preview_scope_changed'])
    assert.equal(Controller.isIntegrityReason(reason) && Preview.isIntegrityReason(reason), true, reason);
  for (const reason of ['approximate_receipt_retired', 'approximate_displayed_frame_mismatch', 'desk_preview_original_expired'])
    assert.equal(Controller.isIntegrityReason(reason) || Preview.isIntegrityReason(reason), false, reason);
});
