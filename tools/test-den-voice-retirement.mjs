// Caller/default/recovery contracts use fake HTTP, Docker and GPU probes only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('voice opt-in, startup and recovery preserve the default lighting stack', { timeout: 65000 }, t => {
  const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  for (const [file, expected] of [
    ['stack/services/supervisor/test_retire_voice.py', /Ran [1-9]\d* tests?[\s\S]*\nOK\s*$/],
    ['tools/test-voice-retirement-contracts.py', /Ran [1-9]\d* tests?[\s\S]*\nOK\s*$/],
    ['tools/test-supervisor-service-actions.py', /[1-9]\d* pass \. 0 fail\s*$/],
  ]) {
    const result = spawnSync(python, ['-S', '-B', file], {
      cwd: fileURLToPath(new URL('../', import.meta.url)),
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', CUDA_VISIBLE_DEVICES: '' },
      encoding: 'utf8', timeout: 20000, maxBuffer: 1024 * 1024, windowsHide: true,
    });
    const output = `${result.stdout || ''}\n${result.stderr || ''}`;
    assert.ifError(result.error);
    assert.equal(result.signal, null, output);
    assert.equal(result.status, 0, `${file}\n${output}`);
    assert.match(output, expected, `${file}: its fake-only checks must execute`);
    t.diagnostic(`${file}: passed`);
  }
});
