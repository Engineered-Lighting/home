// The procedural figure's joints, without a browser: pose 0 is the model test's figure and hold, the eight
// poses are slightly different pictures, and a new seed moves the small motion.
//
//   node --test tools\den-sim\scene\test-scene-figure.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { COCO17, FIGURE_ID, WAY, joints, poseParams } from './figure.js';

const shots = JSON.parse(fs.readFileSync(new URL('./shots.json', import.meta.url), 'utf8'));
const [reading, phone, standing] = shots.scenes.map(scene => scene.person);
const near = (a, b, tolerance) => a.every((v, i) => Math.abs(v - b[i]) <= tolerance);
const apart = (a, b) => Math.max(...a.map((v, i) => Math.abs(v - b[i])));

test('pose 0 is the figure and the way of holding the model test chose', () => {
  assert.equal(FIGURE_ID, shots.figure.id);
  assert.equal(WAY, shots.figure.way);
  const [still] = poseParams(shots.seed, 1), j = joints(reading, still).joints, pitch = 0.42;
  const head = [0, 0.1 - 0.12 * Math.sin(pitch), 1.2];
  assert.ok(near(j.left_wrist, [0.15, -0.26, 0.9], 1e-12) && near(j.right_wrist, [-0.15, -0.26, 0.9], 1e-12));
  assert.ok(near(j.left_elbow, [0.3, 0, 0.76], 1e-12) && near(j.left_shoulder, [0.2, 0.17, 0.98], 1e-12));
  assert.ok(near(j.nose, [0, head[1] - 0.105 * Math.cos(pitch), head[2] - 0.105 * Math.sin(pitch) + 0.005], 1e-12));
  assert.ok(near(joints(phone, still).joints.left_wrist, [0.065, -0.26, 0.9], 1e-12), 'the phone brings the hands together');
  for (const person of [reading, phone, standing]) {
    const all = joints(person, still).joints;
    assert.deepEqual(Object.keys(all).sort(), [...COCO17].sort());
    assert.ok(Object.values(all).every(p => p.length === 3 && p.every(Number.isFinite)));
  }
});

test('eight poses are eight slightly different pictures, and a new seed moves them', () => {
  const poses = poseParams(shots.seed, shots.poses), still = joints(reading, poses[0]).joints;
  assert.equal(poses.length, 8);
  const keys = new Set(poses.map(pose => JSON.stringify(pose)));
  assert.equal(keys.size, 8);
  for (const pose of poses.slice(1)) {
    const j = joints(reading, pose).joints;
    for (const name of ['nose', 'left_wrist', 'right_wrist']) {
      assert.ok(apart(j[name], still[name]) < 0.03, `${name} moves under 3 cm`);
    }
    assert.ok(Math.max(apart(j.nose, still.nose), apart(j.left_wrist, still.left_wrist)) > 0.002, 'and moves');
  }
  // Control: the same seed gives the same poses; another seed does not.
  assert.deepEqual(poseParams(shots.seed, 8), poses);
  const other = poseParams(shots.seed + 1, 8);
  assert.deepEqual(other[0], poses[0]);
  assert.ok(other.slice(1).every((pose, k) => JSON.stringify(pose) !== JSON.stringify(poses[k + 1])));
});

test('the standing figure stands where its scene says', () => {
  const j = joints(standing, poseParams(shots.seed, 1)[0]).joints, [x, y] = standing.at_m;
  assert.ok(near(j.left_ankle, [x + 0.13, y + 0.02, 0.1], 1e-12) && near(j.right_ankle, [x - 0.13, y + 0.02, 0.1], 1e-12));
  assert.ok(j.left_shoulder[2] > 1.3 && j.nose[2] > 1.4, 'upright');
  assert.ok(Math.hypot(x, y) > 1, 'more than a metre from the middle of the couch');
});
