/* The unchanged desk-preview integration path, fed by a verified camera-session
 * successor: the reviewed fit is pinned to a retired capture epoch and only the
 * successor admits the current one. Every harness step and assertion is the
 * existing test file's own; only the fixture generator it spawns is swapped.
 * Authored inputs only; no host, camera, model, browser pixels or hardware. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire, syncBuiltinESMExports } from 'node:module';

const require = createRequire(import.meta.url), childProcess = require('node:child_process');
const original = childProcess.execFileSync, fixtures = [];
childProcess.execFileSync = (file, args, options) => {
  if (!Array.isArray(args) || !args.some(arg => /[\\/]generate-apartment-desk-preview\.py$/.test(arg)))
    return original(file, args, options);
  const out = original(file, args.map(arg => arg.replace(/(generate-apartment-desk-preview)\.py$/, '$1-successor.py')), options);
  fixtures.push(JSON.parse(out));
  return out;
};
syncBuiltinESMExports();
await import('./test-apartment-desk-preview-integration.mjs');

test('the integration fixtures above came from the verified successor, not a same-epoch alignment', () => {
  assert.ok(fixtures.length >= 1, 'the existing integration path spawned the successor fixture');
  for (const data of fixtures) {
    const arm = data.live.arms.B, session = data.view_alignment_camera_session;
    assert.equal(arm.virtual_target_preview.available, true);
    assert.equal(arm.approximate_room_pose.renderable, true);
    assert.deepEqual(arm.approximate_room_pose.approximate_pose.registration.camera_session, session);
    assert.equal(session.status, 'verified_session_successor');
    assert.equal(session.verified_epoch, arm.observation.capture_epoch);
    assert.notEqual(session.reviewed_capture_epoch, session.verified_epoch);
    assert.equal(arm.approximate_room_pose.approximate_pose.registration.source_fit_status, 'refused');
  }
});
