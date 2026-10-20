/* PROPOSED fields (pending the owner's approval): Names labels, hand-placed
 * camera pose and mmWave records. Pure helpers only; authored values. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const D = require('../app/src/home-apartment-setup-devices.js');

test('the proposal flag is off unless the URL or this browser opts in', () => {
  const win = (search, stored) => ({ location: { search }, localStorage: { getItem: key => key === D.FLAG_KEY ? stored : null } });
  assert.equal(D.proposalEnabled(win('', null)), false);
  assert.equal(D.proposalEnabled(win('?setupProposal=1', null)), true);
  assert.equal(D.proposalEnabled(win('', '1')), true);
  assert.equal(D.proposalEnabled(win('?setupProposal=0', '1')), false, 'the URL can switch it off for one visit');
  assert.equal(D.proposalEnabled(null), false);
});

test('labels are at most 10 characters; duplicate names are flagged, not refused', () => {
  assert.equal(D.labelError('Desk lamp'), ''); assert.equal(D.labelError(''), '');
  assert.match(D.labelError('Reading lamp'), /at most 10/);
  assert.deepEqual([...D.duplicateNames([{ id: 'a', name: 'Lamp' }, { id: 'b', name: ' lamp ' }, { id: 'c', name: 'Desk' }, { id: 'd', name: '' }])], ['a', 'b']);
});

test('a mount click places the body facing into the room', () => {
  const wall = D.poseFromPick({ mount: 'wall', point: [0, 2, 1.8], normal: [1, 0, 0], roomHeight: 2.5, roomCenter: [2, 2] });
  assert.deepEqual(wall, { pos: [0.04, 2, 1.8], yaw_rad: 0, pitch_deg: -15 });
  const ceiling = D.poseFromPick({ mount: 'ceiling', point: [1, 1, 0], roomHeight: 2.5, roomCenter: [2, 2] });
  assert.deepEqual(ceiling.pos, [1, 1, 2.45]); assert.equal(ceiling.pitch_deg, -60); assert.equal(ceiling.yaw_rad, 0.785);
  assert.deepEqual(D.poseFromPick({ mount: 'shelf', point: [3, 3, 0.9], roomCenter: [2, 2] }).pos, [3, 3, 0.94]);
});

test('Point at a spot sets pan and tilt; the lens sets a 16:9 vertical field', () => {
  assert.deepEqual(D.aimAt([0, 0, 2], [2, 0, 0]), { yaw_rad: 0, pitch_deg: -45 });
  assert.deepEqual(D.aimAt([0, 0, 2], [0, 2, 2]), { yaw_rad: 1.571, pitch_deg: 0 });
  assert.equal(D.aimAt([1, 1, 1], [1, 1, 1]), null);
  assert.equal(D.cameraVfov(90), 59); assert.equal(D.cameraVfov(120), 89);
});

test('Covers lists named aims and lights inside field and range only', () => {
  const camera = { id: 'cam', pos: [0, 0, 2], yaw_rad: 0, placement: { pitch_deg: -30, hfov_deg: 90, range_m: 6 } };
  const listed = D.covers(camera, { targets: [{ name: 'Desk', pos: [3, 0, 0.74] }, { name: 'Behind', pos: [-2, 0, 1] }],
    devices: [{ id: 'l1', type: 'light', name: 'Lamp', pos: [2, 1, 0.8] }, { id: 'l2', type: 'light', name: 'Far lamp', pos: [9, 0, 0] }, { id: 'cam', type: 'light', name: 'Self', pos: [0, 0, 2] }] });
  assert.deepEqual(listed, ['Desk', 'Lamp']);
  const f = D.frustum(camera);
  assert.deepEqual(f.apex, [0, 0, 2]); assert.equal(f.corners.length, 4);
});

test('camera status is derived from calibration and never stored', () => {
  const extrinsics = { R: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [0, 0, 0], rms_px: 1.2 };
  assert.equal(D.cameraStatus({ camera: {} }).label, 'Placed by hand · not calibrated');
  assert.equal(D.cameraStatus({ camera: { extrinsics } }).label, 'Calibrated');
  const movedAfter = { camera: { extrinsics }, placement: { method: 'hand', calibration_ref: D.calibrationRef(extrinsics) } };
  assert.equal(D.cameraStatus(movedAfter).label, 'Moved since calibration · recalibrate');
  const recalibrated = { camera: { extrinsics: { ...extrinsics, rms_px: 0.9 } }, placement: movedAfter.placement };
  assert.equal(D.cameraStatus(recalibrated).label, 'Calibrated', 'a newer calibration supersedes the move');
  assert.equal(D.calibrationRef({ b: 1, a: 2 }), D.calibrationRef({ a: 2, b: 1 }), 'stable digest');
});

test('a sensor reads Ordered until every listed entity exists in Home Assistant', () => {
  assert.equal(D.sensorStatus({ ha_entity_ids: [] }, {}).label, 'Ordered · not connected');
  assert.equal(D.sensorStatus({ ha_entity_ids: ['binary_sensor.study_presence'] }, {}).label, 'Ordered · not connected');
  assert.equal(D.sensorStatus({ ha_entity_ids: ['binary_sensor.study_presence', 'sensor.study_target_distance'] },
    { 'binary_sensor.study_presence': true }).state, 'ordered', 'partial pairing is not paired');
  assert.equal(D.sensorStatus({ ha_entity_ids: ['binary_sensor.study_presence'] }, { 'binary_sensor.study_presence': true }).label, 'Paired with Home Assistant');
});

test('placement records are clamped and keep cameras and sensors apart', () => {
  const camera = D.placementRecord({ mount: 'wall', pitch_deg: -120, hfov_deg: 200, vfov_deg: 10, now: 'n', extrinsics: null });
  assert.deepEqual(camera, { method: 'hand', mount: 'wall', pitch_deg: -90, hfov_deg: 150, vfov_deg: 20, placed_at: 'n', calibration_ref: null });
  const sensor = D.placementRecord({ mount: 'ceiling', pitch_deg: -60, hfov_deg: 100, vfov_deg: 90, range_m: 6.3, now: 'n', sensor: true });
  assert.deepEqual(sensor, { method: 'hand', mount: 'ceiling', pitch_deg: -60, hfov_deg: 100, vfov_deg: 90, placed_at: 'n', range_m: 6.5 });
});
