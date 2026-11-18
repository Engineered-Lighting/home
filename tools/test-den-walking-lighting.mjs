import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('walking lighting and optional intent contracts pass without site packages or an actuator', { timeout: 35000 }, t => {
  const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const result = spawnSync(python, ['-S', '-B', '-m', 'unittest', '-q',
    'test_walking', 'test_preview_walking', 'test_walking_intent', 'test_intent', 'test_walking_intent_receipts'], {
    cwd: fileURLToPath(new URL('./den-live/', import.meta.url)),
    encoding: 'utf8', timeout: 30000, windowsHide: true,
  });
  const output = `${result.stdout || ''}\n${result.stderr || ''}`;
  assert.ifError(result.error);
  assert.equal(result.signal, null, output);
  assert.equal(result.status, 0, output);
  assert.match(output, /\bRan [1-9]\d* tests? in /, 'the Python contracts actually execute');
  assert.match(output, /\nOK\s*$/, output);
  t.diagnostic(output.trim());
});
