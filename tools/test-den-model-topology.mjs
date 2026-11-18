// CPU-only model/producer contracts. No hosts, model packages, weights or GPU.
// A normal workstation never installs anything; configure PYTHON to an existing
// NumPy test environment. Only a qualified GitHub runner gets a private test venv.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SELF = fileURLToPath(import.meta.url);
const ROOT = path.resolve(path.dirname(SELF), '..');
const PREFIX = 'den-model-topology-';
const ownedDirectories = new Set();
export const PRODUCER_TESTS = Object.freeze([
  'test_motion', 'test_motion_native_clock', 'test_model_projection', 'test_source_clock', 'test_forecast_spatial', 'test_specialist_spatial',
  'test_enrichment_link', 'test_enrichment_service', 'test_enrichment_install', 'test_path_replay',
  'test_live.PersonStateLog', 'test_preview_walking',
]);

export function qualifiedRunnerTemp(env = process.env, { platform = process.platform, repo = ROOT } = {}) {
  if (env.GITHUB_ACTIONS !== 'true' || platform !== 'linux' || env.RUNNER_OS !== 'Linux'
      || env.RUNNER_ENVIRONMENT !== 'github-hosted') return null;
  try {
    const candidate = env.RUNNER_TEMP;
    if (typeof candidate !== 'string' || !path.isAbsolute(candidate) || path.resolve(candidate) !== candidate
        || candidate === path.parse(candidate).root || !env.GITHUB_WORKSPACE) return null;
    const temporary = fs.realpathSync(candidate), checkout = fs.realpathSync(repo);
    if (temporary !== candidate || fs.realpathSync(env.GITHUB_WORKSPACE) !== checkout
        || temporary === checkout || temporary.startsWith(checkout + path.sep)) return null;
    const info = fs.lstatSync(candidate);
    if (!info.isDirectory() || info.isSymbolicLink()
        || (typeof process.getuid === 'function' && info.uid !== process.getuid())) return null;
    return temporary;
  } catch { return null; }
}

export function hostedPython312(env = process.env, { platform = process.platform, arch = process.arch, repo = ROOT } = {}) {
  assert.ok(qualifiedRunnerTemp(env, { platform, repo }) && arch === 'x64',
    'Python fixture preparation requires the qualified Linux x64 hosted runner');
  const cache = env.RUNNER_TOOL_CACHE;
  const checkout = fs.realpathSync(repo);
  assert.ok(typeof cache === 'string' && path.isAbsolute(cache) && path.resolve(cache) === cache
    && cache !== path.parse(cache).root && fs.realpathSync(cache) === cache
    && cache !== checkout && !cache.startsWith(checkout + path.sep), 'Invalid hosted Python toolcache scope');
  const versions = fs.readdirSync(path.join(cache, 'Python')).filter(name => /^3\.12\.\d{1,3}$/.test(name));
  assert.ok(versions.length <= 32, 'Unexpected Python 3.12 toolcache size');
  for (const version of versions.sort((a, b) => Number(b.split('.')[2]) - Number(a.split('.')[2]))) {
    const home = path.join(cache, 'Python', version, 'x64');
    const complete = home + '.complete';
    const executable = path.join(home, 'bin', 'python');
    if (!fs.existsSync(complete) || !fs.existsSync(executable)) continue;
    assert.ok(fs.lstatSync(complete).isFile() && !fs.lstatSync(complete).isSymbolicLink(), 'Incomplete Python toolcache marker');
    assert.equal(fs.realpathSync(home), home, 'Python toolcache home escaped its declared scope');
    const resolved = fs.realpathSync(executable);
    assert.ok(resolved.startsWith(home + path.sep) && fs.statSync(resolved).isFile(), 'Python executable escaped its toolcache home');
    fs.accessSync(resolved, fs.constants.X_OK);
    return resolved;
  }
  throw new Error('Preinstalled CPython 3.12 x64 is required in RUNNER_TOOL_CACHE; refusing another version or a download');
}

export function verifyPython312(details, hosted) {
  assert.ok(details && details.implementation === 'cpython' && Array.isArray(details.version)
    && details.version[0] === 3 && details.version[1] === 12,
  'The hash-pinned fixture environment requires CPython 3.12');
  if (hosted) assert.ok(details.platform === 'linux' && details.machine === 'x86_64',
    'The hash-pinned hosted fixture environment requires Linux x86_64');
}

export function createEnvironmentDirectory(verifiedTemp) {
  const base = fs.realpathSync(verifiedTemp);
  if (base !== verifiedTemp || !fs.statSync(base).isDirectory()) throw new Error('CI temporary root changed');
  const directory = fs.mkdtempSync(path.join(base, PREFIX));
  fs.chmodSync(directory, 0o700);
  ownedDirectories.add(directory);
  return directory;
}

export function cleanupEnvironmentDirectory(verifiedTemp, directory) {
  if (!ownedDirectories.has(directory) || path.dirname(directory) !== verifiedTemp
      || !path.basename(directory).startsWith(PREFIX) || fs.realpathSync(verifiedTemp) !== verifiedTemp) {
    throw new Error('Refusing cleanup outside this guard’s owned CI temporary directory');
  }
  if (fs.existsSync(directory)) {
    const info = fs.lstatSync(directory);
    if (!info.isDirectory() || info.isSymbolicLink() || fs.realpathSync(directory) !== directory) {
      throw new Error('Refusing cleanup of a replaced CI temporary directory');
    }
    fs.rmSync(directory, { recursive: true, force: true });
  }
  ownedDirectories.delete(directory);
}

async function command(executable, args, { cwd, env, deadline, label }) {
  const remaining = Math.floor(deadline - Date.now());
  assert.ok(remaining > 0, `${label}: 150-second CPU contract budget exhausted`);
  return await new Promise((resolve, reject) => {
    let stdout = '', stderr = '', bytes = 0, timedOut = false, overflow = false;
    const child = spawn(executable, args, { cwd, env, windowsHide: true, shell: false,
      detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
    const terminate = () => {
      try {
        if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch { /* close/error proves the result; never substitute a pass */ }
    };
    const timer = setTimeout(() => { timedOut = true; terminate(); }, remaining);
    for (const [stream, kind] of [[child.stdout, 'stdout'], [child.stderr, 'stderr']]) {
      stream.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 4 * 1024 * 1024) { overflow = true; terminate(); return; }
        if (kind === 'stdout') stdout += chunk.toString(); else stderr += chunk.toString();
      });
    }
    child.once('error', error => { clearTimeout(timer); reject(new Error(`${label}: ${error.code || error.name}; configure PYTHON to a Python 3.12 test interpreter`)); });
    child.once('close', (status, signal) => {
      clearTimeout(timer);
      if (timedOut || overflow) reject(new Error(`${label}: ${timedOut ? '150-second deadline exceeded' : 'test output exceeded bound'}`));
      else resolve({ status, signal, output: `${stdout}\n${stderr}` });
    });
  });
}

function assertSuite(result, label) {
  assert.equal(result.signal, null, `${label}\n${result.output}`);
  assert.equal(result.status, 0, `${label}\n${result.output}`);
  assert.match(result.output, /\bRan [1-9]\d* tests? in /, `${label}: no tests executed`);
  assert.match(result.output, /\nOK(?: \(skipped=\d+\))?\s*$/, `${label}\n${result.output}`);
}

export async function runTopology({ env = process.env, diagnostic = () => {} } = {}) {
  const deadline = Date.now() + 150000;
  const temporary = qualifiedRunnerTemp(env);
  if (env.GITHUB_ACTIONS === 'true' && temporary === null) {
    throw new Error('GitHub runner temporary scope did not qualify; refusing test-package installation');
  }
  const childEnv = { ...env, PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', CUDA_VISIBLE_DEVICES: '' };
  delete childEnv.PYTHONPATH;
  delete childEnv.PYTHONHOME;
  const basePython = temporary === null ? (env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')) : hostedPython312(env);
  let directory = null, workerPython = basePython;
  try {
    const identified = await command(basePython, ['-I', '-S', '-B', '-c',
      'import json,platform,sys; print(json.dumps(dict(implementation=sys.implementation.name,version=list(sys.version_info[:2]),platform=sys.platform,machine=platform.machine())))'],
    { cwd: ROOT, env: childEnv, deadline, label: 'Pinned Python interpreter identity' });
    assert.equal(identified.status, 0, identified.output);
    verifyPython312(JSON.parse(identified.output.trim()), temporary !== null);
    diagnostic('Verified CPython 3.12 before preparing the hash-pinned fixture environment.');
    if (temporary !== null) {
      directory = createEnvironmentDirectory(temporary);
      const prepared = await command(basePython, ['-B', '-m', 'venv', directory],
        { cwd: ROOT, env: childEnv, deadline, label: 'Private CI NumPy test environment' });
      assert.equal(prepared.status, 0, prepared.output);
      workerPython = path.join(directory, 'bin', 'python');
      const installed = await command(workerPython, ['-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
        'install', '--no-cache-dir', '--no-deps', '--only-binary=:all:', '--require-hashes',
        '--index-url', 'https://pypi.org/simple', '-r', path.join(ROOT, 'tools/den-models/requirements-test.lock')],
      { cwd: ROOT, env: childEnv, deadline, label: 'Hash-pinned NumPy synthetic fixtures' });
      assert.equal(installed.status, 0, installed.output);
      diagnostic('CI uses a private hash-pinned NumPy-only test venv; no model runtime packages are installed.');
    }
    const numpy = await command(workerPython, ['-I', '-B', '-c', 'import numpy; print(numpy.__version__)'],
      { cwd: ROOT, env: childEnv, deadline, label: 'NumPy fixture dependency' });
    assert.equal(numpy.status, 0, 'NumPy is required for the full synthetic worker mapping tests. '
      + 'Set PYTHON to an existing isolated Python 3.12 environment with NumPy, or prepare one using '
      + 'tools/den-models/requirements-test.lock. This workstation guard never installs packages.\n' + numpy.output);
    const producer = await command(basePython, ['-S', '-B', '-m', 'unittest', '-q', ...PRODUCER_TESTS],
      { cwd: path.join(ROOT, 'tools/den-live'), env: childEnv, deadline, label: 'Producer causal stdlib contracts' });
    assertSuite(producer, 'Producer causal stdlib contracts');
    diagnostic(producer.output.trim());
    const worker = await command(workerPython, ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
      { cwd: path.join(ROOT, 'tools/den-models'), env: childEnv, deadline, label: 'Full synthetic worker contracts' });
    assertSuite(worker, 'Full synthetic worker contracts');
    assert.doesNotMatch(worker.output, /OK \(skipped=/, 'Full worker mapping contracts must execute, not skip');
    diagnostic(worker.output.trim());
    diagnostic('Existing walking guard separately covers walking/intent unit contracts; actual Preview integration also runs here.');
  } finally {
    if (directory !== null) cleanupEnvironmentDirectory(temporary, directory);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === SELF) {
  test('Den model topology CPU contracts pass without inference, hosts or model downloads', { timeout: 155000 }, async t => {
    await runTopology({ diagnostic: message => t.diagnostic(message) });
  });
}
