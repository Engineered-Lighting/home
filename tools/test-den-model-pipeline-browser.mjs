// Exercise the experimental model UI explicitly: the ordinary Serve suite keeps
// its production-default pipeline-off coverage. Authored fixtures only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('model evidence and virtual forecast acceptance passes on desktop and phone', { timeout: 150000 }, t => {
  const env = { ...process.env, HOME_DEN_PIPELINE_SUITE: '1' };
  delete env.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, ['--test', '--test-reporter=tap',
    '--test-name-pattern', 'pipeline runs', 'tools/test-den-live-serve-browser.mjs'], {
    cwd: fileURLToPath(new URL('../', import.meta.url)), env,
    encoding: 'utf8', timeout: 145000, maxBuffer: 4 * 1024 * 1024, windowsHide: true,
  });
  const output = `${result.stdout || ''}\n${result.stderr || ''}`;
  assert.ifError(result.error);
  assert.equal(result.signal, null, output);
  assert.equal(result.status, 0, output);
  assert.match(output, /ok \d+ - the pipeline runs through the room:[^\r\n]*(?:\r?\n|$)/);
  assert.match(output, /# pass 1\b/, 'the selected experimental browser case must execute');
  t.diagnostic(output.trim());
});
